"""
配置管理模块 - 读取 ~/.autoglm/config.ini
"""

import os
import configparser
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger('Config')

DEFAULT_CONFIG_PATH = os.path.expanduser('~/.autoglm/config.ini')


@dataclass
class AIConfig:
    provider: str       # "openai" | "zhipu"
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking: bool = False


@dataclass
class HelperConfig:
    url: str = "http://localhost:6443"
    mode: str = "auto"  # "auto" | "accessibility" | "ladb"


@dataclass
class AppConfig:
    ai: AIConfig
    helper: HelperConfig


PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-plus",
    },
}


def load_config(path: Optional[str] = None) -> AppConfig:
    """
    加载配置，优先级：环境变量 > 配置文件 > 默认值
    """
    path = path or DEFAULT_CONFIG_PATH
    cp = configparser.ConfigParser()

    if os.path.exists(path):
        cp.read(path, encoding='utf-8')
        logger.info(f"已加载配置文件: {path}")
    else:
        logger.warning(f"配置文件不存在: {path}，将使用环境变量或默认值")

    provider = _get(cp, 'ai', 'provider', 'AUTOGLM_PROVIDER', 'openai').lower()
    if provider not in PROVIDER_DEFAULTS:
        logger.warning(f"未知 provider '{provider}'，回退到 openai")
        provider = "openai"

    defaults = PROVIDER_DEFAULTS[provider]

    thinking_raw = _get(cp, 'ai', 'thinking', 'AUTOGLM_THINKING', 'false')
    thinking = thinking_raw.lower() in ('true', '1', 'yes', 'on')

    ai = AIConfig(
        provider=provider,
        base_url=_get(cp, 'ai', 'base_url', 'AUTOGLM_BASE_URL', defaults["base_url"]),
        api_key=_get(cp, 'ai', 'api_key', 'AUTOGLM_API_KEY', ''),
        model=_get(cp, 'ai', 'model', 'AUTOGLM_MODEL', defaults["model"]),
        max_tokens=int(_get(cp, 'ai', 'max_tokens', 'AUTOGLM_MAX_TOKENS', '4096')),
        temperature=float(_get(cp, 'ai', 'temperature', 'AUTOGLM_TEMPERATURE', '0.7')),
        thinking=thinking,
    )

    mode = _get(cp, 'helper', 'mode', 'AUTOGLM_MODE', 'auto').lower()
    if mode not in ('auto', 'accessibility', 'ladb'):
        logger.warning(f"未知 mode '{mode}'，回退到 auto")
        mode = 'auto'

    helper = HelperConfig(
        url=_get(cp, 'helper', 'url', 'AUTOGLM_HELPER_URL', 'http://localhost:6443'),
        mode=mode,
    )

    if not ai.api_key:
        raise ValueError(
            "未配置 API Key！请通过以下方式之一配置:\n"
            f"  1. 编辑 {DEFAULT_CONFIG_PATH} 的 [ai] api_key\n"
            "  2. 设置环境变量 AUTOGLM_API_KEY"
        )

    return AppConfig(ai=ai, helper=helper)


def _get(cp: configparser.ConfigParser, section: str, key: str,
         env_var: str, default: str) -> str:
    """按优先级获取配置值：环境变量 > ini 文件 > 默认值"""
    val = os.environ.get(env_var)
    if val is not None and val != '':
        return val
    try:
        return cp.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default
