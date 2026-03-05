"""Task Engine — Vision LLM-driven multi-step task execution.

Implements the core loop from the original Open-AutoGLM:
  screenshot → Vision LLM analysis → decide action → execute → screenshot → verify → repeat

Each task maintains its own state machine:
  pending → running → (completed | failed | cancelled)

The phone polls /api/phone/poll as usual. When an active task exists for the
device, the poll handler delegates to TaskEngine instead of the auto-reply path.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

MAX_STEPS = 30
MAX_VISION_ERRORS = 3

SCREENSHOT_DIR = Path(os.environ.get("SCREENSHOT_DIR", "tmp/screenshots"))
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    step: int
    timestamp: float
    observation: str
    thought: str
    commands: list[dict]
    status: str = ""
    screenshot_id: str = ""


@dataclass
class Task:
    id: str
    device_id: str
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    steps: list[TaskStep] = field(default_factory=list)
    error: str = ""
    _consecutive_errors: int = field(default=0, repr=False)

    @property
    def current_step(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "goal": self.goal,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_step": self.current_step,
            "steps": [
                {
                    "step": s.step,
                    "timestamp": s.timestamp,
                    "observation": s.observation[:200],
                    "thought": s.thought,
                    "commands": s.commands,
                    "status": s.status,
                    "screenshot_id": s.screenshot_id,
                }
                for s in self.steps
            ],
            "error": self.error,
        }


TASK_PLANNER_PROMPT = """你是一个手机自动化操作助手。你需要根据用户的目标和当前手机屏幕截图，决定下一步操作。

## 用户目标
{goal}

## 已执行步骤
{history}

## 规则
1. 仔细观察截图，理解当前屏幕状态
2. 根据目标和已执行步骤，决定下一步操作
3. 每次只返回一步操作（可包含多个连续指令）
4. 如果目标已完成，返回 "completed"
5. 如果遇到无法继续的情况（如找不到目标元素、出错），返回 "failed" 并说明原因
6. 坐标使用像素值，参考截图尺寸估算

## 可用操作
- tap: 点击指定坐标 {{"action":"tap","x":540,"y":1200}}
- swipe: 滑动 {{"action":"swipe","x1":540,"y1":1500,"x2":540,"y2":500,"duration":300}}
- input: 输入文字 {{"action":"input","text":"你好"}}
- back: 返回键 {{"action":"back"}}
- home: Home键 {{"action":"home"}}
- launch_app: 打开应用 {{"action":"launch_app","app_name":"闲鱼"}}
- wait: 等待 {{"action":"wait","ms":2000}}

## 返回格式（严格 JSON）
{{
    "observation": "对当前屏幕的描述（简洁，1-2句话）",
    "thought": "思考过程（为什么选择这个操作）",
    "status": "continue" 或 "completed" 或 "failed",
    "commands": [操作列表],
    "reason": "如果 completed/failed，说明原因"
}}

只返回 JSON，不要返回其他内容。"""


def _get_vision_client() -> OpenAI:
    return OpenAI(
        api_key=settings.effective_vision_api_key,
        base_url=settings.effective_vision_base_url,
    )


def _format_history(steps: list[TaskStep]) -> str:
    if not steps:
        return "（尚未执行任何步骤）"
    lines = []
    for s in steps[-5:]:
        cmds_str = ", ".join(c.get("action", "?") for c in s.commands)
        lines.append(f"步骤{s.step}: {s.thought} → [{cmds_str}] → {s.status}")
    return "\n".join(lines)


def _parse_json(raw: str) -> dict:
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
    return {}


def _try_extract_app_name(goal: str) -> str:
    """Try to extract an app name from the task goal for the initial launch."""
    apps = ["闲鱼", "微信", "淘宝", "抖音", "支付宝", "京东", "拼多多", "小红书",
            "设置", "相机", "浏览器", "地图", "日历", "备忘录", "计算器"]
    for app in apps:
        if app in goal:
            return app
    return ""


def save_screenshot(screenshot_base64: str, prefix: str = "s") -> str:
    """Save base64 screenshot to disk, return the screenshot ID."""
    sid = f"{prefix}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
    path = SCREENSHOT_DIR / f"{sid}.jpg"
    try:
        data = base64.b64decode(screenshot_base64)
        path.write_bytes(data)
    except Exception as e:
        logger.warning("Failed to save screenshot %s: %s", sid, e)
        return ""
    return sid


def get_screenshot_path(screenshot_id: str) -> Path | None:
    if not screenshot_id:
        return None
    path = SCREENSHOT_DIR / f"{screenshot_id}.jpg"
    return path if path.exists() else None


class TaskEngine:
    """Manages active tasks per device."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._active: dict[str, str] = {}

    def create_task(self, device_id: str, goal: str) -> Task:
        existing = self._active.get(device_id)
        if existing and existing in self._tasks:
            old = self._tasks[existing]
            if old.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                old.status = TaskStatus.CANCELLED
                old.finished_at = time.time()
                old.error = "Replaced by new task"

        task = Task(
            id=str(uuid.uuid4())[:8],
            device_id=device_id,
            goal=goal,
        )
        self._tasks[task.id] = task
        self._active[device_id] = task.id
        logger.info("Task created: [%s] %s → %s", task.id, device_id, goal[:60])
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_active_task(self, device_id: str) -> Optional[Task]:
        tid = self._active.get(device_id)
        if not tid:
            return None
        task = self._tasks.get(tid)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return task
        return None

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            task.error = "Cancelled by user"
            return True
        return False

    def list_tasks(self, device_id: str | None = None, limit: int = 20) -> list[dict]:
        tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        if device_id:
            tasks = [t for t in tasks if t.device_id == device_id]
        return [t.to_dict() for t in tasks[:limit]]

    def process_poll(self, device_id: str, screenshot_base64: str) -> tuple[list[dict], int]:
        """Called by phone_poll when a device has an active task.

        Returns (commands, next_poll_ms).
        """
        task = self.get_active_task(device_id)
        if not task:
            return [{"action": "noop"}], 3000

        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

        if task.current_step >= MAX_STEPS:
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()
            task.error = f"Max steps ({MAX_STEPS}) exceeded"
            logger.warning("Task [%s] exceeded max steps", task.id)
            return [{"action": "noop"}], 3000

        # First step without screenshot: try to launch the app directly
        if not screenshot_base64 and task.current_step == 0:
            app_name = _try_extract_app_name(task.goal)
            if app_name:
                step = TaskStep(
                    step=1,
                    timestamp=time.time(),
                    observation="无截图，根据目标直接打开应用",
                    thought=f"首步无截图，先打开 {app_name}",
                    commands=[{"action": "launch_app", "app_name": app_name}, {"action": "wait", "ms": 2000}],
                    status="continue",
                )
                task.steps.append(step)
                logger.info("Task [%s] step 1: auto-launch %s (no screenshot)", task.id, app_name)
                return step.commands, 2000

            return [{"action": "noop"}], 2000

        if not screenshot_base64:
            return [{"action": "noop"}], 2000

        screenshot_id = save_screenshot(screenshot_base64, prefix=f"task-{task.id}")

        try:
            result = self._ask_vision(task, screenshot_base64)
        except Exception as e:
            task._consecutive_errors += 1
            logger.error("Task [%s] vision error (%d/%d): %s",
                         task.id, task._consecutive_errors, MAX_VISION_ERRORS, e)

            if task._consecutive_errors >= MAX_VISION_ERRORS:
                task.status = TaskStatus.FAILED
                task.finished_at = time.time()
                task.error = f"Vision API failed {MAX_VISION_ERRORS} times: {e}"
                return [{"action": "noop"}], 3000

            step = TaskStep(
                step=task.current_step + 1,
                timestamp=time.time(),
                observation=f"Vision API 错误: {str(e)[:100]}",
                thought=f"第 {task._consecutive_errors} 次错误，等待重试",
                commands=[],
                status="error_retry",
                screenshot_id=screenshot_id,
            )
            task.steps.append(step)
            return [{"action": "wait", "ms": 2000}], 3000

        task._consecutive_errors = 0

        if not result:
            return [{"action": "noop"}], 2000

        status = result.get("status", "continue")
        commands = result.get("commands", [])
        observation = result.get("observation", "")
        thought = result.get("thought", "")
        reason = result.get("reason", "")

        step = TaskStep(
            step=task.current_step + 1,
            timestamp=time.time(),
            observation=observation,
            thought=thought,
            commands=commands,
            status=status,
            screenshot_id=screenshot_id,
        )
        task.steps.append(step)

        if status == "completed":
            task.status = TaskStatus.COMPLETED
            task.finished_at = time.time()
            logger.info("Task [%s] completed: %s", task.id, reason)
            return [{"action": "noop"}], 3000

        if status == "failed":
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()
            task.error = reason
            logger.warning("Task [%s] failed: %s", task.id, reason)
            return [{"action": "noop"}], 3000

        if not commands:
            return [{"action": "noop"}], 2000

        logger.info("Task [%s] step %d: %s → %d commands",
                     task.id, step.step, thought[:40], len(commands))
        return commands, 1500

    def _ask_vision(self, task: Task, screenshot_base64: str) -> dict:
        client = _get_vision_client()

        prompt = TASK_PLANNER_PROMPT.format(
            goal=task.goal,
            history=_format_history(task.steps),
        )

        resp = client.chat.completions.create(
            model=settings.effective_vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{screenshot_base64}"
                    }},
                ],
            }],
            max_tokens=1024,
            temperature=0.1,
        )

        raw = resp.choices[0].message.content or ""
        logger.debug("Task [%s] vision response: %s", task.id, raw[:200])
        return _parse_json(raw)


task_engine = TaskEngine()
