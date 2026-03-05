"""Reply Agent Service — AgentOS entrypoint.

Start:
    uvicorn app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from agent import create_reply_agent, load_knowledge
from config import settings
from phone_controller import (
    analyze_screenshot,
    build_reply_commands,
    build_shortcut_commands,
)
from task_engine import task_engine, get_screenshot_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_start_time = time.time()

_P = settings.api_path_prefix.rstrip("/")
_ADMIN = settings.admin_path.rstrip("/")

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

# Disable OpenAPI docs to hide API surface from scanners
app.openapi_url = None
app.docs_url = None
app.redoc_url = None

# ---------------------------------------------------------------------------
# 4. Security Middleware
# ---------------------------------------------------------------------------
_ALLOWED_PREFIXES = (
    f"{_P}/",
    f"{_ADMIN}",
    "/static/",
    "/favicon.ico",
    "/login",
)

_RATE_BUCKETS: dict[str, list[float]] = defaultdict(list)
_BLOCKED_IPS: dict[str, float] = {}
_BLOCK_DURATION = 300
_404_BODY = b""


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = _get_client_ip(request)
        now = time.time()

        # --- IP block check ---
        blocked_until = _BLOCKED_IPS.get(client_ip, 0)
        if now < blocked_until:
            return Response(status_code=404, content=_404_BODY)

        # --- Rate limiting ---
        if settings.rate_limit_rpm > 0:
            bucket = _RATE_BUCKETS[client_ip]
            cutoff = now - 60
            _RATE_BUCKETS[client_ip] = bucket = [t for t in bucket if t > cutoff]
            if len(bucket) >= settings.rate_limit_rpm:
                _BLOCKED_IPS[client_ip] = now + _BLOCK_DURATION
                logger.warning("Rate limit exceeded, blocking IP %s for %ds", client_ip, _BLOCK_DURATION)
                return Response(status_code=404, content=_404_BODY)
            bucket.append(now)

        path = request.url.path

        # --- Block all paths not matching our allowed prefixes ---
        if not any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            return Response(status_code=404, content=_404_BODY)

        # --- API Key authentication for all API endpoints ---
        if settings.api_key and path.startswith(f"{_P}/"):
            key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if key != settings.api_key:
                return Response(status_code=404, content=_404_BODY)

        # --- Admin page requires auth too ---
        if settings.api_key and path.startswith(f"{_ADMIN}"):
            key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            cookie_key = request.cookies.get("api_key")
            if key != settings.api_key and cookie_key != settings.api_key:
                return Response(status_code=404, content=_404_BODY)

        response = await call_next(request)

        # --- Security headers ---
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if "server" in response.headers:
            del response.headers["server"]

        return response


app.add_middleware(SecurityMiddleware)

# ---------------------------------------------------------------------------
# 5. Static files + Admin page
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    login_html = STATIC_DIR / "login.html"
    if login_html.exists():
        return FileResponse(login_html)
    return HTMLResponse("<h1>Not found</h1>", status_code=404)


@app.post("/login", include_in_schema=False)
async def login_verify(request: Request):
    body = await request.json()
    key = body.get("key", "")
    if not settings.api_key or key == settings.api_key:
        response = JSONResponse({"ok": True, "admin_path": _ADMIN})
        response.set_cookie("api_key", key, httponly=True, samesite="strict", max_age=86400 * 7)
        return response
    return JSONResponse({"ok": False}, status_code=401)


@app.get(f"{_ADMIN}", response_class=HTMLResponse, include_in_schema=False)
@app.get(f"{_ADMIN}/{{rest:path}}", response_class=HTMLResponse, include_in_schema=False)
async def admin_page(rest: str = ""):
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<h1>Not found</h1>", status_code=404)


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
    logger.info("  API prefix: %s", _P)
    logger.info("  Admin path: %s", _ADMIN)
    logger.info("  Rate limit: %d rpm", settings.rate_limit_rpm)

    count = load_knowledge(reply_agent)
    logger.info("  Knowledge: %d documents loaded", count)
    logger.info("Reply Agent Service ready on port %d", settings.agent_port)


# ===========================================================================
# API Endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# Health (requires auth)
# ---------------------------------------------------------------------------
@app.get(f"{_P}/health")
async def health():
    return {"status": "ok"}


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


@app.post(f"{_P}/quick-reply", response_model=QuickReplyResponse)
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


@app.post(f"{_P}/knowledge/reload")
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


@app.post(f"{_P}/phone/poll", response_model=PhonePollResponse)
async def phone_poll(req: PhonePollRequest):
    """Phone APP polls this endpoint.

    Priority:
      1. Manual commands from the queue
      2. Active task (Vision LLM multi-step execution)
      3. Auto-reply based on screenshot analysis
    """
    _device_last_seen[req.device_id] = time.time()

    # --- Priority 1: drain manual command queue ---
    queued = _command_queue.get(req.device_id)
    if queued:
        commands = queued.pop(0)
        if not queued:
            del _command_queue[req.device_id]
        logger.info("Dispatching %d queued commands to [%s]", len(commands), req.device_id)
        return PhonePollResponse(commands=commands, next_poll_ms=1000)

    # --- Priority 2: active task ---
    active_task = task_engine.get_active_task(req.device_id)
    if active_task:
        commands, poll_ms = task_engine.process_poll(req.device_id, req.screenshot)
        return PhonePollResponse(commands=commands, next_poll_ms=poll_ms)

    # --- Priority 3: auto-reply via screenshot analysis ---
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


@app.post(f"{_P}/phone/command")
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


@app.get(f"{_P}/phone/commands/{{device_id}}")
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
@app.get(f"{_P}/admin/status")
async def admin_status():
    docs = list(KNOWLEDGE_DIR.rglob("*.md")) if KNOWLEDGE_DIR.exists() else []
    now = time.time()
    devices = {
        did: {
            "last_seen_ago_s": round(now - ts, 1),
            "queue_depth": len(_command_queue.get(did, [])),
        }
        for did, ts in _device_last_seen.items()
    }
    active_tasks = {}
    for did in _device_last_seen:
        t = task_engine.get_active_task(did)
        if t:
            active_tasks[did] = {"task_id": t.id, "goal": t.goal[:60], "step": t.current_step, "status": t.status.value}

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
        "active_tasks": active_tasks,
        "recent_conversations": len(_last_buyer_message),
    }


# ---------------------------------------------------------------------------
# Admin: Knowledge CRUD
# ---------------------------------------------------------------------------
@app.get(f"{_P}/admin/knowledge")
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


@app.get(f"{_P}/admin/knowledge/{{doc_path:path}}")
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


@app.post(f"{_P}/admin/knowledge/{{doc_path:path}}")
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


@app.put(f"{_P}/admin/knowledge/{{doc_path:path}}")
async def admin_knowledge_update(doc_path: str, req: KnowledgeWriteRequest):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.write_text(req.content, encoding="utf-8")
    count = load_knowledge(reply_agent)
    return {"updated": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@app.delete(f"{_P}/admin/knowledge/{{doc_path:path}}")
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
@app.get(f"{_P}/admin/settings")
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


@app.put(f"{_P}/admin/settings")
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


# ===========================================================================
# Task API — Vision LLM multi-step task execution
# ===========================================================================

class TaskCreateRequest(BaseModel):
    device_id: str = "phone-1"
    goal: str


@app.post(f"{_P}/task")
async def task_create(req: TaskCreateRequest):
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="Goal is required")
    task = task_engine.create_task(req.device_id, req.goal.strip())
    return task.to_dict()


@app.get(f"{_P}/task/{{task_id}}")
async def task_get(task_id: str):
    task = task_engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@app.post(f"{_P}/task/{{task_id}}/cancel")
async def task_cancel(task_id: str):
    ok = task_engine.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    return {"cancelled": True, "task_id": task_id}


@app.get(f"{_P}/tasks")
async def task_list(device_id: str | None = None, limit: int = 20):
    return {"tasks": task_engine.list_tasks(device_id=device_id, limit=limit)}


# ===========================================================================
# Screenshot API
# ===========================================================================

@app.get(f"{_P}/screenshot/{{screenshot_id}}")
async def screenshot_get(screenshot_id: str):
    path = get_screenshot_path(screenshot_id)
    if not path:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/jpeg")
