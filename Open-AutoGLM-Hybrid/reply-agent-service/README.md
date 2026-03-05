# Reply Agent Service

基于 [Agno](https://github.com/agno-agi/agno) 框架的智能客服 Agent 服务，为闲鱼/电商平台提供自动回复能力。

## 架构

**手机端只需安装一个 APK，无需 Termux/Python。**

```
┌─────────────────────────┐          ┌──────────────────────────────┐
│   Android 手机 (APK)     │          │   服务器 (本服务)              │
│                         │  poll    │                              │
│  AutoGLM Helper APP     │ ──────→  │  /api/phone/poll             │
│  ├─ 无障碍服务 (操作手机) │          │  ├─ 视觉LLM 分析截图          │
│  ├─ AgentPoller (轮询)   │ ←──────  │  ├─ Reply Agent 生成回复      │
│  └─ 执行返回的指令       │ commands │  └─ 返回操作指令               │
│                         │          │                              │
│  用户只需:               │          │  内含:                        │
│  1. 安装 APK            │          │  ├─ RAG 知识库                │
│  2. 开启无障碍           │          │  ├─ 长期记忆                  │
│  3. 输入服务器地址       │          │  ├─ 议价策略                  │
│  4. 点击「连接」         │          │  └─ 意图分类                  │
└─────────────────────────┘          └──────────────────────────────┘
```

## 工作流程

```
1. 手机 APP 每3秒截图 + POST /api/phone/poll
2. 服务器用视觉LLM分析截图: "有新消息吗？买家说了什么？"
3. 如果有新消息 → Reply Agent 生成回复（搜索知识库 + 记忆 + 策略）
4. 服务器返回操作指令: [tap输入框, input回复文本, tap发送按钮]
5. 手机 APP 执行指令
6. 回到步骤 1
```

## 快速开始

### 1. 部署服务端

```bash
cd reply-agent-service
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# Docker 部署
docker compose up -d

# 或本地开发
pip install uv && uv pip install -e "."
uvicorn app:app --host 0.0.0.0 --port 8080
```

### 2. 手机端

1. 构建并安装 AutoGLM Helper APK（见 `android-app/BUILD_INSTRUCTIONS.md`）
2. 打开 APP → 开启无障碍服务
3. 输入服务器地址（如 `http://192.168.1.100:8080`）
4. 点击「连接 Agent」
5. 打开闲鱼聊天界面，自动回复开始工作

**手机端不需要安装 Termux、Python 或任何其他东西。**

## API 接口

### 手机轮询接口（核心）

```bash
POST /api/phone/poll
```

手机 APP 自动调用，发送截图，接收操作指令。

### 快捷回复（供其他客户端调用）

```bash
curl -X POST http://localhost:8080/api/quick-reply \
  -H "Content-Type: application/json" \
  -d '{
    "buyer_message": "能便宜点吗？",
    "buyer_id": "buyer_123",
    "product_context": "iPhone 15 Pro 256G，标价5999元"
  }'
```

### 其他接口

| 接口 | 说明 |
|------|------|
| `GET /api/health` | 健康检查 |
| `POST /api/knowledge/reload` | 重新加载知识库 |
| `POST /agents/reply-agent/runs` | AgentOS 标准接口 |
| `GET /docs` | API 文档 (Swagger) |

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `LLM_MODEL` | deepseek-chat | 回复生成模型 |
| `LLM_API_KEY` | - | LLM API Key |
| `LLM_BASE_URL` | https://api.deepseek.com/v1 | API 地址 |
| `VISION_MODEL` | (同LLM) | 截图分析模型（推荐用视觉模型） |
| `VISION_API_KEY` | (同LLM) | 视觉模型 Key |
| `VISION_BASE_URL` | (同LLM) | 视觉模型地址 |
| `EMBEDDING_MODEL` | text-embedding-3-small | Embedding 模型 |
| `DATABASE_URL` | sqlite:///reply_agent.db | 数据库 |
| `MIN_PRICE_RATIO` | 0.8 | 议价最低接受比例 |

## 知识库

将 Markdown 文件放入 `knowledge_docs/`，修改后调用 `/api/knowledge/reload`。

```
knowledge_docs/
├── products/          # 商品信息
├── policies/          # 售后/发货政策
├── templates/         # 话术模板
└── faq.md             # 常见问题
```
