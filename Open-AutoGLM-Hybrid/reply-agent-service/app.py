"""Reply Agent Service — FastAPI entrypoint.

Start:
    uvicorn app:app --host 0.0.0.0 --port 6443
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from agent import create_reply_agent, load_knowledge
from config import settings
from routes.admin import router as admin_router, set_reply_agent as admin_set_agent
from routes.phone import router as phone_router, set_reply_agent as phone_set_agent
from routes.task import router as task_router

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
phone_set_agent(reply_agent)
admin_set_agent(reply_agent)

# ---------------------------------------------------------------------------
# 3. AgentOS + App
# ---------------------------------------------------------------------------
agent_os = AgentOS(agents=[reply_agent], db=db)
app = agent_os.get_app()

app.openapi_url = None
app.docs_url = None
app.redoc_url = None

_P = settings.api_path_prefix.rstrip("/")
_ADMIN = settings.admin_path.rstrip("/")

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

        if now < _BLOCKED_IPS.get(client_ip, 0):
            return Response(status_code=404, content=_404_BODY)

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

        if not any(path.startswith(p) for p in _ALLOWED_PREFIXES):
            return Response(status_code=404, content=_404_BODY)

        if settings.api_key and path.startswith(f"{_P}/"):
            key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if key != settings.api_key:
                return Response(status_code=404, content=_404_BODY)

        if settings.api_key and path.startswith(f"{_ADMIN}"):
            key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            cookie_key = request.cookies.get("api_key")
            if key != settings.api_key and cookie_key != settings.api_key:
                return Response(status_code=404, content=_404_BODY)

        response = await call_next(request)

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
# 5. Mount routers (all under the API prefix)
# ---------------------------------------------------------------------------
app.include_router(phone_router, prefix=_P)
app.include_router(admin_router, prefix=_P)
app.include_router(task_router, prefix=_P)


# ---------------------------------------------------------------------------
# 6. Static files + Login + Admin page
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


@app.get(f"{_P}/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 7. Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    logger.info("Reply Agent Service starting...")
    logger.info("  LLM: %s / %s", settings.llm_provider, settings.llm_model)
    logger.info("  RAG: %s", "enabled" if settings.enable_rag else "disabled")
    if settings.enable_rag:
        logger.info("  Embedding: %s", settings.embedding_model)
    logger.info("  Database: %s", "PostgreSQL" if settings.is_postgres else "SQLite")
    logger.info("  Auth: %s", "enabled" if settings.api_key else "disabled (no API_KEY set)")
    logger.info("  API prefix: %s", _P)
    logger.info("  Admin path: %s", _ADMIN)
    logger.info("  Rate limit: %d rpm", settings.rate_limit_rpm)

    if settings.enable_rag:
        count = load_knowledge(reply_agent)
        logger.info("  Knowledge: %d documents loaded", count)
    else:
        logger.info("  Knowledge: skipped (RAG disabled)")

    logger.info("Reply Agent Service ready on port %d", settings.agent_port)
