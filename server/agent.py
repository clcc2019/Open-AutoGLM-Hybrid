"""
AI Agent：严格参照 Open-AutoGLM 原版实现。

关键设计（与 Open-AutoGLM 一致）：
  1. 模型输出 0-999 相对坐标，服务端按屏幕尺寸转换为像素坐标
  2. 截图使用 PNG 格式，不做缩放
  3. 每步只保留当前截图，执行后立即移除图片只保留文本
  4. 使用 screen_info 传递当前应用等上下文信息
  5. System prompt 包含完整的操作说明和规则

autoglm-phone 模型输出格式:
  <think>思考过程</think>
  do(action="Tap", element=[500, 800])
  或
  finish(message="任务完成总结")
"""

import ast
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional, Callable, Awaitable

from openai import AsyncOpenAI

from config import config
from protocol import (
    ServerMessage, ScreenshotRequest, TapCommand, SwipeCommand,
    InputCommand, BackCommand, HomeCommand, LaunchAppCommand,
    LongPressCommand, DoubleTapCommand,
    TaskStarted, TaskCompleted, TaskFailed,
    ClientMessage, ScreenshotResult, ActionResult,
)

logger = logging.getLogger(__name__)


def _build_system_prompt() -> str:
    """参照 Open-AutoGLM phone_agent/config/prompts_zh.py 构建 system prompt"""
    now = datetime.now()
    weekday_map = {
        0: "星期一", 1: "星期二", 2: "星期三",
        3: "星期四", 4: "星期五", 5: "星期六", 6: "星期日",
    }
    date_str = f"{now.year}年{now.month:02d}月{now.day:02d}日 {weekday_map[now.weekday()]}"

    app_list = ", ".join(f'"{name}"' for name in APP_PACKAGES.keys())

    return f"""今天的日期是: {date_str}
你是一个智能体分析专家，可以根据操作历史和当前状态图执行一系列操作来完成任务。
你必须严格按照要求输出以下格式：
思考过程
操作指令

## 可用操作指令

1. Launch: 启动应用
   do(action="Launch", app="应用名")
   支持的应用: [{app_list}]

2. Tap: 点击屏幕坐标（坐标范围 0-999，相对坐标）
   do(action="Tap", element=[x, y])

3. Type: 输入文本（需要先点击输入框）
   do(action="Type", text="要输入的文本")

4. Swipe: 滑动屏幕（坐标范围 0-999，相对坐标）
   do(action="Swipe", start=[x1, y1], end=[x2, y2])
   向上滑动示例: do(action="Swipe", start=[500, 800], end=[500, 200])
   向下滑动示例: do(action="Swipe", start=[500, 200], end=[500, 800])

5. Back: 返回上一页
   do(action="Back")

6. Home: 返回桌面
   do(action="Home")

7. Long Press: 长按
   do(action="Long Press", element=[x, y])

8. Double Tap: 双击
   do(action="Double Tap", element=[x, y])

9. Wait: 等待页面加载
   do(action="Wait")

10. Take_over: 需要人工接管（登录、验证码等）
    do(action="Take_over", message="原因说明")

11. finish: 任务完成
    finish(message="任务完成的总结说明")

## 执行规则

1. 每次只输出一个操作指令
2. 坐标使用 0-999 的相对坐标系，(0,0) 为左上角，(999,999) 为右下角
3. 先检查当前所在的 app，如果不是目标 app，先用 Launch 切换
4. 遇到不相关的弹窗或页面，先用 Back() 返回
5. 如果页面还在加载中，使用 Wait 等待，但最多连续 Wait 三次
6. 找不到目标内容时，可以使用 Swipe 滚动查找
7. 操作前检查上一步操作是否生效，如果没有生效则调整策略
8. 滑动无效时，调整起点位置、滑动距离或反向滑动
9. 不要连续执行相同的失败操作超过 2 次
10. 任务完成后必须调用 finish()
11. 如果遇到网络问题，尝试点击"重新加载"或类似按钮"""


def _convert_relative_to_absolute(element: list, screen_width: int, screen_height: int) -> tuple:
    """
    将模型输出的 0-999 相对坐标转换为实际像素坐标。
    与 Open-AutoGLM phone_agent/actions/handler.py 中的实现一致。
    """
    x = int(element[0] / 1000 * screen_width)
    y = int(element[1] / 1000 * screen_height)
    return x, y


class PhoneAgent:
    def __init__(self):
        if not config.zhipu_api_key:
            raise ValueError("ZHIPU_API_KEY 未配置")
        self.client = AsyncOpenAI(
            api_key=config.zhipu_api_key,
            base_url=config.zhipu_base_url,
        )
        self.model = config.zhipu_model
        self.screen_width = 0
        self.screen_height = 0

    async def run_task(
        self,
        task: str,
        task_id: str,
        send_to_device: Callable[[ServerMessage], Awaitable[None]],
        wait_for_response: Callable[[str], Awaitable[Optional[ClientMessage]]],
    ) -> None:
        await send_to_device(TaskStarted(task_id=task_id, task=task))

        system_prompt = _build_system_prompt()
        context: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]

        consecutive_parse_failures = 0
        consecutive_wait = 0

        for step in range(config.max_agent_steps):
            logger.info(f"[{task_id}] Step {step + 1}/{config.max_agent_steps}")

            # --- 1. 请求截图 ---
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

            if screenshot_resp.width > 0 and screenshot_resp.height > 0:
                self.screen_width = screenshot_resp.width
                self.screen_height = screenshot_resp.height

            # --- 2. 构建 user message（参照 Open-AutoGLM） ---
            screen_info = json.dumps({"screen_width": self.screen_width, "screen_height": self.screen_height}, ensure_ascii=False)

            user_content = []
            if step == 0:
                text_content = f"{task}\n\n** Screen Info **\n{screen_info}"
            else:
                text_content = f"** Screen Info **\n{screen_info}"

            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_resp.image}"
                },
            })
            user_content.append({"type": "text", "text": text_content})

            context.append({"role": "user", "content": user_content})

            # --- 3. 调用模型 ---
            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=context,
                    max_tokens=3000,
                    temperature=0.1,
                    frequency_penalty=0.2,
                )
                ai_response = completion.choices[0].message.content.strip()
                logger.info(f"[{task_id}] AI: {ai_response[:400]}")
            except Exception as e:
                logger.error(f"[{task_id}] AI API error: {e}")
                if "25480" in str(e) or "token" in str(e).lower():
                    self._trim_context(context)
                    try:
                        completion = await self.client.chat.completions.create(
                            model=self.model,
                            messages=context,
                            max_tokens=2000,
                            temperature=0.1,
                            frequency_penalty=0.2,
                        )
                        ai_response = completion.choices[0].message.content.strip()
                        logger.info(f"[{task_id}] AI (retry): {ai_response[:400]}")
                    except Exception as e2:
                        logger.error(f"[{task_id}] AI retry failed: {e2}")
                        await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e2}"))
                        return
                else:
                    await send_to_device(TaskFailed(task_id=task_id, reason=f"AI 调用失败: {e}"))
                    return

            # --- 4. 添加 assistant 消息，然后移除 user 消息中的图片 ---
            context.append({"role": "assistant", "content": ai_response})
            self._remove_images_from_message(context, len(context) - 2)

            # --- 5. 解析 action ---
            action = self._parse_response(ai_response)
            if action is None:
                logger.warning(f"[{task_id}] 无法解析 action")
                consecutive_parse_failures += 1
                if consecutive_parse_failures >= 3:
                    await send_to_device(TaskFailed(task_id=task_id, reason="连续多次无法解析 AI 回复"))
                    return
                context.append({"role": "user", "content": "无法解析你的回复，请严格使用 do(action=\"操作名\", 参数...) 或 finish(message=\"...\") 格式。"})
                continue

            consecutive_parse_failures = 0
            action_name = action.get("action", action.get("_metadata", ""))
            logger.info(f"[{task_id}] Action: {action}")

            # --- 6. 处理特殊 action ---
            if action.get("_metadata") == "finish":
                summary = action.get("message", "任务完成")
                await send_to_device(TaskCompleted(task_id=task_id, summary=summary))
                return

            if action_name == "Take_over":
                msg = action.get("message", "需要人工操作")
                await send_to_device(TaskFailed(task_id=task_id, reason=f"需要人工接管: {msg}"))
                return

            if action_name == "Wait":
                consecutive_wait += 1
                if consecutive_wait > 3:
                    context.append({"role": "user", "content": "已经等待多次，请尝试其他操作。"})
                    consecutive_wait = 0
                else:
                    await asyncio.sleep(2)
                continue
            else:
                consecutive_wait = 0

            # --- 7. 构建并发送命令 ---
            command = self._build_command(action)
            if command is None:
                logger.warning(f"[{task_id}] 无法构建指令: {action}")
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
                context.append({
                    "role": "user",
                    "content": f"操作 {action_name} 执行失败: {error_msg}。请尝试其他方式。"
                })

            # 等待 UI 响应：Launch 需要更长时间，其他操作短等待即可
            if action_name == "Launch":
                await asyncio.sleep(2.0)
            elif action_name == "Type":
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.8)

        await send_to_device(TaskFailed(
            task_id=task_id,
            reason=f"超过最大步数限制 ({config.max_agent_steps})"
        ))

    # ---- Context management (following Open-AutoGLM) ----

    @staticmethod
    def _remove_images_from_message(context: list, index: int) -> None:
        """
        移除指定位置 user 消息中的图片，只保留文本。
        与 Open-AutoGLM MessageBuilder.remove_images_from_message 一致。
        """
        if index < 0 or index >= len(context):
            return
        msg = context[index]
        if isinstance(msg.get("content"), list):
            msg["content"] = [
                item for item in msg["content"]
                if isinstance(item, dict) and item.get("type") == "text"
            ]

    @staticmethod
    def _trim_context(context: list) -> None:
        """Token 溢出时紧急裁剪：保留 system + 首条任务 + 最近几轮。"""
        if len(context) <= 8:
            return
        system = context[0]
        first_user = context[1] if len(context) > 1 else None
        tail = context[-6:]
        context.clear()
        context.append(system)
        if first_user:
            context.append(first_user)
        context.extend(tail)

    # ---- Action parsing (following Open-AutoGLM phone_agent/actions/handler.py) ----

    def _parse_response(self, text: str) -> Optional[dict]:
        """
        解析模型回复，提取 action dict。
        参照 Open-AutoGLM 的 parse_action 逻辑。
        """
        # 提取 action 部分（去掉 thinking）
        action_str = self._extract_action_str(text)
        if action_str is None:
            return None

        # finish(message="...")
        m = re.match(r'finish\s*\((.*)\)', action_str, re.DOTALL)
        if m:
            msg = self._extract_string_param(m.group(1), "message")
            return {"_metadata": "finish", "message": msg or action_str}

        # do(action="...", ...)
        m = re.match(r'do\s*\((.*)\)', action_str, re.DOTALL)
        if m:
            return self._parse_do_kwargs(m.group(1))

        return None

    def _extract_action_str(self, text: str) -> Optional[str]:
        """从模型回复中提取 action 字符串部分。"""
        # 1) <answer>...</answer>
        m = re.search(r'<answer>(.*?)</answer>', text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 2) 查找 finish(...) 或 do(...)
        # 从文本末尾向前搜索，取最后出现的
        finish_matches = list(re.finditer(r'finish\s*\([^)]*\)', text, re.DOTALL))
        if finish_matches:
            return finish_matches[-1].group(0)

        do_matches = list(re.finditer(r'do\s*\(.*?\)', text, re.DOTALL))
        if do_matches:
            return do_matches[-1].group(0)

        # 3) 尝试匹配更宽松的 do(...)，允许多行
        m = re.search(r'do\s*\((.+)\)', text, re.DOTALL)
        if m:
            return m.group(0)

        return None

    def _parse_do_kwargs(self, kwargs_str: str) -> Optional[dict]:
        """
        解析 do() 内的参数。
        参照 Open-AutoGLM 使用 ast.parse 来安全解析 Python 风格的参数。
        """
        try:
            # 尝试用 ast 解析: dict(action="Tap", element=[500, 800])
            tree = ast.parse(f"dict({kwargs_str})", mode="eval")
            call = tree.body
            result = {}
            if isinstance(call, ast.Call):
                for kw in call.keywords:
                    key = kw.arg
                    val = ast.literal_eval(kw.value)
                    result[key] = val
            if "action" in result:
                return result
        except Exception:
            pass

        # Fallback: regex 解析
        result = {}
        # action="..."
        m = re.search(r'action\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["action"] = m.group(1)

        # element=[x, y]
        m = re.search(r'element\s*=\s*\[([^\]]+)\]', kwargs_str)
        if m:
            try:
                result["element"] = [int(x.strip()) for x in m.group(1).split(',')]
            except ValueError:
                pass

        # start=[x, y]
        m = re.search(r'start\s*=\s*\[([^\]]+)\]', kwargs_str)
        if m:
            try:
                result["start"] = [int(x.strip()) for x in m.group(1).split(',')]
            except ValueError:
                pass

        # end=[x, y]
        m = re.search(r'end\s*=\s*\[([^\]]+)\]', kwargs_str)
        if m:
            try:
                result["end"] = [int(x.strip()) for x in m.group(1).split(',')]
            except ValueError:
                pass

        # text="..."
        m = re.search(r'text\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["text"] = m.group(1)

        # app="..."
        m = re.search(r'app\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["app"] = m.group(1)

        # message="..."
        m = re.search(r'message\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["message"] = m.group(1)

        # duration="..."
        m = re.search(r'duration\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["duration"] = m.group(1)

        # direction="..."
        m = re.search(r'direction\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["direction"] = m.group(1)

        # dist="..."
        m = re.search(r'dist\s*=\s*"([^"]*)"', kwargs_str)
        if m:
            result["dist"] = m.group(1)

        if "action" in result:
            return result
        return None

    @staticmethod
    def _extract_string_param(s: str, key: str) -> Optional[str]:
        m = re.search(rf'{key}\s*=\s*"([^"]*)"', s)
        if m:
            return m.group(1)
        # 如果只有一个字符串参数
        m = re.search(r'"([^"]*)"', s)
        if m:
            return m.group(1)
        return s.strip().strip('"').strip("'") if s.strip() else None

    def _build_command(self, action: dict) -> Optional[ServerMessage]:
        """
        将解析后的 action dict 转换为服务器命令。
        关键：将 0-999 相对坐标转换为实际像素坐标。
        """
        action_name = action.get("action", "")
        sw = self.screen_width or 1080
        sh = self.screen_height or 2400

        if action_name == "Tap":
            element = action.get("element", [])
            if len(element) >= 2:
                x, y = _convert_relative_to_absolute(element, sw, sh)
                logger.info(f"  Tap: relative={element} -> pixel=({x}, {y}) screen={sw}x{sh}")
                return TapCommand(x=x, y=y)

        elif action_name == "Launch":
            app = action.get("app", "")
            if app:
                pkg = APP_PACKAGES.get(app, "")
                return LaunchAppCommand(package_name=pkg, app_name=app)

        elif action_name == "Type":
            text = action.get("text", "")
            if text:
                return InputCommand(text=text)

        elif action_name == "Swipe":
            # Open-AutoGLM 使用 start=[x1,y1], end=[x2,y2] 格式
            start = action.get("start", action.get("element", []))
            end = action.get("end", [])

            if len(start) >= 2 and len(end) >= 2:
                x1, y1 = _convert_relative_to_absolute(start, sw, sh)
                x2, y2 = _convert_relative_to_absolute(end, sw, sh)
            elif len(start) >= 2:
                x1, y1 = _convert_relative_to_absolute(start, sw, sh)
                direction = action.get("direction", "up")
                dist = action.get("dist", "medium")
                dist_px = {"short": 200, "medium": 500, "long": 800}.get(dist, 500)
                x2, y2 = x1, y1
                if direction == "up": y2 = max(0, y1 - dist_px)
                elif direction == "down": y2 = min(sh, y1 + dist_px)
                elif direction == "left": x2 = max(0, x1 - dist_px)
                elif direction == "right": x2 = min(sw, x1 + dist_px)
            else:
                x1, y1 = sw // 2, sh * 3 // 4
                x2, y2 = sw // 2, sh // 4

            # 计算滑动时长（参照 Open-AutoGLM）
            dist_sq = (x1 - x2) ** 2 + (y1 - y2) ** 2
            duration_ms = max(500, min(int(dist_sq / 1000), 2000))

            logger.info(f"  Swipe: ({x1},{y1})->({x2},{y2}) duration={duration_ms}ms screen={sw}x{sh}")
            return SwipeCommand(x1=x1, y1=y1, x2=x2, y2=y2, duration=duration_ms)

        elif action_name == "Back":
            return BackCommand()

        elif action_name == "Home":
            return HomeCommand()

        elif action_name in ("Long Press", "Long_Press", "LongPress"):
            element = action.get("element", [])
            if len(element) >= 2:
                x, y = _convert_relative_to_absolute(element, sw, sh)
                return LongPressCommand(x=x, y=y)

        elif action_name in ("Double Tap", "DoubleTap", "Double_Tap"):
            element = action.get("element", [])
            if len(element) >= 2:
                x, y = _convert_relative_to_absolute(element, sw, sh)
                return DoubleTapCommand(x=x, y=y)

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
