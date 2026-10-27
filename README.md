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
| GET | `/api/models` | 可用模型列表（mock + 真实模型） |
| POST | `/api/chat` | 发起问答，写入 `chat_sessions` 与 `api_call_logs` |
| GET | `/api/history/{user_id}` | 历史查询，按时间倒序，支持 limit/offset |
| DELETE | `/api/history/{session_id}` | 软删除，重复删除幂等 |

示例：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/chat -Method Post `
  -ContentType "application/json" `
  -Body '{"user_id":1,"question":"你好","model_name":"mock"}'
```

## 目录结构

```
ai问答平台/
├── app/
│   ├── main.py                 # 应用入口：lifespan、异常处理、request_id 中间件
│   ├── api/routes.py           # 5 个核心接口
│   ├── schemas/chat.py         # Pydantic 请求/响应模型
│   ├── core/
│   │   ├── config.py           # 读取 .env
│   │   └── exceptions.py       # AppException 及全局处理器
│   ├── database/
│   │   ├── session.py          # 异步引擎、session 工厂、get_db
│   │   └── init_db.py          # 建表 + WAL + 演示用户
│   ├── models/conversation.py  # users / chat_sessions / api_call_logs
│   ├── services/
│   │   ├── llm_client.py       # 模型调用统一封装（mock + 真实）
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

首次启动由 `app/database/init_db.py` 自动建表（幂等），dev 环境额外预置
`demo_user_1`（id=1）、`demo_user_2`（id=2）用于越权测试。

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
