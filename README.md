# AI 智能问答平台

Day19-21 阶段综合项目实战（一）交付：FastAPI + SQLite 的 AI 问答最小闭环，
支持多模型选择、问答落库、历史查询与软删除。

## 快速开始

```powershell
# 1. 创建虚拟环境（已存在可跳过）
python -m venv .venv

# 2. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 配置环境变量
Copy-Item .env.example .env
#    编辑 .env：填入 MODEL_API_KEY（不填也能跑 mock 链路）

# 4. 启动（首次启动自动建表并预置两个演示用户）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/models` | 可用模型列表，可选 `user_id` 返回该用户可见模型 |
| POST | `/api/models` | 用户自建模型配置（DeepSeek / 本地 / 自定义） |
| PATCH | `/api/models/{config_id}` | 修改本人自建配置（内置模型返回 403） |
| DELETE | `/api/models/{config_id}` | 删除本人自建配置（内置模型返回 403） |
| POST | `/api/models/{config_id}/test` | 连通性测试，返回耗时与回答预览 |
| POST | `/api/chat` | 发起问答，写入 `chat_sessions` 与 `api_call_logs` |
| GET | `/api/history/{user_id}` | 历史查询，按时间倒序，支持 limit/offset |
| DELETE | `/api/history/{session_id}` | 软删除，重复删除幂等 |

示例：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/chat -Method Post `
  -ContentType "application/json" `
  -Body '{"user_id":1,"question":"你好","model_name":"mock"}'
```

## 模型切换与自建配置

内置三款模型由 `app/services/model_config_service.py` 的 `seed_builtin()` 在每次启动时
从 `.env` 同步到 `model_configs` 表（`owner_user_id` 为空）：

| model_name | provider | 说明 |
|------------|----------|------|
| `mock` | mock | 本地联调，不联网、不耗 Token |
| `deepseek-chat` | deepseek | 读 `.env` 的 `MODEL_BASE_URL` / `MODEL_API_KEY` |
| `local` | local | 读 `LOCAL_MODEL_BASE_URL` / `LOCAL_MODEL_NAME`，默认 Ollama `http://127.0.0.1:11434/v1` |

用户在 `/api/chat` 里传 `model_name` 即可自由切换；还可以通过 `POST /api/models`
自建配置（同用户下 `name` 唯一，重名返回 409），自建配置仅本人可见可用。

```powershell
# 接入本机 Ollama（需先启动 ollama serve 并 pull 模型）
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/models -Method Post `
  -ContentType "application/json" `
  -Body '{"user_id":1,"name":"my-ollama","display_name":"我的 Ollama","provider":"local","base_url":"http://127.0.0.1:11434/v1","upstream_model":"qwen2.5:7b","api_key":""}'
```

- `api_key` 落库前用 AES-256-GCM 加密（密钥为 `.env` 的 `MASTER_ENCRYPTION_KEY`），
  任何接口响应只返回 `has_api_key`，不回显明文
- 修改 `MASTER_ENCRYPTION_KEY` 会导致旧密文无法解密，需重新填写 Key
- 内置模型不允许修改/删除，改地址或 Key 请改 `.env` 后重启
- 本地模型 connect 超时 5s、推理超时 60s；远程模型默认 10s

## 目录结构

```
ai问答平台/
├── app/
│   ├── main.py                 # 应用入口：lifespan、异常处理、request_id 中间件
│   ├── api/routes.py           # 9 个接口（5 核心 + 4 模型配置）
│   ├── schemas/
│   │   ├── chat.py             # 问答请求/响应模型
│   │   └── model_config.py     # 模型配置请求/响应模型
│   ├── core/
│   │   ├── config.py           # 读取 .env
│   │   ├── crypto.py           # API Key AES-256-GCM 加解密
│   │   └── exceptions.py       # AppException 及全局处理器
│   ├── database/
│   │   ├── session.py          # 异步引擎、session 工厂、get_db
│   │   └── init_db.py          # 建表 + WAL + 演示用户 + 内置模型同步
│   ├── models/
│   │   ├── conversation.py     # users / chat_sessions / api_call_logs
│   │   └── model_config.py     # model_configs
│   ├── services/
│   │   ├── llm_client.py       # 模型调用统一封装（mock + OpenAI 兼容）
│   │   ├── model_config_service.py # 模型配置 CRUD、解析、连通性测试
│   │   ├── chat_service.py     # 问答编排 + 调用日志
│   │   ├── history_service.py  # 历史查询与软删除
│   │   └── user_service.py     # 用户识别与归属校验
│   └── ai/                     # Provider / RAG / 敏感词（P2 预留）
├── docs/
│   ├── 需求说明.md
│   └── 接口测试记录.md
├── data/                       # app.db、chroma 向量库
├── uploads/                    # 上传文件
├── alembic/                    # 数据库迁移（后续迭代启用）
├── requirements.txt
├── .env.example
└── README.md
```

## 数据表

| 表 | 用途 |
|----|------|
| `users` | 用户身份，支撑 user_id 会话隔离 |
| `chat_sessions` | 每轮问答历史（question/answer/model_name/软删除标记） |
| `api_call_logs` | 每次模型调用的模型、耗时、状态、错误信息 |
| `model_configs` | 内置与用户自建模型配置，api_key 加密存储 |

首次启动由 `app/database/init_db.py` 自动建表（幂等），dev 环境额外预置
`demo_user_1`（id=1）、`demo_user_2`（id=2）用于越权测试，并同步三款内置模型。

## 常见问题

**1. `Activate.ps1` 报 PSSecurityException**
PowerShell 默认禁止运行未签名脚本。直接用 `.\.venv\Scripts\python.exe` 指定解释器即可，无需激活。

**2. 真实模型返回 502「模型 API Key 未配置」**
在 `.env` 设置 `MODEL_API_KEY`、`MODEL_BASE_URL`、`DEFAULT_MODEL` 后重启服务。
mock 模型不依赖网络和 Key，本地联调优先用 mock。

**3. `weasyprint` import 失败（Windows）**
缺 GTK 运行时，只影响 PDF 导出（P2）。可安装 GTK3 Runtime，或在 P2 阶段改用 Playwright 打印。

**4. 历史查询返回 403**
请求头 `X-User-Id` 与路径 `user_id` 不一致。本期用它模拟登录态，接入 JWT 后自动生效。

**5. 想重置数据**
停止服务后删除 `data/app.db*`，重启会自动重建表并重新预置演示用户。
