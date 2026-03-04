"""
WebSocket 通信协议定义

服务器与手机客户端之间的所有消息格式。
"""

from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel
import time
import uuid


class ServerMessageType(str, Enum):
    CONNECTED = "connected"
    SCREENSHOT_REQUEST = "screenshot_request"
    TAP = "tap"
    SWIPE = "swipe"
    INPUT = "input"
    BACK = "back"
    HOME = "home"
    LAUNCH_APP = "launch_app"
    LONG_PRESS = "long_press"
    DOUBLE_TAP = "double_tap"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    HEARTBEAT_ACK = "heartbeat_ack"
    ERROR = "error"


class ClientMessageType(str, Enum):
    SCREENSHOT_RESULT = "screenshot_result"
    ACTION_RESULT = "action_result"
    HEARTBEAT = "heartbeat"
    DEVICE_INFO = "device_info"
    ERROR = "error"


class ServerMessage(BaseModel):
    type: ServerMessageType
    request_id: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]


class ScreenshotRequest(ServerMessage):
    type: ServerMessageType = ServerMessageType.SCREENSHOT_REQUEST


class TapCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.TAP
    x: int
    y: int


class LongPressCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.LONG_PRESS
    x: int
    y: int
    duration: int = 1000


class DoubleTapCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.DOUBLE_TAP
    x: int
    y: int


class SwipeCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.SWIPE
    x1: int
    y1: int
    x2: int
    y2: int
    duration: int = 500


class InputCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.INPUT
    text: str


class BackCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.BACK


class HomeCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.HOME


class LaunchAppCommand(ServerMessage):
    type: ServerMessageType = ServerMessageType.LAUNCH_APP
    package_name: str = ""
    app_name: str = ""


class TaskStarted(ServerMessage):
    type: ServerMessageType = ServerMessageType.TASK_STARTED
    task_id: str
    task: str


class TaskCompleted(ServerMessage):
    type: ServerMessageType = ServerMessageType.TASK_COMPLETED
    task_id: str
    summary: str = ""


class TaskFailed(ServerMessage):
    type: ServerMessageType = ServerMessageType.TASK_FAILED
    task_id: str
    reason: str


class ServerError(ServerMessage):
    type: ServerMessageType = ServerMessageType.ERROR
    message: str


class ClientMessage(BaseModel):
    type: ClientMessageType
    request_id: str = ""


class ScreenshotResult(ClientMessage):
    type: ClientMessageType = ClientMessageType.SCREENSHOT_RESULT
    success: bool
    image: str = ""  # Base64 encoded PNG
    error: str = ""
    width: int = 0
    height: int = 0


class ActionResult(ClientMessage):
    type: ClientMessageType = ClientMessageType.ACTION_RESULT
    success: bool
    error: str = ""


class Heartbeat(ClientMessage):
    type: ClientMessageType = ClientMessageType.HEARTBEAT
    device_id: str
    timestamp: int = 0

    def model_post_init(self, __context: Any) -> None:
        if not self.timestamp:
            self.timestamp = int(time.time())


class DeviceInfo(ClientMessage):
    type: ClientMessageType = ClientMessageType.DEVICE_INFO
    device_id: str
    model: str = ""
    android_version: str = ""
    screen_width: int = 0
    screen_height: int = 0


def parse_client_message(data: dict) -> Optional[ClientMessage]:
    msg_type = data.get("type")
    try:
        if msg_type == ClientMessageType.SCREENSHOT_RESULT:
            return ScreenshotResult(**data)
        elif msg_type == ClientMessageType.ACTION_RESULT:
            return ActionResult(**data)
        elif msg_type == ClientMessageType.HEARTBEAT:
            return Heartbeat(**data)
        elif msg_type == ClientMessageType.DEVICE_INFO:
            return DeviceInfo(**data)
        elif msg_type == ClientMessageType.ERROR:
            return ClientMessage(**data)
    except Exception:
        pass
    return None
