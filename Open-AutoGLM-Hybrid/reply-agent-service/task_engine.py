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
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from openai import OpenAI

from config import settings

logger = logging.getLogger(__name__)

MAX_STEPS = 30
MAX_VISION_ERRORS = 3
DEFAULT_SCREEN_W = 1080
DEFAULT_SCREEN_H = 2340

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
    screen_w: int = field(default=DEFAULT_SCREEN_W, repr=False)
    screen_h: int = field(default=DEFAULT_SCREEN_H, repr=False)

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


# ---------------------------------------------------------------------------
# AutoGLM-Phone system prompt (from original Open-AutoGLM)
# ---------------------------------------------------------------------------
def _build_autoglm_system_prompt() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""今天的日期是: {today}
你是一个智能体分析专家，可以根据操作历史和当前状态图执行一系列操作来完成任务。
你必须严格按照要求输出以下格式：
<think>{{思考过程}}</think>
<answer>{{action}}</answer>

其中：
- {{think}} 是对你为什么选择这个操作的简短推理说明。
- {{action}} 是本次执行的具体操作指令，必须严格遵循下方定义的指令格式。

操作指令及其作用如下：
- do(action="Launch", app="xxx")
    Launch是启动目标app的操作，这比通过主屏幕导航更快。
- do(action="Tap", element=[x,y])
    Tap是点击操作。坐标系统从左上角 (0,0) 开始到右下角（999,999)结束。
- do(action="Type", text="xxx")
    Type是输入操作，在当前聚焦的输入框中输入文本。输入框中现有文本会自动清除。
- do(action="Swipe", start=[x1,y1], end=[x2,y2])
    Swipe是滑动操作。坐标系统从左上角 (0,0) 到右下角（999,999)。
- do(action="Long Press", element=[x,y])
    长按操作。坐标系统 (0,0) 到 (999,999)。
- do(action="Back")
    导航返回上一个屏幕或关闭对话框。
- do(action="Home")
    回到系统桌面。
- do(action="Wait", duration="x seconds")
    等待页面加载。
- finish(message="xxx")
    finish是结束任务的操作，message是终止信息。

必须遵循的规则：
1. 在执行任何操作前，先检查当前app是否是目标app，如果不是，先执行 Launch。
2. 如果进入到了无关页面，先执行 Back。
3. 如果页面未加载出内容，最多连续 Wait 三次，否则执行 Back 重新进入。
4. 如果当前页面找不到目标信息，可以尝试 Swipe 滑动查找。
5. 在执行下一步操作前请检查上一步的操作是否生效。
6. 在结束任务前请检查任务是否完整准确的完成。
"""


# Generic prompt for non-AutoGLM vision models
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


def _is_autoglm_model() -> bool:
    model = settings.effective_vision_model.lower()
    return "autoglm" in model


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


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------
def _parse_json(raw: str) -> dict:
    """Parse JSON from raw text, stripping markdown fences."""
    cleaned = re.sub(r"```json\s*", "", raw)
    cleaned = re.sub(r"```\s*$", "", cleaned)

    depth = 0
    start = -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(cleaned[start:i + 1])
                except json.JSONDecodeError:
                    pass
                break
    return {}


def _split_autoglm_response(content: str) -> tuple[str, str]:
    """Split AutoGLM response into (action_text, thinking_text).

    Supports:
      <answer>do(...)</answer> tags
      do(action=...) or finish(message=...) inline
    """
    m = re.search(r'<answer>(.*?)</answer>', content, re.DOTALL)
    if m:
        thinking = content[:m.start()].strip()
        thinking = re.sub(r'<think>(.*?)</think>', r'\1', thinking, flags=re.DOTALL).strip()
        return m.group(1).strip(), thinking

    idx = content.find('finish(')
    if idx >= 0:
        return content[idx:].strip(), content[:idx].strip()

    idx = content.find('do(')
    if idx >= 0:
        thinking = content[:idx].strip()
        action_text = content[idx:].strip()
        paren_depth = 0
        for i, ch in enumerate(action_text):
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    action_text = action_text[:i + 1]
                    break
        return action_text, thinking

    return content, ""


def _ast_parse_call(text: str) -> dict:
    """Parse do(action="Tap", element=[x,y]) or finish(message="...") into a dict."""
    params = {}

    for m in re.finditer(
        r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\[[^\]]*\]|\d+(?:\.\d+)?)',
        text
    ):
        key = m.group(1)
        val_str = m.group(2)
        if (val_str.startswith('"') and val_str.endswith('"')) or \
           (val_str.startswith("'") and val_str.endswith("'")):
            params[key] = val_str[1:-1]
        elif val_str.startswith('['):
            try:
                params[key] = json.loads(val_str)
            except json.JSONDecodeError:
                nums = re.findall(r'\d+', val_str)
                params[key] = [int(n) for n in nums]
        elif val_str.replace('.', '', 1).isdigit():
            params[key] = float(val_str) if '.' in val_str else int(val_str)
        else:
            params[key] = val_str

    return params


def _norm_to_pixel(val: int | float, screen_size: int) -> int:
    """Convert 0-999 normalized coordinate to pixel value."""
    return round(val / 999 * screen_size)


def _parse_autoglm_response(raw: str, screen_w: int = DEFAULT_SCREEN_W,
                             screen_h: int = DEFAULT_SCREEN_H) -> dict:
    """Parse AutoGLM-Phone model output into standardized commands.

    AutoGLM uses 0-999 normalized coordinates and do()/finish() format.
    """
    action_text, thinking = _split_autoglm_response(raw)

    if not action_text:
        return {}

    thinking_lines = [l.strip() for l in thinking.split("\n") if l.strip()]
    observation = thinking_lines[0][:200] if thinking_lines else ""
    thought = thinking_lines[-1][:200] if len(thinking_lines) > 1 else observation

    if action_text.startswith('finish(') or action_text.startswith('finish ('):
        m = re.search(r'message\s*=\s*["\'](.+?)["\']', action_text, re.DOTALL)
        reason = m.group(1) if m else "Task finished"
        return {
            "observation": observation,
            "thought": thought,
            "status": "completed",
            "commands": [],
            "reason": reason,
        }

    if not action_text.startswith('do(') and not action_text.startswith('do ('):
        return {}

    params = _ast_parse_call(action_text)
    action = params.pop("action", "")
    action_lower = action.lower().replace(" ", "_")

    commands = []
    status = "continue"

    if action_lower in ("launch",):
        app_name = params.get("app", params.get("app_name", ""))
        if app_name:
            commands.append({"action": "launch_app", "app_name": app_name})
            commands.append({"action": "wait", "ms": 2000})

    elif action_lower in ("tap", "click"):
        pos = params.get("element", params.get("position", [0, 0]))
        if isinstance(pos, list) and len(pos) >= 2:
            x = _norm_to_pixel(pos[0], screen_w)
            y = _norm_to_pixel(pos[1], screen_h)
            commands.append({"action": "tap", "x": x, "y": y})

    elif action_lower in ("long_press",):
        pos = params.get("element", params.get("position", [0, 0]))
        if isinstance(pos, list) and len(pos) >= 2:
            x = _norm_to_pixel(pos[0], screen_w)
            y = _norm_to_pixel(pos[1], screen_h)
            commands.append({"action": "long_press", "x": x, "y": y})

    elif action_lower in ("double_tap",):
        pos = params.get("element", params.get("position", [0, 0]))
        if isinstance(pos, list) and len(pos) >= 2:
            x = _norm_to_pixel(pos[0], screen_w)
            y = _norm_to_pixel(pos[1], screen_h)
            commands.append({"action": "tap", "x": x, "y": y})
            commands.append({"action": "wait", "ms": 100})
            commands.append({"action": "tap", "x": x, "y": y})

    elif action_lower in ("type", "type_name", "input"):
        text = params.get("text", params.get("content", ""))
        if text:
            commands.append({"action": "input", "text": text})

    elif action_lower in ("swipe", "scroll"):
        start = params.get("start", params.get("startPosition", [500, 800]))
        end = params.get("end", params.get("endPosition", [500, 200]))
        if isinstance(start, list) and isinstance(end, list) and len(start) >= 2 and len(end) >= 2:
            commands.append({
                "action": "swipe",
                "x1": _norm_to_pixel(start[0], screen_w),
                "y1": _norm_to_pixel(start[1], screen_h),
                "x2": _norm_to_pixel(end[0], screen_w),
                "y2": _norm_to_pixel(end[1], screen_h),
                "duration": 300,
            })

    elif action_lower == "back":
        commands.append({"action": "back"})

    elif action_lower == "home":
        commands.append({"action": "home"})

    elif action_lower == "wait":
        dur = params.get("duration", "2")
        try:
            secs = int(str(dur).replace(" seconds", "").replace("seconds", "").strip())
        except (ValueError, TypeError):
            secs = 2
        commands.append({"action": "wait", "ms": secs * 1000})

    elif action_lower == "take_over":
        msg = params.get("message", "需要用户协助")
        status = "failed"
        return {
            "observation": observation,
            "thought": thought,
            "status": status,
            "commands": [],
            "reason": f"需要用户介入: {msg}",
        }

    else:
        logger.warning("Unknown AutoGLM action: %s (params=%s)", action, params)

    return {
        "observation": observation,
        "thought": thought,
        "status": status,
        "commands": commands,
        "reason": "",
    }


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

    def process_poll(self, device_id: str, screenshot_base64: str,
                     screen_w: int = 0, screen_h: int = 0) -> tuple[list[dict], int]:
        """Called by phone_poll when a device has an active task.

        Returns (commands, next_poll_ms).
        """
        task = self.get_active_task(device_id)
        if not task:
            return [{"action": "noop"}], 3000

        if screen_w > 0 and screen_h > 0:
            task.screen_w = screen_w
            task.screen_h = screen_h

        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

        if task.current_step >= MAX_STEPS:
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()
            task.error = f"Max steps ({MAX_STEPS}) exceeded"
            logger.warning("Task [%s] exceeded max steps", task.id)
            return [{"action": "noop"}], 3000

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
            logger.warning("Task [%s] step %d: vision returned empty commands", task.id, step.step)
            return [{"action": "noop"}], 2000

        logger.info("Task [%s] step %d: %s → %s",
                     task.id, step.step, thought[:40],
                     json.dumps(commands, ensure_ascii=False)[:200])
        return commands, 1500

    def _ask_vision(self, task: Task, screenshot_base64: str) -> dict:
        client = _get_vision_client()
        use_autoglm = _is_autoglm_model()

        if use_autoglm:
            return self._ask_autoglm(client, task, screenshot_base64)
        return self._ask_generic_vision(client, task, screenshot_base64)

    def _ask_autoglm(self, client: OpenAI, task: Task, screenshot_base64: str) -> dict:
        """Call AutoGLM-Phone with its native system prompt and conversation history."""
        messages: list[dict] = [
            {"role": "system", "content": _build_autoglm_system_prompt()},
        ]

        for s in task.steps[-5:]:
            cmds_desc = ", ".join(c.get("action", "?") for c in s.commands)
            messages.append({
                "role": "assistant",
                "content": f"<think>{s.thought}</think>\n<answer>{cmds_desc}</answer>",
            })

        user_content = task.goal
        if task.steps:
            user_content = f"{task.goal}\n\n已执行 {len(task.steps)} 步，请根据当前截图继续。"

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_content},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{screenshot_base64}"
                }},
            ],
        })

        resp = client.chat.completions.create(
            model=settings.effective_vision_model,
            messages=messages,
            max_tokens=1024,
            temperature=0.1,
        )

        raw = resp.choices[0].message.content or ""
        logger.info("Task [%s] AutoGLM raw (%d chars): %s", task.id, len(raw), raw[:500])

        result = _parse_autoglm_response(raw, task.screen_w, task.screen_h)
        if result and (result.get("commands") or result.get("status") != "continue"):
            logger.info("Task [%s] AutoGLM parsed: status=%s, commands=%s",
                         task.id, result.get("status"),
                         json.dumps(result.get("commands", []), ensure_ascii=False)[:200])
            return result

        logger.warning("Task [%s] failed to parse AutoGLM response", task.id)
        return {}

    def _ask_generic_vision(self, client: OpenAI, task: Task, screenshot_base64: str) -> dict:
        """Call a generic vision model with JSON output format."""
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
        logger.info("Task [%s] vision raw (%d chars): %s", task.id, len(raw), raw[:500])

        result = _parse_json(raw)
        if result:
            logger.info("Task [%s] parsed as JSON: status=%s, commands=%d",
                         task.id, result.get("status"), len(result.get("commands", [])))
            return result

        logger.warning("Task [%s] failed to parse vision response", task.id)
        return {}


task_engine = TaskEngine()
