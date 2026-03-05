"""Phone device API routes — poll, command queue, auto-reply."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from phone_controller import analyze_screenshot, build_reply_commands, build_shortcut_commands
from task_engine import task_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["phone"])

_last_buyer_message: dict[str, str] = {}
_command_queue: dict[str, list[list[dict]]] = {}
_device_last_seen: dict[str, float] = {}


def get_device_state() -> tuple[dict[str, float], dict[str, list[list[dict]]]]:
    return _device_last_seen, _command_queue


class PhonePollRequest(BaseModel):
    device_id: str = "phone-1"
    screenshot: str = ""
    current_app: str = ""
    current_package: str = ""
    accessibility_enabled: bool = False
    last_results: list = []


class PhonePollResponse(BaseModel):
    commands: list[dict] = []
    next_poll_ms: int = 3000


_reply_agent = None


def set_reply_agent(agent):
    global _reply_agent
    _reply_agent = agent


class QuickReplyRequest(BaseModel):
    buyer_message: str
    buyer_id: str = "anonymous"
    session_id: str = ""
    product_context: str = ""


class QuickReplyResponse(BaseModel):
    reply: str
    session_id: str


@router.post("/quick-reply", response_model=QuickReplyResponse)
async def quick_reply(req: QuickReplyRequest):
    session_id = req.session_id or f"xianyu-{req.buyer_id}"
    message = req.buyer_message
    if req.product_context:
        message = f"[当前商品信息: {req.product_context}]\n\n买家消息: {message}"
    else:
        message = f"买家消息: {message}"

    try:
        response = _reply_agent.run(input=message, user_id=req.buyer_id, session_id=session_id)
        reply_text = response.content if response else "亲，稍等一下哈～"
        return QuickReplyResponse(reply=reply_text, session_id=session_id)
    except Exception as e:
        logger.error("Reply generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/phone/poll", response_model=PhonePollResponse)
async def phone_poll(req: PhonePollRequest):
    """Phone APP polls this endpoint.

    Priority:
      1. Manual commands from the queue
      2. Active task (Vision LLM multi-step execution)
      3. Auto-reply based on screenshot analysis
    """
    _device_last_seen[req.device_id] = time.time()

    queued = _command_queue.get(req.device_id)
    if queued:
        commands = queued.pop(0)
        if not queued:
            del _command_queue[req.device_id]
        logger.info("Dispatching %d queued commands to [%s]", len(commands), req.device_id)
        return PhonePollResponse(commands=commands, next_poll_ms=1000)

    active_task = task_engine.get_active_task(req.device_id)
    if active_task:
        commands, poll_ms = task_engine.process_poll(req.device_id, req.screenshot)
        logger.info("Task poll [%s] → %d cmds, screenshot=%d bytes",
                     req.device_id, len(commands), len(req.screenshot))
        return PhonePollResponse(commands=commands, next_poll_ms=poll_ms)

    if not req.screenshot:
        return PhonePollResponse(commands=[{"action": "noop"}], next_poll_ms=5000)

    analysis = analyze_screenshot(req.screenshot)

    if not analysis.get("is_chat_screen"):
        return PhonePollResponse(commands=[{"action": "noop"}], next_poll_ms=5000)

    if not analysis.get("has_new_message") or not analysis.get("buyer_message"):
        return PhonePollResponse(commands=[{"action": "noop"}], next_poll_ms=3000)

    buyer_msg = analysis["buyer_message"]
    buyer_name = analysis.get("buyer_name", "anonymous")
    device_key = f"{req.device_id}:{buyer_name}"

    if _last_buyer_message.get(device_key) == buyer_msg:
        return PhonePollResponse(commands=[{"action": "noop"}], next_poll_ms=3000)

    logger.info("New message from [%s]: %s", buyer_name, buyer_msg[:80])

    try:
        session_id = f"xianyu-{buyer_name}"
        product = analysis.get("product_name", "")
        message = f"买家消息: {buyer_msg}"
        if product:
            message = f"[商品: {product}]\n{message}"

        response = _reply_agent.run(input=message, user_id=buyer_name, session_id=session_id)
        reply_text = response.content if response else "亲，稍等一下哈～"
    except Exception as e:
        logger.error("Agent reply failed: %s", e)
        return PhonePollResponse(commands=[{"action": "noop"}], next_poll_ms=5000)

    logger.info("Reply to [%s]: %s", buyer_name, reply_text[:80])
    _last_buyer_message[device_key] = buyer_msg

    commands = build_reply_commands(
        reply_text=reply_text,
        input_box_y=analysis.get("input_box_y", 0),
        send_button_x=analysis.get("send_button_x", 0),
        send_button_y=analysis.get("send_button_y", 0),
        screen_width=analysis.get("screen_width", 1080),
        screen_height=analysis.get("screen_height", 2340),
    )
    return PhonePollResponse(commands=commands, next_poll_ms=3000)


class PhoneCommandRequest(BaseModel):
    device_id: str = "phone-1"
    commands: list[dict] | None = None
    shortcut: str | None = None
    params: dict | None = None


@router.post("/phone/command")
async def phone_command(req: PhoneCommandRequest):
    if req.commands:
        cmds = req.commands
    elif req.shortcut:
        try:
            cmds = build_shortcut_commands(req.shortcut, req.params)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="Provide 'commands' or 'shortcut'")

    _command_queue.setdefault(req.device_id, []).append(cmds)
    logger.info("Enqueued %d commands for [%s] (queue depth: %d)",
                len(cmds), req.device_id, len(_command_queue[req.device_id]))

    return {
        "queued": True,
        "device_id": req.device_id,
        "command_count": len(cmds),
        "queue_depth": len(_command_queue[req.device_id]),
        "commands": cmds,
    }


@router.get("/phone/commands/{device_id}")
async def phone_commands(device_id: str):
    queued = _command_queue.get(device_id, [])
    return {"device_id": device_id, "queue_depth": len(queued), "pending": queued}
