# Open-AutoGLM Remote

> 远程服务器 + 手机客户端架构，AI 驱动的手机自动化

---

## 项目简介

**Open-AutoGLM Remote** 采用"远程服务器 + 手机客户端"架构：

- **服务器端**（Linux VPS）：运行 AI Agent，调用智谱 BigModel 视觉模型分析屏幕截图，决策下一步操作
- **手机端**（Android APP）：通过 WebSocket 连接服务器，接收指令并通过无障碍服务执行点击、滑动、输入等操作

手机主动连接公网服务器，无需内网穿透，无需 USB 连接。

## 工作原理

```
用户 (Web/API)
    ↓ POST /api/task
远程服务器 (Python FastAPI)
    ↓ 循环：截图 → AI 分析 → 下发指令
    ↓ WebSocket
手机 APP (Android)
    ↓ 无障碍服务执行操作
手机屏幕
```

每个任务的执行循环：
1. 服务器请求手机截图
2. 手机通过无障碍服务截图，Base64 编码后上传
3. 服务器将截图发送给智谱视觉模型分析
4. AI 返回下一步操作（点击坐标、滑动、输入文字等）
5. 服务器将操作指令下发给手机
6. 手机执行操作并返回结果
7. 重复 1-6 直到任务完成

## 快速开始

### 服务器端部署

```bash
cd server
pip install -r requirements.txt

# 配置智谱 API Key
export ZHIPU_API_KEY="your-api-key"

# 启动服务器
python main.py
```

服务器默认监听 `0.0.0.0:8000`。

### 手机端

1. 安装 AutoGLM APK（从 GitHub Releases 下载或自行构建）
2. 开启无障碍服务：设置 → 无障碍 → AutoGLM → 开启
3. 在 APP 中输入服务器地址（如 `ws://your-server-ip:8000`）
4. 点击"连接服务器"

### 下发任务

```bash
curl -X POST http://your-server-ip:8000/api/task \
  -H "Content-Type: application/json" \
  -d '{"task": "打开微信，找到文件传输助手，发送你好"}'
```

## 项目结构

```
├── server/                      # 远程服务器
│   ├── main.py                  # FastAPI 主程序（WebSocket + HTTP API）
│   ├── agent.py                 # AI Agent（截图分析、操作决策循环）
│   ├── protocol.py              # WebSocket 通信协议定义
│   ├── config.py                # 配置管理
│   └── requirements.txt
│
├── android-app/                 # Android 客户端
│   └── app/src/main/java/com/autoglm/helper/
│       ├── AutoGLMAccessibilityService.kt  # 无障碍服务（点击/滑动/输入/截图）
│       ├── HttpServer.kt                   # 本地 HTTP 调试接口
│       ├── WebSocketClient.kt              # WebSocket 客户端（连接服务器）
│       ├── CommandExecutor.kt              # 指令执行器
│       └── MainActivity.kt                 # 主界面
│
└── docs/                        # 文档
```

## 服务器 API

### HTTP 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务器状态 |
| GET | `/api/devices` | 已连接设备列表 |
| POST | `/api/task` | 创建任务 |

### WebSocket

手机通过 `ws://server:8000/device/{device_id}` 连接。

## 配置

服务器通过环境变量配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZHIPU_API_KEY` | (必填) | 智谱 BigModel API Key |
| `ZHIPU_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | API 地址 |
| `ZHIPU_MODEL` | `glm-4v-plus` | 视觉模型名称 |
| `SERVER_HOST` | `0.0.0.0` | 监听地址 |
| `SERVER_PORT` | `8000` | 监听端口 |
| `MAX_AGENT_STEPS` | `30` | 单任务最大步数 |

## 构建 Android APK

```bash
cd android-app
./gradlew assembleDebug
# APK 位于 app/build/outputs/apk/debug/
```

## 许可证

MIT License

## 致谢

- [Open-AutoGLM](https://github.com/zai-org/Open-AutoGLM)
- [NanoHTTPD](https://github.com/NanoHttpd/nanohttpd)
- [OkHttp](https://square.github.io/okhttp/)
