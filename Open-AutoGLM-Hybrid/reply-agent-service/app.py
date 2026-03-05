"""Reply Agent Service — AgentOS entrypoint.

Start:
    uvicorn app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from agent import create_reply_agent, load_knowledge
from config import settings
from phone_controller import (
    analyze_screenshot,
    build_reply_commands,
    build_shortcut_commands,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_start_time = time.time()

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
# 3. AgentOS + App
# ---------------------------------------------------------------------------
agent_os = AgentOS(agents=[reply_agent], db=db)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# 4. API Key Authentication Middleware
# ---------------------------------------------------------------------------
_AUTH_WHITELIST = ("/api/health", "/admin", "/static", "/docs", "/openapi.json", "/favicon.ico")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.api_key:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _AUTH_WHITELIST):
            return await call_next(request)

        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if key != settings.api_key:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)


app.add_middleware(ApiKeyMiddleware)

# ---------------------------------------------------------------------------
# 5. Static files + Admin page
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/{rest:path}", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(rest: str = ""):
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<h1>Admin UI not found</h1><p>Place index.html in static/</p>", status_code=404)


# ---------------------------------------------------------------------------
# 6. Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    logger.info("Reply Agent Service starting...")
    logger.info("  LLM: %s / %s", settings.llm_provider, settings.llm_model)
    logger.info("  Embedding: %s", settings.embedding_model)
    logger.info("  Database: %s", "PostgreSQL" if settings.is_postgres else "SQLite")
    logger.info("  Auth: %s", "enabled" if settings.api_key else "disabled (no API_KEY set)")

    count = load_knowledge(reply_agent)
    logger.info("  Knowledge: %d documents loaded", count)
    logger.info("Reply Agent Service ready on port %d", settings.agent_port)


# ===========================================================================
# API Endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "agent": reply_agent.id,
        "model": settings.llm_model,
        "auth_enabled": bool(settings.api_key),
    }


# ---------------------------------------------------------------------------
# Quick Reply
# ---------------------------------------------------------------------------
class QuickReplyRequest(BaseModel):
    buyer_message: str
    buyer_id: str = "anonymous"
    session_id: str = ""
    product_context: str = ""


class QuickReplyResponse(BaseModel):
    reply: str
    session_id: str


@app.post("/api/quick-reply", response_model=QuickReplyResponse)
async def quick_reply(req: QuickReplyRequest):
    session_id = req.session_id or f"xianyu-{req.buyer_id}"
    message = req.buyer_message
    if req.product_context:
        message = f"[当前商品信息: {req.product_context}]\n\n买家消息: {message}"
    else:
        message = f"买家消息: {message}"

    try:
        response = reply_agent.run(input=message, user_id=req.buyer_id, session_id=session_id)
        reply_text = response.content if response else "亲，稍等一下哈～"
        return QuickReplyResponse(reply=reply_text, session_id=session_id)
    except Exception as e:
        logger.error("Reply generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/knowledge/reload")
async def reload_knowledge():
    count = load_knowledge(reply_agent)
    return {"reloaded": True, "documents": count}


# ---------------------------------------------------------------------------
# Phone Poll API
# ---------------------------------------------------------------------------
_last_buyer_message: dict[str, str] = {}
_command_queue: dict[str, list[list[dict]]] = {}
_device_last_seen: dict[str, float] = {}


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
    _device_last_seen[req.device_id] = time.time()

    queued = _command_queue.get(req.device_id)
    if queued:
        commands = queued.pop(0)
        if not queued:
            del _command_queue[req.device_id]
        logger.info("Dispatching %d queued commands to [%s]", len(commands), req.device_id)
        return PhonePollResponse(commands=commands, next_poll_ms=1000)

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

        response = reply_agent.run(input=message, user_id=buyer_name, session_id=session_id)
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
# Phone Command API
# ---------------------------------------------------------------------------
class PhoneCommandRequest(BaseModel):
    device_id: str = "phone-1"
    commands: list[dict] | None = None
    shortcut: str | None = None
    params: dict | None = None


@app.post("/api/phone/command")
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


@app.get("/api/phone/commands/{device_id}")
async def phone_commands(device_id: str):
    queued = _command_queue.get(device_id, [])
    return {"device_id": device_id, "queue_depth": len(queued), "pending": queued}


# ===========================================================================
# Admin API
# ===========================================================================
KNOWLEDGE_DIR = Path(settings.knowledge_docs_dir)


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "****" + key[-4:]


# ---------------------------------------------------------------------------
# Admin: Status
# ---------------------------------------------------------------------------
@app.get("/api/admin/status")
async def admin_status():
    docs = list(KNOWLEDGE_DIR.rglob("*.md")) if KNOWLEDGE_DIR.exists() else []
    now = time.time()
    devices = {
        did: {"last_seen_ago_s": round(now - ts, 1), "queue_depth": len(_command_queue.get(did, []))}
        for did, ts in _device_last_seen.items()
    }
    return {
        "uptime_s": round(now - _start_time, 1),
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "vision_model": settings.effective_vision_model,
        "embedding_model": settings.embedding_model,
        "database": "PostgreSQL" if settings.is_postgres else "SQLite",
        "auth_enabled": bool(settings.api_key),
        "knowledge_docs": len(docs),
        "devices": devices,
        "recent_conversations": len(_last_buyer_message),
    }


# ---------------------------------------------------------------------------
# Admin: Knowledge CRUD
# ---------------------------------------------------------------------------
@app.get("/api/admin/knowledge")
async def admin_knowledge_list():
    if not KNOWLEDGE_DIR.exists():
        return {"documents": []}
    docs = []
    for f in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        rel = f.relative_to(KNOWLEDGE_DIR)
        category = rel.parts[0] if len(rel.parts) > 1 else "general"
        docs.append({
            "name": f.stem,
            "path": str(rel),
            "category": category,
            "size": f.stat().st_size,
        })
    return {"documents": docs}


@app.get("/api/admin/knowledge/{doc_path:path}")
async def admin_knowledge_get(doc_path: str):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "path": str(target.relative_to(KNOWLEDGE_DIR)),
        "content": target.read_text(encoding="utf-8"),
    }


class KnowledgeWriteRequest(BaseModel):
    content: str
    category: str = "general"


@app.post("/api/admin/knowledge/{doc_path:path}")
async def admin_knowledge_create(doc_path: str, req: KnowledgeWriteRequest):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if target.exists():
        raise HTTPException(status_code=409, detail="Document already exists, use PUT to update")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    count = load_knowledge(reply_agent)
    return {"created": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@app.put("/api/admin/knowledge/{doc_path:path}")
async def admin_knowledge_update(doc_path: str, req: KnowledgeWriteRequest):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.write_text(req.content, encoding="utf-8")
    count = load_knowledge(reply_agent)
    return {"updated": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@app.delete("/api/admin/knowledge/{doc_path:path}")
async def admin_knowledge_delete(doc_path: str):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.unlink()
    count = load_knowledge(reply_agent)
    return {"deleted": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


# ---------------------------------------------------------------------------
# Admin: Settings
# ---------------------------------------------------------------------------
@app.get("/api/admin/settings")
async def admin_settings_get():
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_api_key": _mask_key(settings.llm_api_key),
        "llm_base_url": settings.llm_base_url,
        "embedding_model": settings.embedding_model,
        "embedding_base_url": settings.embedding_base_url,
        "embedding_dimensions": settings.embedding_dimensions,
        "vision_model": settings.effective_vision_model,
        "vision_base_url": settings.effective_vision_base_url,
        "database_url": settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
        "min_price_ratio": settings.min_price_ratio,
        "auto_escalate_keywords": settings.auto_escalate_keywords,
    }


class SettingsUpdateRequest(BaseModel):
    min_price_ratio: float | None = None
    auto_escalate_keywords: str | None = None


@app.put("/api/admin/settings")
async def admin_settings_update(req: SettingsUpdateRequest):
    updated = {}
    if req.min_price_ratio is not None:
        settings.min_price_ratio = req.min_price_ratio
        updated["min_price_ratio"] = req.min_price_ratio
    if req.auto_escalate_keywords is not None:
        settings.auto_escalate_keywords = req.auto_escalate_keywords
        updated["auto_escalate_keywords"] = req.auto_escalate_keywords
    if not updated:
        raise HTTPException(status_code=400, detail="No settings to update")
    return {"updated": updated}
