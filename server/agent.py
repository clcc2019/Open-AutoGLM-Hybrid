"""
AI Agent：适配 autoglm-phone 模型的输出格式。

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

        messages = [
            {"role": "user", "content": task},
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

            if step == 0:
                messages[-1] = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": task},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_resp.image}"
                            },
                        },
                    ],
                }
            else:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_resp.image}"}},
                    ],
                })

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
                await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e}"))
                return

            messages.append({"role": "assistant", "content": ai_response})

            answer = self._extract_answer(ai_response)
            if answer is None:
                logger.warning(f"[{task_id}] 无法从 AI 回复中提取 answer")
                messages.append({"role": "user", "content": "无法解析你的回复，请使用标准 <answer> 格式。"})
                continue

            action_name, params = self._parse_do(answer)
            if action_name is None:
                logger.warning(f"[{task_id}] 无法解析 do(): {answer}")
                continue

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
            if not success:
                error = getattr(action_resp, "error", "")
                logger.warning(f"[{task_id}] 操作失败: {error}")

            await asyncio.sleep(1.5)

        await send_to_device(TaskFailed(
            task_id=task_id,
            reason=f"超过最大步数限制 ({config.max_agent_steps})"
        ))

    # Action names the model may emit directly (without do() wrapper)
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

        # 4) Bare action calls: Launch("微信"), Tap([500, 800]), Back(), etc.
        #    Search from the END of the text so we get the last (most recent) action
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
        支持:
          do(action="Tap", element=[500, 800])
          Launch("微信")
          Tap([500, 800])
          Back()
          finished(content="...")
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
        """
        解析裸 action 调用的参数。
        Launch("微信") → {"app": "微信"}
        Tap([500, 800]) → {"element": [500, 800]}
        Type("你好") → {"text": "你好"}
        Swipe([540, 1200], "up") → {"element": [540, 1200], "direction": "up"}
        Back() → {}
        """
        if not args_str:
            return {}

        # 如果有 kwargs 格式，优先用 kwargs 解析
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
            parts = []
            element = []
            direction = "up"
            dist = "medium"
            # Extract [x, y]
            m = re.search(r'\[([^\]]+)\]', args_str)
            if m:
                try:
                    element = [int(x.strip()) for x in m.group(1).split(',')]
                except ValueError:
                    pass
            # Extract quoted strings for direction/dist
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
        """解析 Python 风格的 kwargs 字符串: action="Tap", element=[500, 800]"""
        result = {}
        # 匹配 key=value 对
        # value 可以是: "string", [list], number
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
