"""Task API routes — Vision LLM multi-step task execution + screenshots."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from task_engine import task_engine, get_screenshot_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["task"])


class TaskCreateRequest(BaseModel):
    device_id: str = "phone-1"
    goal: str


@router.post("/task")
async def task_create(req: TaskCreateRequest):
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="Goal is required")
    task = task_engine.create_task(req.device_id, req.goal.strip())
    return task.to_dict()


@router.get("/task/{task_id}")
async def task_get(task_id: str):
    task = task_engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@router.post("/task/{task_id}/cancel")
async def task_cancel(task_id: str):
    ok = task_engine.cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    return {"cancelled": True, "task_id": task_id}


@router.get("/tasks")
async def task_list(device_id: str | None = None, limit: int = 20):
    return {"tasks": task_engine.list_tasks(device_id=device_id, limit=limit)}


@router.get("/screenshot/{screenshot_id}")
async def screenshot_get(screenshot_id: str):
    path = get_screenshot_path(screenshot_id)
    if not path:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/jpeg")
