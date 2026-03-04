"""
AI Agent：接收任务，循环执行"截图 -> AI 分析 -> 下发指令 -> 等待结果"
"""

import asyncio
import json
import logging
import re
from typing import Optional, Callable, Awaitable

from openai import AsyncOpenAI

from config import config
from protocol import (
    ServerMessage, ScreenshotRequest, TapCommand, SwipeCommand,
    InputCommand, BackCommand, HomeCommand, LaunchAppCommand,
    TaskStarted, TaskCompleted, TaskFailed,
    ClientMessage, ScreenshotResult, ActionResult,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个手机操作助手。你能看到手机屏幕截图，并根据用户的任务目标决定下一步操作。

你可以执行以下操作（每次只能执行一个）：
- tap(x, y) — 点击屏幕坐标
- swipe(x1, y1, x2, y2) — 从 (x1,y1) 滑动到 (x2,y2)
- input("文本") — 在当前输入框中输入文本
- back() — 按返回键
- home() — 按 Home 键
- launch("包名") — 启动应用
- done("总结") — 任务完成，附带总结
- fail("原因") — 任务无法完成，附带原因

回复格式必须是一个 JSON 对象，例如：
{"action": "tap", "x": 500, "y": 800}
{"action": "swipe", "x1": 540, "y1": 1500, "x2": 540, "y2": 500}
{"action": "input", "text": "你好"}
{"action": "back"}
{"action": "home"}
{"action": "launch", "package_name": "com.tencent.mm"}
{"action": "done", "summary": "已成功打开微信并发送消息"}
{"action": "fail", "reason": "找不到目标应用"}

重要规则：
1. 仔细观察截图，确认当前屏幕状态
2. 每次只执行一个操作
3. 操作后等待截图确认结果再决定下一步
4. 如果操作没有效果，尝试其他方式
5. 只回复 JSON，不要添加其他文字
"""


class PhoneAgent:
    def __init__(self):
        if not config.zhipu_api_key:
            raise ValueError("ZHIPU_API_KEY 未配置")
        self.client = AsyncOpenAI(
            api_key=config.zhipu_api_key,
            base_url=config.zhipu_base_url,
        )
        self.model = config.zhipu_model

    async def run_task(
        self,
        task: str,
        task_id: str,
        send_to_device: Callable[[ServerMessage], Awaitable[None]],
        wait_for_response: Callable[[str], Awaitable[Optional[ClientMessage]]],
    ) -> None:
        """
        执行一个完整的 Agent 任务循环。

        send_to_device: 向手机发送指令
        wait_for_response: 等待手机返回指定 request_id 的响应
        """
        await send_to_device(TaskStarted(task_id=task_id, task=task))

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"任务：{task}\n\n请先截图查看当前屏幕状态。"},
        ]

        for step in range(config.max_agent_steps):
            logger.info(f"[{task_id}] Step {step + 1}/{config.max_agent_steps}")

            screenshot_req = ScreenshotRequest()
            await send_to_device(screenshot_req)

            screenshot_resp = await wait_for_response(screenshot_req.request_id)
            if screenshot_resp is None:
                await send_to_device(TaskFailed(task_id=task_id, reason="等待截图超时"))
                return
            if not isinstance(screenshot_resp, ScreenshotResult) or not screenshot_resp.success:
                error = getattr(screenshot_resp, "error", "截图失败")
                await send_to_device(TaskFailed(task_id=task_id, reason=f"截图失败: {error}"))
                return

            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "当前屏幕截图："},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_resp.image}"
                        },
                    },
                ],
            })

            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=512,
                    temperature=0.1,
                )
                ai_response = completion.choices[0].message.content.strip()
                logger.info(f"[{task_id}] AI response: {ai_response}")
            except Exception as e:
                logger.error(f"[{task_id}] AI API error: {e}")
                await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e}"))
                return

            messages.append({"role": "assistant", "content": ai_response})

            action = self._parse_action(ai_response)
            if action is None:
                messages.append({
                    "role": "user",
                    "content": "无法解析你的回复，请严格按照 JSON 格式回复。",
                })
                continue

            action_type = action.get("action")

            if action_type == "done":
                summary = action.get("summary", "任务完成")
                await send_to_device(TaskCompleted(task_id=task_id, summary=summary))
                return

            if action_type == "fail":
                reason = action.get("reason", "任务失败")
                await send_to_device(TaskFailed(task_id=task_id, reason=reason))
                return

            command = self._build_command(action)
            if command is None:
                messages.append({
                    "role": "user",
                    "content": f"未知操作类型: {action_type}",
                })
                continue

            await send_to_device(command)

            action_resp = await wait_for_response(command.request_id)
            if action_resp is None:
                await send_to_device(TaskFailed(task_id=task_id, reason="等待操作结果超时"))
                return

            success = getattr(action_resp, "success", False)
            if success:
                messages.append({
                    "role": "user",
                    "content": "操作已执行成功，请截图查看结果。",
                })
            else:
                error = getattr(action_resp, "error", "未知错误")
                messages.append({
                    "role": "user",
                    "content": f"操作执行失败: {error}，请尝试其他方式。",
                })

            await asyncio.sleep(1)

        await send_to_device(TaskFailed(
            task_id=task_id,
            reason=f"超过最大步数限制 ({config.max_agent_steps})"
        ))

    def _parse_action(self, text: str) -> Optional[dict]:
        json_match = re.search(r'\{[^{}]+\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _build_command(self, action: dict) -> Optional[ServerMessage]:
        action_type = action.get("action")
        if action_type == "tap":
            return TapCommand(x=int(action["x"]), y=int(action["y"]))
        elif action_type == "swipe":
            return SwipeCommand(
                x1=int(action["x1"]), y1=int(action["y1"]),
                x2=int(action["x2"]), y2=int(action["y2"]),
                duration=int(action.get("duration", 300)),
            )
        elif action_type == "input":
            return InputCommand(text=action["text"])
        elif action_type == "back":
            return BackCommand()
        elif action_type == "home":
            return HomeCommand()
        elif action_type == "launch":
            return LaunchAppCommand(package_name=action["package_name"])
        return None
