"""
AI Agent：适配 autoglm-phone 模型的输出格式。

参照 Open-AutoGLM 原版实现：
  - 使用 system prompt 规范输出格式
  - 每步只保留当前截图，执行后立即移除图片只保留文本
  - 将操作结果反馈给模型

autoglm-phone 模型输出格式:
  <think>思考过程</think>
  <answer>do(action="Tap", element=[500, 800])</answer>

支持的 action:
  Launch(app="微信")
  Tap(element=[x, y])
  Type(text="你好")
  Swipe(element=[x1, y1], direction="up"/"down"/"left"/"right", dist="medium")
  Back()
  Home()
  Long Press(element=[x, y])
  Wait()
  Take_over(message="请手动操作")
  finished(content="任务完成总结")
"""

import asyncio
import logging
import re
from datetime import datetime
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

SWIPE_DIST = {"short": 200, "medium": 500, "long": 800}


def _build_system_prompt() -> str:
    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日 %A")
    weekday_map = {
        "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
        "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六",
        "Sunday": "星期日",
    }
    for en, zh in weekday_map.items():
        date_str = date_str.replace(en, zh)

    app_list = "、".join(APP_PACKAGES.keys())

    return f"""今天的日期是: {date_str}
你是一个手机操作智能体，根据用户任务和当前屏幕截图执行操作。

## 输出格式
你必须严格按照以下格式输出：
<answer>do(action="操作名", 参数...)</answer>

或直接使用简写格式：
<answer>Launch("微信")</answer>
<answer>Tap(element=[500, 800])</answer>

## 可用操作
- Launch(app="应用名"): 启动应用。支持的应用: {app_list}
- Tap(element=[x, y]): 点击坐标
- Type(text="文本"): 输入文本
- Swipe(element=[x, y], direction="up"/"down"/"left"/"right", dist="short"/"medium"/"long"): 滑动
- Back(): 返回上一页
- Home(): 返回桌面
- Long Press(element=[x, y]): 长按
- Double Tap(element=[x, y]): 双击
- Wait(): 等待页面加载（最多3秒）
- Take_over(message="原因"): 需要人工接管（登录/验证码等）
- finished(content="任务完成总结"): 任务完成时调用

## 执行规则
1. 每次只输出一个操作
2. 先观察当前屏幕，判断当前状态，再决定下一步操作
3. 如果需要打开某个应用，使用 Launch 操作
4. 遇到不相关的弹窗或页面，先用 Back() 返回
5. 如果操作失败，尝试其他方式完成任务
6. 任务完成后必须调用 finished()
7. 不要连续执行相同的失败操作超过2次
8. 如果当前不在目标应用中，先用 Launch 切换到目标应用"""


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
        await send_to_device(TaskStarted(task_id=task_id, task=task))

        system_prompt = _build_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        consecutive_failures = 0
        last_action_name = None

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

            user_content = []
            if step == 0:
                user_content.append({"type": "text", "text": task})
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_resp.image}"
                },
            })
            messages.append({"role": "user", "content": user_content})

            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=3000,
                    temperature=0.1,
                    frequency_penalty=0.2,
                )
                ai_response = completion.choices[0].message.content.strip()
                logger.info(f"[{task_id}] AI: {ai_response[:300]}")
            except Exception as e:
                logger.error(f"[{task_id}] AI API error: {e}")
                # Token overflow — trim oldest messages and retry once
                if "25480" in str(e) or "token" in str(e).lower():
                    self._trim_context(messages)
                    try:
                        completion = await self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            max_tokens=2000,
                            temperature=0.1,
                            frequency_penalty=0.2,
                        )
                        ai_response = completion.choices[0].message.content.strip()
                        logger.info(f"[{task_id}] AI (retry): {ai_response[:300]}")
                    except Exception as e2:
                        logger.error(f"[{task_id}] AI API retry failed: {e2}")
                        await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e2}"))
                        return
                else:
                    await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e}"))
                    return

            messages.append({"role": "assistant", "content": ai_response})

            # Key optimization: remove screenshot from the user message we just sent.
            # This follows Open-AutoGLM's approach — only the current step's screenshot
            # is visible to the model; after getting the response, strip it to save tokens.
            self._remove_images_from_last_user(messages)

            answer = self._extract_answer(ai_response)
            if answer is None:
                logger.warning(f"[{task_id}] 无法从 AI 回复中提取 answer")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    await send_to_device(TaskFailed(task_id=task_id, reason="连续多次无法解析 AI 回复"))
                    return
                messages.append({"role": "user", "content": "无法解析你的回复，请严格使用 <answer>操作</answer> 格式输出一个操作。"})
                continue

            action_name, params = self._parse_do(answer)
            if action_name is None:
                logger.warning(f"[{task_id}] 无法解析 action: {answer}")
                consecutive_failures += 1
                continue

            consecutive_failures = 0
            logger.info(f"[{task_id}] Action: {action_name} {params}")

            if action_name == "finished":
                summary = params.get("content", "任务完成")
                await send_to_device(TaskCompleted(task_id=task_id, summary=summary))
                return

            if action_name == "Take_over":
                msg = params.get("message", "需要人工操作")
                logger.warning(f"[{task_id}] 需要人工接管: {msg}")
                await send_to_device(TaskFailed(task_id=task_id, reason=f"需要人工接管: {msg}"))
                return

            if action_name == "Wait":
                await asyncio.sleep(3)
                last_action_name = "Wait"
                continue

            command = self._build_command(action_name, params)
            if command is None:
                logger.warning(f"[{task_id}] 无法构建指令: {action_name}")
                continue

            await send_to_device(command)

            action_resp = await wait_for_response(command.request_id)
            if action_resp is None:
                await send_to_device(TaskFailed(task_id=task_id, reason="等待操作结果超时"))
                return

            success = getattr(action_resp, "success", False)
            error_msg = getattr(action_resp, "error", "")

            if not success:
                logger.warning(f"[{task_id}] 操作失败: {error_msg}")
                messages.append({
                    "role": "user",
                    "content": f"操作 {action_name} 执行失败: {error_msg}。请尝试其他方式。"
                })
            last_action_name = action_name

            await asyncio.sleep(1.5)

        await send_to_device(TaskFailed(
            task_id=task_id,
            reason=f"超过最大步数限制 ({config.max_agent_steps})"
        ))

    @staticmethod
    def _remove_images_from_last_user(messages: list) -> None:
        """Find the last user message and strip image_url parts, keeping only text."""
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                text_parts = [p for p in msg["content"] if isinstance(p, dict) and p.get("type") == "text"]
                if text_parts:
                    messages[i] = {"role": "user", "content": text_parts}
                else:
                    messages[i] = {"role": "user", "content": "[截图]"}
                break

    @staticmethod
    def _trim_context(messages: list) -> None:
        """
        Emergency trim when approaching token limit.
        Keep system prompt (index 0), the first user task message, and the last 6 messages.
        """
        if len(messages) <= 8:
            return
        system = messages[0]
        first_user = messages[1] if len(messages) > 1 else None
        tail = messages[-6:]
        messages.clear()
        messages.append(system)
        if first_user:
            messages.append(first_user)
        messages.extend(tail)

    _KNOWN_ACTIONS = {
        "Launch", "Tap", "Type", "Swipe", "Back", "Home",
        "Long Press", "Long_Press", "LongPress",
        "Double Tap", "DoubleTap", "Wait", "Take_over",
        "finished",
    }

    def _extract_answer(self, text: str) -> Optional[str]:
        """
        从模型回复中提取可执行的 action 字符串。
        支持多种格式:
          1. <answer>do(action="Tap", element=[500, 800])</answer>
          2. do(action="Tap", element=[500, 800])
          3. Launch("微信")  /  Tap([500, 800])  /  Back()
          4. finished(content="...")
        """
        # 1) <answer>...</answer>
        m = re.search(r'<answer>(.*?)</answer>', text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 2) do(...)
        m = re.search(r'do\(.*\)', text)
        if m:
            return m.group(0)

        # 3) finished(...)
        m = re.search(r'finished\(.*\)', text)
        if m:
            return m.group(0)

        # 4) Bare action calls — search from the END so we get the last (most recent) action
        for action in self._KNOWN_ACTIONS:
            escaped = re.escape(action)
            pattern = rf'{escaped}\s*\(.*?\)'
            matches = list(re.finditer(pattern, text, re.DOTALL))
            if matches:
                return matches[-1].group(0)

        return None

    def _parse_do(self, answer: str) -> tuple:
        """
        解析多种 action 格式，返回 (action_name, params_dict)。
        """
        # finished(content="...")
        m = re.match(r'finished\((.*)\)', answer, re.DOTALL)
        if m:
            params = self._parse_kwargs(m.group(1))
            if not params.get("content"):
                inner = m.group(1).strip().strip('"').strip("'")
                if inner:
                    params["content"] = inner
            return ("finished", params)

        # do(action="...", ...)
        m = re.match(r'do\((.*)\)', answer, re.DOTALL)
        if m:
            kwargs_str = m.group(1)
            params = self._parse_kwargs(kwargs_str)
            action_name = params.pop("action", None)
            if action_name:
                return (action_name, params)

        # Bare action: ActionName(args)
        for action in self._KNOWN_ACTIONS:
            escaped = re.escape(action)
            m = re.match(rf'{escaped}\s*\((.*)\)', answer, re.DOTALL)
            if m:
                args_str = m.group(1).strip()
                params = self._parse_bare_args(action, args_str)
                return (action, params)

        return (None, {})

    def _parse_bare_args(self, action: str, args_str: str) -> dict:
        if not args_str:
            return {}

        if '=' in args_str:
            return self._parse_kwargs(args_str)

        if action == "Launch":
            app = args_str.strip().strip('"').strip("'")
            return {"app": app}

        if action == "Type":
            text = args_str.strip().strip('"').strip("'")
            return {"text": text}

        if action == "Tap" or action in ("Long Press", "Long_Press", "LongPress",
                                          "Double Tap", "DoubleTap"):
            m = re.search(r'\[([^\]]+)\]', args_str)
            if m:
                try:
                    nums = [int(x.strip()) for x in m.group(1).split(',')]
                    return {"element": nums}
                except ValueError:
                    pass
            return {}

        if action == "Swipe":
            element = []
            direction = "up"
            dist = "medium"
            m = re.search(r'\[([^\]]+)\]', args_str)
            if m:
                try:
                    element = [int(x.strip()) for x in m.group(1).split(',')]
                except ValueError:
                    pass
            strings = re.findall(r'"([^"]*)"', args_str)
            if not strings:
                strings = re.findall(r"'([^']*)'", args_str)
            for s in strings:
                if s in ("up", "down", "left", "right"):
                    direction = s
                elif s in ("short", "medium", "long"):
                    dist = s
            return {"element": element, "direction": direction, "dist": dist}

        if action == "Take_over":
            msg = args_str.strip().strip('"').strip("'")
            return {"message": msg}

        return {}

    def _parse_kwargs(self, s: str) -> dict:
        result = {}
        pattern = r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\[.*?\]|\d+(?:\.\d+)?)'
        for m in re.finditer(pattern, s):
            key = m.group(1)
            val_str = m.group(2)
            if val_str.startswith('"') and val_str.endswith('"'):
                result[key] = val_str[1:-1]
            elif val_str.startswith('['):
                try:
                    nums = [int(x.strip()) for x in val_str[1:-1].split(',') if x.strip()]
                    result[key] = nums
                except ValueError:
                    result[key] = val_str
            else:
                try:
                    result[key] = int(val_str)
                except ValueError:
                    try:
                        result[key] = float(val_str)
                    except ValueError:
                        result[key] = val_str
        return result

    def _build_command(self, action_name: str, params: dict) -> Optional[ServerMessage]:
        if action_name == "Tap":
            element = params.get("element", [])
            if len(element) >= 2:
                return TapCommand(x=int(element[0]), y=int(element[1]))

        elif action_name == "Launch":
            app = params.get("app", "")
            if app:
                pkg = APP_PACKAGES.get(app, "")
                return LaunchAppCommand(package_name=pkg, app_name=app)

        elif action_name == "Type":
            text = params.get("text", "")
            if text:
                return InputCommand(text=text)

        elif action_name == "Swipe":
            element = params.get("element", [])
            direction = params.get("direction", "up")
            dist_name = params.get("dist", "medium")
            dist_px = SWIPE_DIST.get(dist_name, 500)

            if len(element) >= 2:
                x, y = int(element[0]), int(element[1])
            else:
                x, y = 540, 1200

            dx, dy = 0, 0
            if direction == "up":
                dy = -dist_px
            elif direction == "down":
                dy = dist_px
            elif direction == "left":
                dx = -dist_px
            elif direction == "right":
                dx = dist_px

            return SwipeCommand(
                x1=x, y1=y,
                x2=max(0, x + dx), y2=max(0, y + dy),
                duration=300,
            )

        elif action_name == "Back":
            return BackCommand()

        elif action_name == "Home":
            return HomeCommand()

        elif action_name in ("Long Press", "Long_Press", "LongPress"):
            element = params.get("element", [])
            if len(element) >= 2:
                return TapCommand(x=int(element[0]), y=int(element[1]))

        elif action_name == "Double Tap" or action_name == "DoubleTap":
            element = params.get("element", [])
            if len(element) >= 2:
                return TapCommand(x=int(element[0]), y=int(element[1]))

        return None


APP_PACKAGES = {
    "微信": "com.tencent.mm",
    "QQ": "com.tencent.mobileqq",
    "微博": "com.sina.weibo",
    "淘宝": "com.taobao.taobao",
    "京东": "com.jingdong.app.mall",
    "拼多多": "com.xunmeng.pinduoduo",
    "美团": "com.sankuai.meituan",
    "饿了么": "me.ele",
    "抖音": "com.ss.android.ugc.aweme",
    "bilibili": "tv.danmaku.bili",
    "小红书": "com.xingin.xhs",
    "知乎": "com.zhihu.android",
    "支付宝": "com.eg.android.AlipayGphone",
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "网易云音乐": "com.netease.cloudmusic",
    "QQ音乐": "com.tencent.qqmusic",
    "携程": "ctrip.android.view",
    "12306": "com.MobileTicket",
    "滴滴出行": "com.sdu.didi.psnger",
    "大众点评": "com.dianping.v1",
    "豆瓣": "com.douban.frodo",
    "今日头条": "com.ss.android.article.news",
    "快手": "com.smile.gifmaker",
    "腾讯视频": "com.tencent.qqlive",
    "爱奇艺": "com.qiyi.video",
    "优酷": "com.youku.phone",
    "闲鱼": "com.taobao.idlefish",
    "得物": "com.shizhuang.duapp",
    "唯品会": "com.achievo.vipshop",
    "肯德基": "com.yek.android.kfc.activitys",
    "喜马拉雅": "com.ximalaya.ting.android",
    "钉钉": "com.alibaba.android.rimet",
    "飞书": "com.ss.android.lark",
    "企业微信": "com.tencent.wework",
    "WPS": "cn.wps.moffice_eng",
    "Chrome": "com.android.chrome",
    "设置": "com.android.settings",
    "相机": "com.android.camera",
    "日历": "com.android.calendar",
    "时钟": "com.android.deskclock",
    "计算器": "com.android.calculator2",
    "文件管理": "com.android.fileexplorer",
}
