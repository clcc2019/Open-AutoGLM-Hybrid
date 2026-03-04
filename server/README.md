# AutoGLM Server

远程服务器端，负责 AI 推理和任务调度。

## 启动

```bash
pip install -r requirements.txt
export ZHIPU_API_KEY="your-api-key"
python main.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZHIPU_API_KEY` | (必填) | 智谱 BigModel API Key |
| `ZHIPU_MODEL` | `glm-4v-plus` | 视觉模型 |
| `SERVER_PORT` | `8000` | 监听端口 |
| `MAX_AGENT_STEPS` | `30` | 单任务最大步数 |
| `HEARTBEAT_TIMEOUT` | `90` | 心跳超时(秒) |

## API

- `GET /` - 服务器状态
- `GET /api/devices` - 已连接设备
- `POST /api/task` - 创建任务 `{"task": "打开微信"}`
- `WS /device/{device_id}` - 设备 WebSocket 连接
