"""
AutoGLM 远程服务器
架构：远程服务器处理 AI 逻辑，手机客户端通过 WebSocket 连接并执行指令
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import config
from protocol import (
    ServerMessage, ServerMessageType, ClientMessageType,
    parse_client_message, ClientMessage,
    ScreenshotResult, ActionResult, Heartbeat, DeviceInfo,
    ServerError,
)
from agent import PhoneAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AutoGLM Server", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DeviceConnection:
    def __init__(self, device_id: str, websocket: WebSocket):
        self.device_id = device_id
        self.websocket = websocket
        self.last_heartbeat = time.time()
        self.device_info: Optional[DeviceInfo] = None
        self.pending_responses: Dict[str, asyncio.Future] = {}
        self.current_task_id: Optional[str] = None

    async def send(self, msg: ServerMessage):
        await self.websocket.send_text(msg.model_dump_json())

    def set_response(self, request_id: str, message: ClientMessage):
        future = self.pending_responses.get(request_id)
        if future and not future.done():
            future.set_result(message)

    async def wait_response(self, request_id: str, timeout: float = 30.0) -> Optional[ClientMessage]:
        future = asyncio.get_event_loop().create_future()
        self.pending_responses[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[{self.device_id}] 等待响应超时: {request_id}")
            return None
        finally:
            self.pending_responses.pop(request_id, None)


devices: Dict[str, DeviceConnection] = {}
task_counter = 0


class TaskRequest(BaseModel):
    task: str
    device_id: Optional[str] = None


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


# --- HTTP API ---

@app.get("/")
async def root():
    return {
        "name": "AutoGLM Server",
        "version": "2.0.0",
        "connected_devices": list(devices.keys()),
    }


@app.get("/api/devices")
async def get_devices():
    result = []
    for did, conn in devices.items():
        info = {
            "device_id": did,
            "last_heartbeat": conn.last_heartbeat,
            "current_task": conn.current_task_id,
        }
        if conn.device_info:
            info["model"] = conn.device_info.model
            info["screen"] = f"{conn.device_info.screen_width}x{conn.device_info.screen_height}"
        result.append(info)
    return {"devices": result, "count": len(result)}


@app.post("/api/task", response_model=TaskResponse)
async def create_task(request: TaskRequest):
    global task_counter

    device_id = request.device_id
    if not device_id and devices:
        device_id = next(iter(devices))

    if not device_id or device_id not in devices:
        raise HTTPException(status_code=404, detail="没有可用的设备连接")

    conn = devices[device_id]
    if conn.current_task_id:
        raise HTTPException(status_code=409, detail=f"设备正在执行任务: {conn.current_task_id}")

    task_counter += 1
    task_id = f"task_{task_counter:04d}"

    asyncio.create_task(_run_agent_task(conn, task_id, request.task))

    return TaskResponse(
        task_id=task_id,
        status="started",
        message=f"任务已下发给设备 {device_id}",
    )


async def _run_agent_task(conn: DeviceConnection, task_id: str, task: str):
    conn.current_task_id = task_id
    try:
        agent = PhoneAgent()
        await agent.run_task(
            task=task,
            task_id=task_id,
            send_to_device=conn.send,
            wait_for_response=conn.wait_response,
        )
    except Exception as e:
        logger.error(f"[{task_id}] Agent 异常: {e}", exc_info=True)
        try:
            await conn.send(ServerError(message=f"Agent 异常: {e}"))
        except Exception:
            pass
    finally:
        conn.current_task_id = None


# --- WebSocket ---

@app.websocket("/device/{device_id}")
async def device_websocket(websocket: WebSocket, device_id: str):
    await websocket.accept()

    if device_id in devices:
        old = devices[device_id]
        logger.info(f"设备 {device_id} 重新连接，关闭旧连接")
        try:
            await old.websocket.close(code=4001, reason="replaced")
        except Exception:
            pass
        for future in old.pending_responses.values():
            if not future.done():
                future.cancel()

    conn = DeviceConnection(device_id, websocket)
    devices[device_id] = conn
    logger.info(f"设备 {device_id} 已连接")

    try:
        await conn.send(ServerMessage(type=ServerMessageType.CONNECTED, request_id="init"))

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[{device_id}] 无效 JSON: {raw[:200]}")
                continue

            msg = parse_client_message(data)
            if msg is None:
                logger.warning(f"[{device_id}] 无法解析消息: {data.get('type')}")
                continue

            if isinstance(msg, Heartbeat):
                conn.last_heartbeat = time.time()
                await conn.send(ServerMessage(
                    type=ServerMessageType.HEARTBEAT_ACK,
                    request_id=msg.request_id,
                ))

            elif isinstance(msg, DeviceInfo):
                conn.device_info = msg
                logger.info(f"[{device_id}] 设备信息: {msg.model} {msg.screen_width}x{msg.screen_height}")

            elif isinstance(msg, (ScreenshotResult, ActionResult)):
                conn.set_response(msg.request_id, msg)

            else:
                logger.debug(f"[{device_id}] 未处理消息类型: {msg.type}")

    except WebSocketDisconnect:
        logger.info(f"设备 {device_id} 断开连接")
    except Exception as e:
        logger.error(f"设备 {device_id} 连接异常: {e}")
    finally:
        if devices.get(device_id) is conn:
            del devices[device_id]
        for future in conn.pending_responses.values():
            if not future.done():
                future.cancel()


# --- 心跳检测 ---

async def heartbeat_checker():
    while True:
        await asyncio.sleep(config.heartbeat_interval)
        now = time.time()
        stale = [
            did for did, conn in devices.items()
            if now - conn.last_heartbeat > config.heartbeat_timeout
        ]
        for did in stale:
            logger.warning(f"设备 {did} 心跳超时，断开连接")
            conn = devices.pop(did, None)
            if conn:
                try:
                    await conn.websocket.close()
                except Exception:
                    pass


@app.on_event("startup")
async def startup():
    asyncio.create_task(heartbeat_checker())
    logger.info(f"AutoGLM Server 启动: {config.host}:{config.port}")
    if not config.zhipu_api_key:
        logger.warning("ZHIPU_API_KEY 未配置，AI 功能不可用")


@app.on_event("shutdown")
async def shutdown():
    for conn in devices.values():
        try:
            await conn.websocket.close()
        except Exception:
            pass
    logger.info("服务器已关闭")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.host, port=config.port)
