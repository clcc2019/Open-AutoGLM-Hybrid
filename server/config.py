"""
服务器配置管理
"""

import os
from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000

    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "autoglm-phone"

    heartbeat_interval: int = 30  # seconds
    heartbeat_timeout: int = 90
    task_timeout: int = 300  # 5 minutes per task
    max_agent_steps: int = 30  # max steps per task

    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            host=os.getenv("SERVER_HOST", "0.0.0.0"),
            port=int(os.getenv("SERVER_PORT", "8000")),
            zhipu_api_key=os.getenv("ZHIPU_API_KEY", ""),
            zhipu_base_url=os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            zhipu_model=os.getenv("ZHIPU_MODEL", "autoglm-phone"),
            heartbeat_interval=int(os.getenv("HEARTBEAT_INTERVAL", "30")),
            heartbeat_timeout=int(os.getenv("HEARTBEAT_TIMEOUT", "90")),
            task_timeout=int(os.getenv("TASK_TIMEOUT", "300")),
            max_agent_steps=int(os.getenv("MAX_AGENT_STEPS", "30")),
        )


config = ServerConfig.from_env()
