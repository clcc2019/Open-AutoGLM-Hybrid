"""Server-side phone controller.

Analyzes screenshots from the phone and generates action commands.
The phone APP polls this service, sends a screenshot, and receives
a list of commands to execute.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

DETECT_MESSAGE_PROMPT = """分析这张手机截图，判断当前是否在聊天界面，是否有新的买家消息需要回复。

请用 JSON 格式返回：
{
    "is_chat_screen": true/false,
    "has_new_message": true/false,
    "buyer_message": "买家最新一条消息的完整文本（如果有）",
    "buyer_name": "买家昵称（如果能识别）",
    "product_name": "正在讨论的商品名称（如果能识别）",
    "input_box_y": 输入框大概的Y坐标（像素值，0表示未识别到）,
    "send_button_x": 发送按钮大概的X坐标（像素值，0表示未识别到）,
    "send_button_y": 发送按钮大概的Y坐标（像素值，0表示未识别到）,
    "screen_width": 屏幕宽度像素估计值,
    "screen_height": 屏幕高度像素估计值
}

规则：
- 如果不是聊天界面，所有字段返回 false/空/0
- buyer_message 只返回买家（对方）的最新一条消息，不要返回自己发的
- 如果最新消息是自己发的（通常在右侧），has_new_message 返回 false
- 尽量识别买家昵称和商品名称
- 只返回 JSON，不要返回其他内容"""


def _get_vision_client() -> OpenAI:
    return OpenAI(
        api_key=settings.effective_vision_api_key,
        base_url=settings.effective_vision_base_url,
    )


def analyze_screenshot(screenshot_base64: str) -> dict:
    """Send screenshot to vision LLM and parse the chat detection result."""
    if not screenshot_base64:
        return {"is_chat_screen": False, "has_new_message": False}

    client = _get_vision_client()

    try:
        resp = client.chat.completions.create(
            model=settings.effective_vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": DETECT_MESSAGE_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_base64}"
                    }},
                ],
            }],
            max_tokens=1024,
            temperature=0.1,
        )

        raw = resp.choices[0].message.content or ""
        return _parse_json(raw)

    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        return {"is_chat_screen": False, "has_new_message": False, "error": str(e)}


def _parse_json(raw: str) -> dict:
    """Extract the first JSON object from the LLM response."""
    import re
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw)

    depth = 0
    start = -1
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    pass
                break
    return {"is_chat_screen": False, "has_new_message": False}


def build_reply_commands(
    reply_text: str,
    input_box_y: int = 0,
    send_button_x: int = 0,
    send_button_y: int = 0,
    screen_width: int = 1080,
    screen_height: int = 2340,
) -> list[dict]:
    """Build a sequence of phone commands to type and send a reply."""
    ib_x = screen_width // 2
    ib_y = input_box_y if input_box_y > 0 else int(screen_height * 0.92)
    sb_x = send_button_x if send_button_x > 0 else int(screen_width * 0.9)
    sb_y = send_button_y if send_button_y > 0 else ib_y

    return [
        {"action": "tap", "x": ib_x, "y": ib_y},
        {"action": "wait", "ms": 300},
        {"action": "input", "text": reply_text},
        {"action": "wait", "ms": 300},
        {"action": "tap", "x": sb_x, "y": sb_y},
        {"action": "wait", "ms": 500},
    ]


def build_shortcut_commands(
    shortcut: str,
    params: dict | None = None,
    screen_width: int = 1080,
    screen_height: int = 2340,
) -> list[dict]:
    """Expand a shortcut name into a concrete command sequence.

    Supported shortcuts:
        open_app      — params: app_name or package_name
        send_message  — params: text, (optional) input_box_y, send_button_x, send_button_y
        go_back       — no params
        go_home       — no params
        scroll_down   — no params
        scroll_up     — no params
        tap           — params: x, y
        swipe         — params: x1, y1, x2, y2, (optional) duration
        input         — params: text
        wait          — params: ms

    Returns a list of command dicts ready for the phone to execute.
    Raises ValueError for unknown shortcuts.
    """
    params = params or {}

    if shortcut == "open_app":
        pkg = params.get("package_name", "")
        name = params.get("app_name", "")
        cmd: dict = {"action": "launch_app"}
        if pkg:
            cmd["package_name"] = pkg
        elif name:
            cmd["app_name"] = name
        else:
            raise ValueError("open_app requires 'app_name' or 'package_name'")
        return [cmd, {"action": "wait", "ms": 2000}]

    if shortcut == "send_message":
        text = params.get("text", "")
        if not text:
            raise ValueError("send_message requires 'text'")
        return build_reply_commands(
            reply_text=text,
            input_box_y=params.get("input_box_y", 0),
            send_button_x=params.get("send_button_x", 0),
            send_button_y=params.get("send_button_y", 0),
            screen_width=screen_width,
            screen_height=screen_height,
        )

    if shortcut == "go_back":
        return [{"action": "back"}]

    if shortcut == "go_home":
        return [{"action": "home"}]

    if shortcut == "scroll_down":
        mid_x = screen_width // 2
        return [{"action": "swipe",
                 "x1": mid_x, "y1": int(screen_height * 0.7),
                 "x2": mid_x, "y2": int(screen_height * 0.3),
                 "duration": 300}]

    if shortcut == "scroll_up":
        mid_x = screen_width // 2
        return [{"action": "swipe",
                 "x1": mid_x, "y1": int(screen_height * 0.3),
                 "x2": mid_x, "y2": int(screen_height * 0.7),
                 "duration": 300}]

    if shortcut in ("tap", "swipe", "input", "back", "home", "wait", "launch_app"):
        return [{"action": shortcut, **params}]

    raise ValueError(f"Unknown shortcut: {shortcut}")
