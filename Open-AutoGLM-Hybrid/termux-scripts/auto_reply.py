"""Auto-reply mode for Xianyu / e-commerce apps.

Monitors the screen for new buyer messages and automatically generates
replies using the remote Agno Reply Agent service.

Usage:
    python auto_reply.py                    # interactive config
    python auto_reply.py --agent-url http://server:8080
    python auto_reply.py --app xianyu --interval 5
"""

import argparse
import logging
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

from config import load_config, AppConfig
from ai_client import create_ai_client, BaseAIClient
from phone_controller import PhoneController
from agent_api import AgentAPIClient, AgentReply

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("AutoReply")


# ---------------------------------------------------------------------------
# Screen analysis prompts
# ---------------------------------------------------------------------------

DETECT_MESSAGE_PROMPT = """分析这张手机截图，判断当前是否在聊天界面，是否有新的买家消息需要回复。

请用 JSON 格式返回：
{
    "is_chat_screen": true/false,
    "has_new_message": true/false,
    "buyer_message": "买家最新一条消息的完整文本（如果有）",
    "buyer_name": "买家昵称（如果能识别）",
    "product_name": "正在讨论的商品名称（如果能识别）",
    "input_box_visible": true/false,
    "message_count": 0
}

规则：
- 如果不是聊天界面，所有字段返回 false/空
- buyer_message 只返回买家（对方）的最新一条消息，不要返回自己发的
- 如果最新消息是自己发的（通常在右侧），has_new_message 返回 false
- 尽量识别买家昵称和商品名称"""

FIND_INPUT_PROMPT = """分析这张手机截图，找到聊天输入框和发送按钮的位置。

请用 JSON 格式返回：
{
    "input_box": {"x": 输入框中心X坐标, "y": 输入框中心Y坐标},
    "send_button": {"x": 发送按钮中心X坐标, "y": 发送按钮中心Y坐标},
    "has_input_box": true/false
}

坐标使用屏幕实际像素值。"""


@dataclass
class ChatMessage:
    buyer_name: str = ""
    buyer_message: str = ""
    product_name: str = ""
    is_chat_screen: bool = False
    has_new_message: bool = False
    input_box_visible: bool = False


@dataclass
class ReplyState:
    """Tracks state to avoid duplicate replies."""
    last_buyer_message: str = ""
    last_reply_time: float = 0
    consecutive_no_message: int = 0
    total_replies: int = 0
    errors: int = 0


def _parse_detection_result(raw: str) -> ChatMessage:
    """Parse the vision model's JSON response into a ChatMessage."""
    import json

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
                    data = json.loads(raw[start : i + 1])
                    return ChatMessage(
                        buyer_name=data.get("buyer_name", ""),
                        buyer_message=data.get("buyer_message", ""),
                        product_name=data.get("product_name", ""),
                        is_chat_screen=data.get("is_chat_screen", False),
                        has_new_message=data.get("has_new_message", False),
                        input_box_visible=data.get("input_box_visible", False),
                    )
                except json.JSONDecodeError:
                    pass
                break

    return ChatMessage()


def detect_new_message(
    phone: PhoneController, ai: BaseAIClient
) -> tuple[Optional[ChatMessage], any]:
    """Take a screenshot and detect if there's a new buyer message.

    Returns:
        (ChatMessage or None, screenshot PIL.Image)
    """
    screenshot = phone.screenshot()
    if screenshot is None:
        return None, None

    from ai_client import Action

    history = []
    action, _ = ai.decide_action(DETECT_MESSAGE_PROMPT, screenshot, history)

    raw_content = str(action.params.get("raw", action.params.get("reason", "")))
    if not raw_content:
        raw_content = str(action)

    msg = _parse_detection_result(raw_content)
    return msg, screenshot


def send_reply(
    phone: PhoneController,
    ai: BaseAIClient,
    reply_text: str,
    screenshot,
) -> bool:
    """Type the reply text and send it.

    Uses the vision model to locate the input box and send button,
    then performs tap + input + tap-send.
    """
    phone.tap(540, 2100)
    time.sleep(0.5)

    ok = phone.input_text(reply_text)
    if not ok:
        logger.error("Failed to input reply text")
        return False

    time.sleep(0.3)

    phone.tap(980, 2100)
    time.sleep(0.5)

    logger.info("Reply sent: %s", reply_text[:50])
    return True


def run_auto_reply(
    phone: PhoneController,
    ai: BaseAIClient,
    agent: AgentAPIClient,
    check_interval: float = 5.0,
    max_errors: int = 10,
):
    """Main auto-reply loop.

    Continuously monitors the chat screen for new messages and
    generates replies using the Agno Reply Agent service.
    """
    state = ReplyState()

    print("\n╔══════════════════════════════════════╗")
    print("║     Auto-Reply Mode (自动回复)       ║")
    print("║  请打开闲鱼/App 的聊天界面           ║")
    print("║  按 Ctrl+C 停止                      ║")
    print("╚══════════════════════════════════════╝\n")

    while True:
        try:
            msg, screenshot = detect_new_message(phone, ai)

            if msg is None:
                logger.debug("Screenshot failed, retrying...")
                time.sleep(check_interval)
                continue

            if not msg.is_chat_screen:
                state.consecutive_no_message += 1
                if state.consecutive_no_message % 10 == 1:
                    logger.info("Not on chat screen, waiting... (%d checks)", state.consecutive_no_message)
                time.sleep(check_interval)
                continue

            if not msg.has_new_message or not msg.buyer_message:
                state.consecutive_no_message += 1
                time.sleep(check_interval)
                continue

            if msg.buyer_message == state.last_buyer_message:
                time.sleep(check_interval)
                continue

            state.consecutive_no_message = 0
            buyer_id = msg.buyer_name or "anonymous"
            logger.info(
                "New message from [%s]: %s",
                buyer_id, msg.buyer_message[:100],
            )

            result = agent.get_reply(
                buyer_message=msg.buyer_message,
                buyer_id=buyer_id,
                product_context=msg.product_name,
            )

            if not result.success:
                logger.error("Agent reply failed: %s", result.error)
                state.errors += 1
                if state.errors >= max_errors:
                    logger.error("Too many errors (%d), stopping", state.errors)
                    break
                time.sleep(check_interval)
                continue

            logger.info("Agent reply: %s", result.reply[:100])

            ok = send_reply(phone, ai, result.reply, screenshot)
            if ok:
                state.last_buyer_message = msg.buyer_message
                state.last_reply_time = time.time()
                state.total_replies += 1
                state.errors = 0
                print(f"  [{buyer_id}] {msg.buyer_message[:40]}")
                print(f"  => {result.reply[:60]}")
                print(f"  (total replies: {state.total_replies})\n")
            else:
                state.errors += 1

            time.sleep(check_interval)

        except KeyboardInterrupt:
            print(f"\n\nAuto-reply stopped. Total replies: {state.total_replies}")
            break
        except Exception as e:
            logger.error("Auto-reply error: %s", e)
            state.errors += 1
            if state.errors >= max_errors:
                logger.error("Too many errors, stopping")
                break
            time.sleep(check_interval)


def main():
    parser = argparse.ArgumentParser(description="Auto-reply mode for Xianyu/e-commerce")
    parser.add_argument("--agent-url", default="http://localhost:8080",
                        help="Agno Reply Agent service URL")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Check interval in seconds (default: 5)")
    parser.add_argument("-c", "--config", default=None,
                        help="Config file path")
    parser.add_argument("-m", "--mode", choices=["auto", "accessibility", "ladb"],
                        default=None, help="Phone control mode")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print("\n╔══════════════════════════════════════╗")
    print("║   Open-AutoGLM Auto-Reply Setup      ║")
    print("╚══════════════════════════════════════╝\n")

    cfg = load_config(args.config)
    mode = args.mode or cfg.helper.mode

    print(f"  Vision AI: {cfg.ai.provider} / {cfg.ai.model}")
    print(f"  Agent API: {args.agent_url}")
    print(f"  Interval:  {args.interval}s")

    ai = create_ai_client(cfg.ai)
    phone = PhoneController(helper_url=cfg.helper.url, preferred_mode=mode)
    print(f"  Phone:     {phone.get_mode()}")

    agent = AgentAPIClient(base_url=args.agent_url)
    print()

    run_auto_reply(
        phone=phone,
        ai=ai,
        agent=agent,
        check_interval=args.interval,
    )


if __name__ == "__main__":
    main()
