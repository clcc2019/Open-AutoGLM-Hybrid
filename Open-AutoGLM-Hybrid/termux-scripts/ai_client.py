"""
统一 AI 客户端 - 支持 OpenAI 兼容 API 和智谱 AI

两种 provider:
  - openai: 任何兼容 OpenAI Chat Completions API 的服务
  - zhipu:  智谱 AI（支持 autoglm-phone / GLM-4V 等模型）

autoglm-phone 模型输出 do(action="...", ...) 格式，使用 AST 解析。
其他模型输出 JSON 格式。
"""

import ast
import base64
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from PIL import Image

from config import AIConfig

logger = logging.getLogger('AIClient')

MAX_RETRIES = 3
RETRY_DELAY = 2


# ── 动作数据结构 ──────────────────────────────────────────────

class Action:
    TYPE_TAP = "tap"
    TYPE_LONG_PRESS = "long_press"
    TYPE_DOUBLE_TAP = "double_tap"
    TYPE_SWIPE = "swipe"
    TYPE_INPUT = "input"
    TYPE_LAUNCH_APP = "launch_app"
    TYPE_BACK = "back"
    TYPE_HOME = "home"
    TYPE_WAIT = "wait"
    TYPE_DONE = "done"
    TYPE_TAKE_OVER = "take_over"
    TYPE_UNKNOWN = "unknown"

    def __init__(self, action_type: str, **kwargs):
        self.action_type = action_type
        self.params = kwargs
        self.normalized_coords = False

    def __repr__(self):
        return f"Action({self.action_type}, {self.params})"


@dataclass
class AIResponse:
    content: str
    thinking: str = ""


# ── System Prompt (仅用于非 autoglm-phone 模型) ──────────────

SYSTEM_PROMPT_GENERIC = """你是一个手机操作助手。用户会给你一张手机截图和一个任务描述。
你需要分析截图内容，决定下一步操作。

每次只输出一个操作指令（JSON 格式）:
{"action": "launch_app", "app_name": "淘宝", "reason": "打开淘宝"}
{"action": "tap", "x": 500, "y": 800, "reason": "点击搜索框"}
{"action": "swipe", "x1": 500, "y1": 1500, "x2": 500, "y2": 500, "reason": "向上滑动"}
{"action": "input", "text": "蓝牙耳机", "reason": "输入搜索关键词"}
{"action": "back", "reason": "返回上一页"}
{"action": "home", "reason": "回到桌面"}
{"action": "wait", "seconds": 2, "reason": "等待页面加载"}
{"action": "done", "reason": "任务已完成"}

规则:
- 如果任务要求打开某个应用，优先使用 launch_app
- 每次只返回一个操作"""

# autoglm-phone 模型自带行为规则，不需要自定义 prompt
AUTOGLM_PHONE_MODELS = {"autoglm-phone"}


# ── 基类 ──────────────────────────────────────────────────────

class BaseAIClient(ABC):

    def __init__(self, config: AIConfig):
        self.config = config
        self._is_autoglm_phone = config.model.lower() in AUTOGLM_PHONE_MODELS

    @abstractmethod
    def _call_api(self, messages: list) -> AIResponse:
        ...

    def decide_action(self, task: str, image: Image.Image,
                      history: Optional[list] = None) -> tuple[Action, str]:
        b64 = self._image_to_base64(image)

        messages = []
        if not self._is_autoglm_phone:
            messages.append({"role": "system", "content": SYSTEM_PROMPT_GENERIC})
        if history:
            messages.extend(history)

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": task},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        })

        resp = self._call_with_retry(messages)
        logger.debug(f"模型原始返回: {resp.content[:500]}")
        if resp.thinking:
            logger.debug(f"模型思考: {resp.thinking[:300]}")

        content = resp.content
        thinking = resp.thinking

        if self._is_autoglm_phone:
            action_text, auto_thinking = self._split_autoglm_response(content)
            if not thinking:
                thinking = auto_thinking
            action = self._parse_autoglm_action(action_text)
        else:
            action = self._parse_json_action(content)

        return action, thinking

    def _call_with_retry(self, messages: list) -> AIResponse:
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._call_api(messages)
            except Exception as e:
                last_err = e
                logger.warning(f"API 调用失败 (第 {attempt}/{MAX_RETRIES} 次): {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
        raise RuntimeError(f"API 调用连续失败 {MAX_RETRIES} 次: {last_err}")

    # ── 工具方法 ──────────────────────────────────────────────

    @staticmethod
    def _image_to_base64(image: Image.Image, fmt: str = "JPEG",
                         quality: int = 80, max_size: int = 1080) -> str:
        w, h = image.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            image = image.resize((int(w * ratio), int(h * ratio)),
                                 Image.LANCZOS)
        buf = BytesIO()
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        image.save(buf, format=fmt, quality=quality)
        return base64.b64encode(buf.getvalue()).decode('utf-8')

    @staticmethod
    def _strip_think_tags(text: str) -> tuple[str, str]:
        pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
        thinking_parts = pattern.findall(text)
        thinking = "\n".join(thinking_parts).strip()
        content = pattern.sub('', text).strip()
        return content, thinking

    # ── autoglm-phone 响应解析 ────────────────────────────────

    @staticmethod
    def _split_autoglm_response(content: str) -> tuple[str, str]:
        """
        从 autoglm-phone 的响应中分离 thinking 和 action。
        模型输出格式: <思考文本> do(action="...", ...) 或 finish(message="...")
        也可能用 <answer>...</answer> 包裹。
        """
        # 尝试 <answer> 标签
        m = re.search(r'<answer>(.*?)</answer>', content, re.DOTALL)
        if m:
            thinking = content[:m.start()].strip()
            action_text = m.group(1).strip()
            return action_text, thinking

        # 尝试 finish(message= 标记
        idx = content.find('finish(message=')
        if idx >= 0:
            thinking = content[:idx].strip()
            action_text = content[idx:].strip()
            return action_text, thinking

        # 尝试 do(action= 标记
        idx = content.find('do(action=')
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

        # 尝试简化格式: Launch("闲鱼"), Tap(500,800), Wait(2), Back() 等
        _SIMPLE_ACTIONS = (
            'Launch', 'Tap', 'Click', 'Swipe', 'Type', 'Type_Name',
            'Back', 'Home', 'Wait', 'Long Press', 'Double Tap',
            'Take_over', 'Interact', 'Note',
        )
        for act_name in _SIMPLE_ACTIONS:
            pattern = re.compile(
                re.escape(act_name) + r'\s*\(', re.IGNORECASE
            )
            m = pattern.search(content)
            if m:
                thinking = content[:m.start()].strip()
                action_text = content[m.start():].strip()
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

    @classmethod
    def _parse_autoglm_action(cls, text: str) -> Action:
        """解析 autoglm-phone 格式，支持:
        - do(action="Launch", app="闲鱼")  — 官方完整格式
        - finish(message="...")             — 任务完成
        - Launch("闲鱼")                    — 简化格式
        """
        text = text.strip()

        # finish(message="...")
        if text.startswith('finish'):
            m = re.search(r'finish\s*\(\s*message\s*=\s*["\'](.+?)["\']', text, re.DOTALL)
            msg = m.group(1) if m else ""
            return Action(Action.TYPE_DONE, reason=msg)

        # do(action="...", ...) — 官方完整格式
        if text.startswith('do(') or text.startswith('do ('):
            try:
                data = cls._ast_parse_do(text)
            except Exception as e:
                logger.warning(f"AST 解析 do() 失败: {e}, 原文: {text[:200]}")
                return Action(Action.TYPE_UNKNOWN, raw=text)
            return cls._build_autoglm_action(data)

        # 简化格式: Launch("闲鱼"), Tap(500,800), Wait(2), Back() 等
        simple = cls._parse_simple_call(text)
        if simple is not None:
            return cls._build_autoglm_action(simple)

        logger.warning(f"autoglm-phone 无法解析: {text[:200]}")
        return Action(Action.TYPE_UNKNOWN, raw=text)

    @classmethod
    def _parse_simple_call(cls, text: str) -> Optional[dict]:
        """解析简化格式 ActionName(args) → dict"""
        m = re.match(r'(\w[\w\s]*?)\s*\(', text)
        if not m:
            return None

        func_name = m.group(1).strip()

        # 提取括号内的参数部分
        paren_start = m.end() - 1
        paren_depth = 0
        paren_end = len(text)
        for i in range(paren_start, len(text)):
            if text[i] == '(':
                paren_depth += 1
            elif text[i] == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    paren_end = i
                    break
        args_str = text[paren_start + 1:paren_end].strip()

        # 按 func_name 分派解析
        name_lower = func_name.lower().replace(' ', '_')

        if name_lower == 'launch':
            app = args_str.strip('"\'')
            return {"action": "Launch", "app": app}

        if name_lower in ('tap', 'click', 'long_press', 'double_tap'):
            action_map = {'tap': 'Tap', 'click': 'Tap',
                          'long_press': 'Long Press', 'double_tap': 'Double Tap'}
            coords = re.findall(r'[-+]?\d+', args_str)
            if len(coords) >= 2:
                return {"action": action_map.get(name_lower, 'Tap'),
                        "element": [int(coords[0]), int(coords[1])]}

        if name_lower == 'swipe':
            coords = re.findall(r'[-+]?\d+', args_str)
            if len(coords) >= 4:
                return {"action": "Swipe",
                        "start": [int(coords[0]), int(coords[1])],
                        "end": [int(coords[2]), int(coords[3])]}

        if name_lower in ('type', 'type_name'):
            txt = args_str.strip('"\'')
            return {"action": "Type", "text": txt}

        if name_lower == 'wait':
            nums = re.findall(r'\d+', args_str)
            dur = nums[0] if nums else "2"
            return {"action": "Wait", "duration": f"{dur} seconds"}

        if name_lower == 'back':
            return {"action": "Back"}

        if name_lower == 'home':
            return {"action": "Home"}

        if name_lower == 'take_over':
            msg = args_str.strip('"\'')
            return {"action": "Take_over", "message": msg}

        return None

    @staticmethod
    def _ast_parse_do(text: str) -> dict:
        """用 Python AST 解析 do(action="...", ...) 调用"""
        # Type/Type_Name 的 text 参数可能包含特殊字符，特殊处理
        if text.startswith('do(action="Type"') or text.startswith('do(action="Type_Name"'):
            parts = text.split("text=", 1)
            if len(parts) == 2:
                raw_text = parts[1].strip()
                if raw_text.endswith(')'):
                    raw_text = raw_text[:-1]
                raw_text = raw_text.strip('"\'')
                return {"action": "Type", "text": raw_text}

        sanitized = text.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        tree = ast.parse(sanitized, mode="eval")
        call = tree.body
        data = {}
        for keyword in call.keywords:
            key = keyword.arg
            value = ast.literal_eval(keyword.value)
            data[key] = value
        return data

    # autoglm-phone action 名称映射
    _AUTOGLM_ACTION_MAP = {
        "Launch":      Action.TYPE_LAUNCH_APP,
        "Tap":         Action.TYPE_TAP,
        "Type":        Action.TYPE_INPUT,
        "Type_Name":   Action.TYPE_INPUT,
        "Swipe":       Action.TYPE_SWIPE,
        "Back":        Action.TYPE_BACK,
        "Home":        Action.TYPE_HOME,
        "Wait":        Action.TYPE_WAIT,
        "Long Press":  Action.TYPE_LONG_PRESS,
        "Double Tap":  Action.TYPE_DOUBLE_TAP,
        "Take_over":   Action.TYPE_TAKE_OVER,
        "Note":        Action.TYPE_UNKNOWN,
        "Call_API":    Action.TYPE_UNKNOWN,
        "Interact":    Action.TYPE_UNKNOWN,
    }

    @classmethod
    def _build_autoglm_action(cls, data: dict) -> Action:
        """从 AST 解析结果构建 Action（autoglm-phone 格式）"""
        action_name = data.get("action", "")
        action_type = cls._AUTOGLM_ACTION_MAP.get(action_name, Action.TYPE_UNKNOWN)

        if action_type == Action.TYPE_LAUNCH_APP:
            a = Action(action_type, app_name=data.get("app", ""))
            return a

        if action_type in (Action.TYPE_TAP, Action.TYPE_LONG_PRESS, Action.TYPE_DOUBLE_TAP):
            element = data.get("element", [0, 0])
            if isinstance(element, (list, tuple)) and len(element) >= 2:
                x, y = int(element[0]), int(element[1])
            else:
                x, y = 0, 0
            a = Action(action_type, x=x, y=y,
                       reason=data.get("message", ""))
            a.normalized_coords = True  # 0-999 归一化坐标
            return a

        if action_type == Action.TYPE_SWIPE:
            start = data.get("start", [0, 0])
            end = data.get("end", [0, 0])
            a = Action(action_type,
                       x1=int(start[0]), y1=int(start[1]),
                       x2=int(end[0]), y2=int(end[1]))
            a.normalized_coords = True
            return a

        if action_type == Action.TYPE_INPUT:
            return Action(action_type, text=data.get("text", ""))

        if action_type == Action.TYPE_WAIT:
            dur = data.get("duration", "2")
            secs = 2
            m = re.search(r'(\d+)', str(dur))
            if m:
                secs = int(m.group(1))
            return Action(action_type, seconds=secs)

        if action_type == Action.TYPE_TAKE_OVER:
            return Action(action_type, reason=data.get("message", ""))

        if action_type in (Action.TYPE_BACK, Action.TYPE_HOME):
            return Action(action_type)

        if action_type == Action.TYPE_DONE:
            return Action(action_type, reason=data.get("message", ""))

        return Action(Action.TYPE_UNKNOWN, raw=str(data))

    # ── 通用 JSON 格式解析 (GPT-4o / GLM-4V 等) ──────────────

    _GENERIC_ACTION_MAP = {
        "launch_app": Action.TYPE_LAUNCH_APP,
        "launch":     Action.TYPE_LAUNCH_APP,
        "tap":        Action.TYPE_TAP,
        "click":      Action.TYPE_TAP,
        "swipe":      Action.TYPE_SWIPE,
        "input":      Action.TYPE_INPUT,
        "type":       Action.TYPE_INPUT,
        "back":       Action.TYPE_BACK,
        "home":       Action.TYPE_HOME,
        "wait":       Action.TYPE_WAIT,
        "done":       Action.TYPE_DONE,
        "finish":     Action.TYPE_DONE,
    }

    @classmethod
    def _parse_json_action(cls, raw: str) -> Action:
        """解析 JSON 格式的动作（通用模型）"""
        raw = re.sub(r'</?(?:answer|output|response|result|json)>', '', raw).strip()

        # 提取第一个 JSON 对象
        depth = 0
        start = -1
        json_match = None
        for i, ch in enumerate(raw):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    json_match = raw[start:i + 1]
                    break

        if not json_match:
            logger.warning(f"JSON 解析: 未找到 JSON 对象: {raw[:200]}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)

        try:
            data = json.loads(json_match)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)

        action_str = data.get('action', 'unknown').lower()
        action_type = cls._GENERIC_ACTION_MAP.get(action_str, Action.TYPE_UNKNOWN)
        reason = data.get('reason', '')

        try:
            if action_type == Action.TYPE_LAUNCH_APP:
                return Action(action_type,
                              app_name=data.get('app_name', ''),
                              package_name=data.get('package_name', ''),
                              reason=reason)
            elif action_type == Action.TYPE_TAP:
                return Action(action_type,
                              x=int(data['x']), y=int(data['y']),
                              reason=reason)
            elif action_type == Action.TYPE_SWIPE:
                return Action(action_type,
                              x1=int(data['x1']), y1=int(data['y1']),
                              x2=int(data['x2']), y2=int(data['y2']),
                              reason=reason)
            elif action_type == Action.TYPE_INPUT:
                return Action(action_type,
                              text=str(data.get('text', '')), reason=reason)
            elif action_type == Action.TYPE_WAIT:
                return Action(action_type,
                              seconds=int(data.get('seconds', 2)), reason=reason)
            elif action_type in (Action.TYPE_BACK, Action.TYPE_HOME, Action.TYPE_DONE):
                return Action(action_type, reason=reason)
            else:
                return Action(Action.TYPE_UNKNOWN, raw=raw)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"动作参数解析失败: {e}, data={data}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)


# ── OpenAI 兼容客户端 ────────────────────────────────────────

class OpenAIClient(BaseAIClient):

    def __init__(self, config: AIConfig):
        super().__init__(config)
        from openai import OpenAI
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        logger.info(f"OpenAI 客户端: base_url={config.base_url}, "
                     f"model={config.model}")

    def _call_api(self, messages: list) -> AIResponse:
        kwargs = dict(
            model=self.config.model,
            messages=messages,
            max_tokens=self.config.max_tokens,
        )

        if self.config.thinking:
            for msg in kwargs['messages']:
                if msg['role'] == 'system':
                    msg['role'] = 'user'
        else:
            kwargs['temperature'] = self.config.temperature

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        content = msg.content or ""
        thinking = ""

        reasoning = getattr(msg, 'reasoning_content', None)
        if reasoning:
            thinking = reasoning

        if not thinking and '<think>' in content:
            content, thinking = self._strip_think_tags(content)

        return AIResponse(content=content, thinking=thinking)


# ── 智谱 AI 客户端 ───────────────────────────────────────────

class ZhipuClient(BaseAIClient):

    def __init__(self, config: AIConfig):
        super().__init__(config)
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=config.api_key)
        logger.info(f"智谱 AI 客户端: model={config.model}")

    def _call_api(self, messages: list) -> AIResponse:
        patched = self._ensure_data_uri(messages)

        kwargs = dict(
            model=self.config.model,
            messages=patched,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        content = msg.content or ""
        thinking = ""

        if '<think>' in content:
            content, thinking = self._strip_think_tags(content)

        return AIResponse(content=content, thinking=thinking)

    @staticmethod
    def _ensure_data_uri(messages: list) -> list:
        import copy
        patched = copy.deepcopy(messages)
        for msg in patched:
            if not isinstance(msg.get('content'), list):
                continue
            for part in msg['content']:
                if part.get('type') != 'image_url':
                    continue
                url = part['image_url'].get('url', '')
                if url and not url.startswith(('http://', 'https://', 'data:')):
                    part['image_url']['url'] = f"data:image/jpeg;base64,{url}"
        return patched


# ── 工厂函数 ──────────────────────────────────────────────────

_PROVIDERS = {
    "openai": OpenAIClient,
    "zhipu": ZhipuClient,
}


def create_ai_client(config: AIConfig) -> BaseAIClient:
    cls = _PROVIDERS.get(config.provider)
    if cls is None:
        raise ValueError(
            f"不支持的 provider: '{config.provider}'\n"
            f"可选值: {', '.join(_PROVIDERS.keys())}"
        )
    return cls(config)
