"""Agent API Client — calls the remote Agno Reply Agent service.

Used by Open-AutoGLM to get intelligent replies for buyer messages
instead of relying on the local LLM for reply generation.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger("AgentAPI")

DEFAULT_TIMEOUT = 30


@dataclass
class AgentReply:
    reply: str
    session_id: str
    success: bool
    error: str = ""


class AgentAPIClient:
    """Client for the Agno Reply Agent Service."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url.rstrip("/")
        self._check_health()

    def _check_health(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    "Agent API connected: %s (model: %s)",
                    data.get("agent"), data.get("model"),
                )
                return True
        except Exception as e:
            logger.warning("Agent API health check failed: %s", e)
        return False

    def get_reply(
        self,
        buyer_message: str,
        buyer_id: str = "anonymous",
        session_id: str = "",
        product_context: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> AgentReply:
        """Get an intelligent reply from the Agent service.

        Args:
            buyer_message: The buyer's message text.
            buyer_id: Unique identifier for the buyer (for memory).
            session_id: Session ID (auto-generated if empty).
            product_context: Optional product info for context.
            timeout: Request timeout in seconds.

        Returns:
            AgentReply with the generated reply text.
        """
        payload = {
            "buyer_message": buyer_message,
            "buyer_id": buyer_id,
            "session_id": session_id,
            "product_context": product_context,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/api/quick-reply",
                json=payload,
                timeout=timeout,
            )

            if resp.status_code == 200:
                data = resp.json()
                return AgentReply(
                    reply=data["reply"],
                    session_id=data["session_id"],
                    success=True,
                )
            else:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.error("Agent API error: %s", error_msg)
                return AgentReply(reply="", session_id="", success=False, error=error_msg)

        except requests.Timeout:
            logger.error("Agent API timeout after %ds", timeout)
            return AgentReply(reply="", session_id="", success=False, error="timeout")
        except Exception as e:
            logger.error("Agent API request failed: %s", e)
            return AgentReply(reply="", session_id="", success=False, error=str(e))

    def get_reply_via_agentos(
        self,
        buyer_message: str,
        buyer_id: str = "anonymous",
        session_id: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> AgentReply:
        """Alternative: call the standard AgentOS run endpoint.

        Use this if you want the full AgentOS session management.
        """
        sid = session_id or f"xianyu-{buyer_id}"
        payload = {
            "message": f"买家消息: {buyer_message}",
            "user_id": buyer_id,
            "session_id": sid,
            "stream": False,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/agents/reply-agent/runs",
                data=payload,
                timeout=timeout,
            )

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", "")
                return AgentReply(reply=content, session_id=sid, success=True)
            else:
                error_msg = f"HTTP {resp.status_code}"
                return AgentReply(reply="", session_id=sid, success=False, error=error_msg)

        except Exception as e:
            logger.error("AgentOS run failed: %s", e)
            return AgentReply(reply="", session_id=sid, success=False, error=str(e))
