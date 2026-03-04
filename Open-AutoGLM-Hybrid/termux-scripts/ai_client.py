"""
统一 AI 客户端 - 支持 OpenAI 兼容 API 和智谱 AI

两种 provider:
  - openai: 任何兼容 OpenAI Chat Completions API 的服务
            (OpenAI / Azure / DeepSeek / Moonshot / 本地 Ollama 等)
  - zhipu:  智谱 AI，使用官方 zhipuai SDK（支持 GLM-4V 视觉模型）

思考模式:
  config.thinking = true 时，自动适配:
  - OpenAI o-系列: 读取 message.reasoning_content
  - DeepSeek-R1 等: 剥离 <think>...</think> 标签
  - 通用: 先尝试 reasoning_content 字段，再 fallback 到 <think> 标签
"""

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
RETRY_DELAY = 2  # 秒


# ── 动作数据结构 ──────────────────────────────────────────────

class Action:
    """AI 返回的操作指令"""

    TYPE_TAP = "tap"
    TYPE_SWIPE = "swipe"
    TYPE_INPUT = "input"
    TYPE_LAUNCH_APP = "launch_app"
    TYPE_BACK = "back"
    TYPE_HOME = "home"
    TYPE_WAIT = "wait"
    TYPE_DONE = "done"
    TYPE_UNKNOWN = "unknown"

    def __init__(self, action_type: str, **kwargs):
        self.action_type = action_type
        self.params = kwargs

    def __repr__(self):
        return f"Action({self.action_type}, {self.params})"


@dataclass
class AIResponse:
    """AI 回复，包含思考过程和最终内容"""
    content: str
    thinking: str = ""


# ── System Prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个手机操作助手。用户会给你一张手机截图和一个任务描述。
你需要分析截图内容，决定下一步操作。

请以 JSON 格式返回操作指令，格式如下:
- 打开应用: {"action": "launch_app", "app_name": "淘宝", "reason": "打开淘宝"}
  支持的应用名: 淘宝/闲鱼/京东/微信/支付宝/抖音/快手/微博/小红书/美团/饿了么/拼多多/高德地图/百度地图/QQ/哔哩哔哩/B站/QQ音乐/网易云音乐
  也可以用包名: {"action": "launch_app", "package_name": "com.example.app", "reason": "打开应用"}
- 点击: {"action": "tap", "x": 500, "y": 800, "reason": "点击搜索框"}
- 滑动: {"action": "swipe", "x1": 500, "y1": 1500, "x2": 500, "y2": 500, "reason": "向上滑动"}
- 输入: {"action": "input", "text": "蓝牙耳机", "reason": "输入搜索关键词"}
- 返回: {"action": "back", "reason": "返回上一页"}
- 回到桌面: {"action": "home", "reason": "回到桌面"}
- 等待: {"action": "wait", "seconds": 2, "reason": "等待页面加载"}
- 完成: {"action": "done", "reason": "任务已完成"}

重要规则:
- 如果任务要求打开某个应用，优先使用 launch_app 而不是在桌面上找图标点击
- 只返回一个 JSON 对象，不要返回其他内容"""


# ── 基类 ──────────────────────────────────────────────────────

class BaseAIClient(ABC):
    """AI 客户端基类"""

    def __init__(self, config: AIConfig):
        self.config = config

    @abstractmethod
    def _call_api(self, messages: list) -> AIResponse:
        """调用模型 API，返回 AIResponse"""
        ...

    def decide_action(self, task: str, image: Image.Image,
                      history: Optional[list] = None) -> tuple[Action, str]:
        """
        根据截图和任务决定下一步操作

        Returns:
            (action, thinking_text)
        """
        b64 = self._image_to_base64(image)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"任务: {task}"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                    },
                },
            ],
        })

        resp = self._call_with_retry(messages)
        logger.debug(f"模型返回 content: {resp.content[:300]}")
        if resp.thinking:
            logger.debug(f"模型思考过程: {resp.thinking[:300]}")

        action = self._parse_action(resp.content)
        return action, resp.thinking

    def _call_with_retry(self, messages: list) -> AIResponse:
        """带重试的 API 调用"""
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
        """
        分离 <think>...</think> 标签中的思考内容和正式回复
        Returns: (content, thinking)
        """
        pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
        thinking_parts = pattern.findall(text)
        thinking = "\n".join(thinking_parts).strip()
        content = pattern.sub('', text).strip()
        return content, thinking

    @staticmethod
    def _parse_action(raw: str) -> Action:
        """从模型回复中提取 JSON 并解析为 Action"""
        # 移除模型可能包裹的 XML 标签（如 <answer>...</answer>、<output>...</output>）
        raw = re.sub(r'</?(?:answer|output|response|result|json)>', '', raw).strip()

        json_match = None
        depth = 0
        start = -1
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
            logger.warning(f"无法从模型回复中提取 JSON: {raw[:200]}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)

        try:
            data = json.loads(json_match)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}, 原文: {json_match[:200]}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)

        action_type = data.get('action', 'unknown')
        reason = data.get('reason', '')

        try:
            if action_type == 'launch_app':
                return Action(Action.TYPE_LAUNCH_APP,
                              app_name=data.get('app_name', ''),
                              package_name=data.get('package_name', ''),
                              reason=reason)
            elif action_type == 'tap':
                return Action(Action.TYPE_TAP,
                              x=int(data['x']), y=int(data['y']),
                              reason=reason)
            elif action_type == 'swipe':
                return Action(Action.TYPE_SWIPE,
                              x1=int(data['x1']), y1=int(data['y1']),
                              x2=int(data['x2']), y2=int(data['y2']),
                              reason=reason)
            elif action_type == 'input':
                return Action(Action.TYPE_INPUT,
                              text=str(data.get('text', '')), reason=reason)
            elif action_type == 'back':
                return Action(Action.TYPE_BACK, reason=reason)
            elif action_type == 'home':
                return Action(Action.TYPE_HOME, reason=reason)
            elif action_type == 'wait':
                return Action(Action.TYPE_WAIT,
                              seconds=int(data.get('seconds', 2)),
                              reason=reason)
            elif action_type == 'done':
                return Action(Action.TYPE_DONE, reason=reason)
            else:
                return Action(Action.TYPE_UNKNOWN, raw=raw)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"动作参数解析失败: {e}, data={data}")
            return Action(Action.TYPE_UNKNOWN, raw=raw)


# ── OpenAI 兼容客户端 ────────────────────────────────────────

class OpenAIClient(BaseAIClient):
    """
    适用于所有兼容 OpenAI Chat Completions API 的服务:
    OpenAI / Azure / DeepSeek / Moonshot / Ollama / vLLM 等
    """

    def __init__(self, config: AIConfig):
        super().__init__(config)
        from openai import OpenAI
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        logger.info(f"OpenAI 兼容客户端已初始化 "
                     f"(base_url={config.base_url}, model={config.model}, "
                     f"thinking={config.thinking})")

    def _call_api(self, messages: list) -> AIResponse:
        kwargs = dict(
            model=self.config.model,
            messages=messages,
            max_tokens=self.config.max_tokens,
        )

        if self.config.thinking:
            # o-系列等推理模型不支持 temperature / system role
            # 将 system 消息转为 user 消息
            for msg in kwargs['messages']:
                if msg['role'] == 'system':
                    msg['role'] = 'user'
        else:
            kwargs['temperature'] = self.config.temperature

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        content = msg.content or ""
        thinking = ""

        # 方式1: reasoning_content 字段 (OpenAI o-系列 / DeepSeek-R1 API)
        reasoning = getattr(msg, 'reasoning_content', None)
        if reasoning:
            thinking = reasoning

        # 方式2: <think> 标签 (DeepSeek-R1 通过第三方兼容 API)
        if not thinking and '<think>' in content:
            content, thinking = self._strip_think_tags(content)

        return AIResponse(content=content, thinking=thinking)


# ── 智谱 AI 客户端 ───────────────────────────────────────────

class ZhipuClient(BaseAIClient):
    """
    智谱 AI 客户端，使用官方 zhipuai SDK
    支持 GLM-4V / GLM-4V-Plus / GLM-4 等模型
    """

    def __init__(self, config: AIConfig):
        super().__init__(config)
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=config.api_key)
        logger.info(f"智谱 AI 客户端已初始化 "
                     f"(model={config.model}, thinking={config.thinking})")

    def _call_api(self, messages: list) -> AIResponse:
        # 智谱视觉模型的 image_url 需要完整的 data URI
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

        if not thinking and '<think>' in content:
            content, thinking = self._strip_think_tags(content)

        return AIResponse(content=content, thinking=thinking)

    @staticmethod
    def _ensure_data_uri(messages: list) -> list:
        """确保 image_url.url 带有 data URI 前缀（智谱要求）"""
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
    """根据 config.provider 创建对应的 AI 客户端"""
    cls = _PROVIDERS.get(config.provider)
    if cls is None:
        raise ValueError(
            f"不支持的 provider: '{config.provider}'\n"
            f"可选值: {', '.join(_PROVIDERS.keys())}"
        )
    return cls(config)
