"""Admin API routes — status, knowledge CRUD, settings, capabilities."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent import get_agent_capabilities, load_knowledge
from config import settings
from task_engine import task_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

KNOWLEDGE_DIR = Path(settings.knowledge_docs_dir)

_start_time = time.time()
_reply_agent = None


def set_reply_agent(agent):
    global _reply_agent
    _reply_agent = agent


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "****" + key[-4:]


@router.get("/admin/status")
async def admin_status():
    from routes.phone import get_device_state
    device_last_seen, command_queue = get_device_state()

    docs = list(KNOWLEDGE_DIR.rglob("*.md")) if KNOWLEDGE_DIR.exists() else []
    now = time.time()
    devices = {
        did: {
            "last_seen_ago_s": round(now - ts, 1),
            "queue_depth": len(command_queue.get(did, [])),
        }
        for did, ts in device_last_seen.items()
    }
    active_tasks = {}
    for did in device_last_seen:
        t = task_engine.get_active_task(did)
        if t:
            active_tasks[did] = {
                "task_id": t.id, "goal": t.goal[:60],
                "step": t.current_step, "status": t.status.value,
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
        "active_tasks": active_tasks,
        "recent_conversations": 0,
        "capabilities": get_agent_capabilities(),
    }


@router.post("/knowledge/reload")
async def reload_knowledge():
    count = load_knowledge(_reply_agent)
    return {"reloaded": True, "documents": count}


@router.get("/admin/knowledge")
async def admin_knowledge_list():
    if not KNOWLEDGE_DIR.exists():
        return {"documents": []}
    docs = []
    for f in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        rel = f.relative_to(KNOWLEDGE_DIR)
        category = rel.parts[0] if len(rel.parts) > 1 else "general"
        docs.append({
            "name": f.stem, "path": str(rel),
            "category": category, "size": f.stat().st_size,
        })
    return {"documents": docs}


@router.get("/admin/knowledge/{doc_path:path}")
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


@router.post("/admin/knowledge/{doc_path:path}")
async def admin_knowledge_create(doc_path: str, req: KnowledgeWriteRequest):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if target.exists():
        raise HTTPException(status_code=409, detail="Document already exists, use PUT to update")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    count = load_knowledge(_reply_agent)
    return {"created": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@router.put("/admin/knowledge/{doc_path:path}")
async def admin_knowledge_update(doc_path: str, req: KnowledgeWriteRequest):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.write_text(req.content, encoding="utf-8")
    count = load_knowledge(_reply_agent)
    return {"updated": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@router.delete("/admin/knowledge/{doc_path:path}")
async def admin_knowledge_delete(doc_path: str):
    target = KNOWLEDGE_DIR / doc_path
    if not target.suffix:
        target = target.with_suffix(".md")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.unlink()
    count = load_knowledge(_reply_agent)
    return {"deleted": str(target.relative_to(KNOWLEDGE_DIR)), "knowledge_reloaded": count}


@router.get("/admin/settings")
async def admin_settings_get():
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_api_key": _mask_key(settings.llm_api_key),
        "llm_base_url": settings.llm_base_url,
        "embedding_model": settings.embedding_model if settings.enable_rag else "(RAG disabled)",
        "embedding_base_url": settings.embedding_base_url if settings.enable_rag else "",
        "embedding_dimensions": settings.embedding_dimensions,
        "vision_model": settings.effective_vision_model,
        "vision_base_url": settings.effective_vision_base_url,
        "database_url": settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url,
        "enable_rag": settings.enable_rag,
        "min_price_ratio": settings.min_price_ratio,
        "auto_escalate_keywords": settings.auto_escalate_keywords,
    }


class SettingsUpdateRequest(BaseModel):
    min_price_ratio: float | None = None
    auto_escalate_keywords: str | None = None


@router.put("/admin/settings")
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


@router.get("/capabilities")
async def capabilities():
    return get_agent_capabilities()
