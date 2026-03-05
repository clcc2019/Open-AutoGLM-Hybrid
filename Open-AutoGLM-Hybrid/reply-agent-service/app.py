"""Reply Agent Service — AgentOS entrypoint.

Start:
    uvicorn app:app --host 0.0.0.0 --port 7777
    # or
    fastapi dev app.py --port 7777
"""

from __future__ import annotations

import logging

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS

from config import settings
from agent import create_reply_agent, load_knowledge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Database
# ---------------------------------------------------------------------------
if settings.is_postgres:
    from agno.db.postgres import PostgresDb
    db = PostgresDb(db_url=settings.database_url)
else:
    db_path = settings.database_url.replace("sqlite:///", "")
    db = SqliteDb(db_file=db_path)

# ---------------------------------------------------------------------------
# 2. Agent
# ---------------------------------------------------------------------------
reply_agent = create_reply_agent(db=db)

# ---------------------------------------------------------------------------
# 3. AgentOS
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    agents=[reply_agent],
    db=db,
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# 4. Startup — load knowledge base
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    logger.info("Reply Agent Service starting...")
    logger.info("  LLM: %s / %s", settings.llm_provider, settings.llm_model)
    logger.info("  Embedding: %s", settings.embedding_model)
    logger.info("  Database: %s", "PostgreSQL" if settings.is_postgres else "SQLite")

    count = load_knowledge(reply_agent)
    logger.info("  Knowledge: %d documents loaded", count)
    logger.info("Reply Agent Service ready on port %d", settings.agent_port)


# ---------------------------------------------------------------------------
# 5. Custom API — convenience endpoints
# ---------------------------------------------------------------------------
from fastapi import HTTPException
from pydantic import BaseModel


class QuickReplyRequest(BaseModel):
    """Simplified request for quick reply generation."""
    buyer_message: str
    buyer_id: str = "anonymous"
    session_id: str = ""
    product_context: str = ""


class QuickReplyResponse(BaseModel):
    reply: str
    session_id: str


@app.post("/api/quick-reply", response_model=QuickReplyResponse)
async def quick_reply(req: QuickReplyRequest):
    """Generate a reply without using the full AgentOS run API.

    This is a convenience endpoint for Open-AutoGLM integration.
    """
    session_id = req.session_id or f"xianyu-{req.buyer_id}"

    message = req.buyer_message
    if req.product_context:
        message = f"[当前商品信息: {req.product_context}]\n\n买家消息: {message}"
    else:
        message = f"买家消息: {message}"

    try:
        response = reply_agent.run(
            message=message,
            user_id=req.buyer_id,
            session_id=session_id,
        )
        reply_text = response.content if response else "亲，稍等一下哈～"
        return QuickReplyResponse(reply=reply_text, session_id=session_id)
    except Exception as e:
        logger.error("Reply generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/knowledge/reload")
async def reload_knowledge():
    """Reload knowledge documents from disk."""
    count = load_knowledge(reply_agent)
    return {"reloaded": True, "documents": count}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "agent": reply_agent.id,
        "model": settings.llm_model,
    }


# ---------------------------------------------------------------------------
# 6. Phone Poll API — the phone APP calls this to get commands
# ---------------------------------------------------------------------------
from phone_controller import analyze_screenshot, build_reply_commands, build_shortcut_commands

_last_buyer_message: dict[str, str] = {}

# Per-device manual command queue. Commands enqueued via /api/phone/command
# are drained by the next /api/phone/poll from the matching device.
_command_queue: dict[str, list[list[dict]]] = {}


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


@app.post("/api/phone/poll", response_model=PhonePollResponse)
async def phone_poll(req: PhonePollRequest):
    """Phone APP polls this endpoint.

    Priority:
      1. Manual commands from the queue (enqueued via /api/phone/command)
      2. Auto-reply based on screenshot analysis
    """
    # --- Priority 1: drain manual command queue ---
    queued = _command_queue.get(req.device_id)
    if queued:
        commands = queued.pop(0)
        if not queued:
            del _command_queue[req.device_id]
        logger.info("Dispatching %d queued commands to [%s]", len(commands), req.device_id)
        return PhonePollResponse(commands=commands, next_poll_ms=1000)

    # --- Priority 2: auto-reply via screenshot analysis ---
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

        response = reply_agent.run(
            message=message,
            user_id=buyer_name,
            session_id=session_id,
        )
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


# ---------------------------------------------------------------------------
# 7. Manual Command API — enqueue commands for a device
# ---------------------------------------------------------------------------

class PhoneCommandRequest(BaseModel):
    """Enqueue commands for a device to execute on next poll.

    Two modes:
      - Raw: provide `commands` list directly
      - Shortcut: provide `shortcut` name + optional `params`
    """
    device_id: str = "phone-1"
    commands: list[dict] | None = None
    shortcut: str | None = None
    params: dict | None = None


@app.post("/api/phone/command")
async def phone_command(req: PhoneCommandRequest):
    """Enqueue manual commands for a device.

    The commands will be picked up by the next /api/phone/poll from that device.
    """
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


@app.get("/api/phone/commands/{device_id}")
async def phone_commands(device_id: str):
    """View the pending command queue for a device (debug endpoint)."""
    queued = _command_queue.get(device_id, [])
    return {
        "device_id": device_id,
        "queue_depth": len(queued),
        "pending": queued,
    }
