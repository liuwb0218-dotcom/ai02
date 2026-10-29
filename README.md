# AI 智能问答平台

Day19-21 阶段综合项目实战（一）交付：FastAPI + SQLite 的 AI 问答平台，
支持注册登录、三层权限（super_admin / admin / user）、管理员审核、公告、
多模型选择（含本地 Ollama、token 用量统计）、问答落库、历史查询与软删除，
以及问答页「使用统计」、个人中心「我的数据」和管理后台「调用日志」。

## 快速开始

```powershell
# 1. 创建虚拟环境（已存在可跳过）
python -m venv .venv

# 2. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 配置环境变量
Copy-Item .env.example .env
#    编辑 .env：填入 MODEL_API_KEY（不填也能跑 mock 链路）
#    上线前务必修改 JWT_SECRET / MASTER_ENCRYPTION_KEY / SUPER_ADMIN_PASSWORD

# 4. 执行数据库迁移（已有 data/app.db 时必须执行，补登录相关字段）
.\.venv\Scripts\python.exe -m alembic upgrade head

# 5. 启动
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

- 登录/注册：http://127.0.0.1:8000/login
- 问答页：http://127.0.0.1:8000/
- 我的数据：http://127.0.0.1:8000/me （普通用户个人中心）
- 管理后台：http://127.0.0.1:8000/admin （仅 admin / super_admin）
- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## 账号与审核

首次启动会自动创建超级管理员（取 `.env` 的 `SUPER_ADMIN_USERNAME` /
`SUPER_ADMIN_PASSWORD`，默认 `superadmin` / `Admin@123456`）。
dev 环境另外预置 `demo_user_1`、`demo_user_2`，密码均为 `demo1234`。

| 角色 | 权限 |
|------|------|
| `super_admin` | 全部权限；可在管理后台任命/撤销普通管理员，可操作管理员，查看「分组」页；不能被禁用 |
| `admin` | 查看本组用户、审核/启用/禁用本组用户、管理公告、查看本组日志；不能操作超级管理员、不能禁用自己、不能任命管理员、不能看分组 |
| `user` | 使用问答、历史、模型管理；只能访问自己的数据 |

分组规则：普通用户注册时按注册顺序轮询归属到各管理员（第 1 个给第 1 个管理员，循环），
落库在 `users.managed_by`；普通管理员的用户列表与日志只限本组。管理员被撤销/禁用时，
组内用户转给当前人数最少的管理员，无人可接则置为未分配（存量不重排，新人补位）。
超管还可在用户审核表的「归属」列直接下拉调整单个用户的分组。

注册流程与默认规则：

1. 注册成功后立即获得 `REGISTER_TRIAL_DAYS`（默认 7）天临时使用权，状态为 `pending`
2. 试用期内可正常登录、问答、查看历史
3. 管理员审核通过后状态变为 `active`，`trial_expires_at` 清空，永久可用
4. 试用到期仍未审核：仍可登录查看历史，但 `/api/chat` 返回 `403 TRIAL_EXPIRED`
5. 被禁用（`disabled`）后无法登录；禁用会立即吊销全部 Token
6. 设置 `AUTO_APPROVE_REGISTER=true` 可跳过审核（仅建议本地演示使用）

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/auth/register` | 注册并自动登录，默认进入 7 天试用 |
| POST | `/api/auth/login` | 用户名 + 密码登录 |
| POST | `/api/auth/refresh` | 刷新双 Token（旧 refresh 立即作废） |
| POST | `/api/auth/logout` | 登出当前设备；`all_devices=true` 时全端下线 |
| POST | `/api/auth/change-password` | 修改密码，成功后全部设备需重新登录 |
| GET | `/api/auth/me` | 当前用户信息与试用状态 |
| GET | `/api/models` | 内置模型 + 当前登录用户的自建模型 |
| POST | `/api/models` | 用户自建模型配置（DeepSeek / 本地 / 自定义） |
| PATCH | `/api/models/{config_id}` | 修改本人自建配置（内置模型返回 403） |
| DELETE | `/api/models/{config_id}` | 删除本人自建配置（内置模型返回 403） |
| POST | `/api/models/{config_id}/test` | 连通性测试，返回耗时与回答预览 |
| POST | `/api/chat` | 发起问答，`user_id` 以登录 Token 为准 |
| GET | `/api/history/{user_id}` | 历史查询，只能查自己；支持 limit/offset |
| DELETE | `/api/history/{session_id}` | 软删除，重复删除幂等 |
| GET | `/api/admin/users` | 用户列表 + 统计，支持 status/keyword/managed_by/page/page_size（普通管理员仅本组） |
| PATCH | `/api/admin/users/{id}/status` | 审核通过 / 拒绝 / 启用 / 禁用 |
| PATCH | `/api/admin/users/{id}/role` | 仅超管：任命 / 撤销普通管理员（`{"role":"admin" \| "user"}`） |
| PATCH | `/api/admin/users/{id}/manager` | 仅超管：调整普通用户归属管理员（`{"managed_by":4}`，null=未分配） |
| POST | `/api/admin/users/batch-review` | 批量通过或拒绝 |
| GET | `/api/announcements/pending` | 当前用户可见且未读的公告 |
| POST | `/api/announcements/{id}/read` | 记录已读（首次时间 + 累计次数） |
| GET | `/api/logs/summary` | 本人近 N 天调用汇总（days 默认 30） |
| GET | `/api/logs` | 本人调用明细，支持 status/model_name/page/page_size |
| GET | `/api/admin/announcements` | 公告列表 + 状态统计 |
| POST | `/api/admin/announcements` | 新建公告（草稿 / 定时 / 发布） |
| PATCH | `/api/admin/announcements/{id}` | 编辑公告 |
| POST | `/api/admin/announcements/{id}/revoke` | 撤回公告 |
| GET | `/api/admin/logs` | 调用日志，支持 user_id/status/model_name/days（普通管理员仅本组） |
| GET | `/api/admin/groups` | 仅超管：各管理员管辖人数与未分配统计 |

除 `/health`、注册登录、刷新外，业务接口均需请求头
`Authorization: Bearer <access_token>`。

示例：

```powershell
# 注册（返回 access_token / refresh_token）
$body = '{"username":"alice","password":"Passw0rd!"}'
$res = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/auth/register `
  -Method Post -ContentType "application/json" -Body $body
$token = $res.access_token

# 带 Token 提问
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/chat -Method Post `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"question":"你好","model_name":"mock"}'
```

## 模型切换与自建配置

内置三款模型由 `app/services/model_config_service.py` 的 `seed_builtin()` 在每次启动时
从 `.env` 同步到 `model_configs` 表（`owner_user_id` 为空）：

| model_name | provider | 说明 |
|------------|----------|------|
| `mock` | mock | 本地联调，不联网、不耗 Token |
| `deepseek-chat` | deepseek | 读 `.env` 的 `MODEL_BASE_URL` / `MODEL_API_KEY` |
| `local` | local | 读 `LOCAL_MODEL_BASE_URL` / `LOCAL_MODEL_NAME`，默认 Ollama `http://127.0.0.1:11434/v1` + `qwen2.5:3b` |

用户在 `/api/chat` 里传 `model_name` 即可自由切换；还可以通过 `POST /api/models`
自建配置（同用户下 `name` 唯一，重名返回 409），自建配置仅本人可见可用。

```powershell
# 接入本机 Ollama（需先启动 ollama serve 并 pull 模型）
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/models -Method Post `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"name":"my-ollama","display_name":"我的 Ollama","provider":"local","base_url":"http://127.0.0.1:11434/v1","upstream_model":"qwen2.5:3b","api_key":""}'
```

- `api_key` 落库前用 AES-256-GCM 加密（密钥为 `.env` 的 `MASTER_ENCRYPTION_KEY`），
  任何接口响应只返回 `has_api_key`，不回显明文
- 修改 `MASTER_ENCRYPTION_KEY` 会导致旧密文无法解密，需重新填写 Key
- 内置模型不允许修改/删除，改地址或 Key 请改 `.env` 后重启
- 本地模型 connect 超时 5s、推理超时 60s；远程模型默认 10s
- 纯 CPU 下 `qwen2.5:3b` 冷启动约 10s、常驻后约 4s；`qwen2.5:7b` 冷启动可达 57s，接近 60s 超时，
  需要常驻（`OLLAMA_KEEP_ALIVE=-1`）或把 `LOCAL_MODEL_TIMEOUT_SECONDS` 提到 180

## 目录结构

```
ai问答平台/
├── app/
│   ├── main.py                 # 应用入口：lifespan、异常处理、request_id 中间件
│   ├── api/
│   │   ├── deps.py             # 当前用户 / 管理员权限依赖
│   │   ├── auth_routes.py      # 注册、登录、刷新、登出、改密、me
│   │   ├── admin_routes.py     # 用户列表、审核启停、批量审核、公告管理、调用日志
│   │   ├── announcement_routes.py # 用户端公告查询与已读上报
│   │   ├── log_routes.py       # 用户端 /api/logs 与管理端 /api/admin/logs
│   │   └── routes.py           # 问答、历史、模型配置
│   ├── schemas/
│   │   ├── chat.py             # 问答请求/响应模型
│   │   ├── auth.py             # 认证请求/响应模型
│   │   ├── admin.py            # 管理后台请求/响应模型
│   │   └── model_config.py     # 模型配置请求/响应模型
│   ├── core/
│   │   ├── config.py           # 读取 .env
│   │   ├── security.py         # bcrypt 密码哈希 + JWT 签发/校验
│   │   ├── crypto.py           # API Key AES-256-GCM 加解密
│   │   ├── logging.py          # 控制台 + 文件日志，按天轮转
│   │   └── exceptions.py       # AppException 及全局处理器
│   ├── database/
│   │   ├── session.py          # 异步引擎、session 工厂、get_db
│   │   └── init_db.py          # 建表 + WAL + 超管/演示用户 + 内置模型同步
│   ├── models/
│   │   ├── conversation.py     # users / user_tokens / audit_log / chat_sessions / api_call_logs
│   │   ├── announcement.py     # announcements / announcement_reads
│   │   └── model_config.py     # model_configs
│   ├── services/
│   │   ├── auth_service.py     # 注册试用、登录、刷新、登出、改密
│   │   ├── admin_service.py    # 用户列表、审核启停、批量审核、审计
│   │   ├── llm_client.py       # 模型调用统一封装（mock + OpenAI 兼容）
│   │   ├── model_config_service.py # 模型配置 CRUD、解析、连通性测试
│   │   ├── chat_service.py     # 问答编排 + 调用日志
│   │   ├── log_service.py      # 调用日志查询、汇总、过期清理
│   │   ├── history_service.py  # 历史查询与软删除
│   │   └── user_service.py     # 用户查询、试用期判断、归属校验
│   ├── static/                 # login / index / admin / profile 页面 + JS/CSS
│   └── ai/                     # Provider / RAG / 敏感词（P2 预留）
├── docs/
│   ├── 需求说明.md
│   └── 接口测试记录.md
├── data/                       # app.db、chroma 向量库
├── uploads/                    # 上传文件
├── logs/                       # app.log 及按天轮转的历史文件（不入库）
├── alembic/                    # 迁移：0001_auth / 0002_announcement / 0003_chat_log_tokens
├── requirements.txt
├── .env.example
└── README.md
```

## 数据表

| 表 | 用途 |
|----|------|
| `users` | 账号、bcrypt 密码、角色、审核状态、归属管理员（managed_by）、试用期、token 版本 |
| `user_tokens` | refresh token 白名单（只存 SHA-256，支持轮换与吊销） |
| `audit_log` | 管理员审核/启停操作留痕（before/after 脱敏） |
| `chat_sessions` | 每轮问答历史（question/answer/model_name/软删除标记） |
| `api_call_logs` | 每次模型调用的模型、耗时、状态、prompt/completion/total tokens、错误信息 |
| `model_configs` | 内置与用户自建模型配置，api_key 加密存储 |
| `announcements` | 公告标题/正文/级别/状态/生效与到期时间 |
| `announcement_reads` | 公告已读记录（首次/最近时间、累计次数） |

已有数据库升级执行 `alembic upgrade head`；全新数据库由
`app/database/init_db.py` 自动建表（幂等），并创建超级管理员与演示用户。

## 常见问题

**1. `Activate.ps1` 报 PSSecurityException**
PowerShell 默认禁止运行未签名脚本。直接用 `.\.venv\Scripts\python.exe` 指定解释器即可，无需激活。

**2. 真实模型返回 502「模型 API Key 未配置」**
在 `.env` 设置 `MODEL_API_KEY`、`MODEL_BASE_URL`、`DEFAULT_MODEL` 后重启服务。
mock 模型不依赖网络和 Key，本地联调优先用 mock。

**3. `weasyprint` import 失败（Windows）**
缺 GTK 运行时，只影响 PDF 导出（P2）。可安装 GTK3 Runtime，或在 P2 阶段改用 Playwright 打印。

**4. 历史查询返回 403**
当前登录用户与路径 `user_id` 不一致。登录后接口一律以 Token 中的用户为准，
只有 `AUTH_REQUIRED=false` 的本地调试模式才会读取旧的 `X-User-Id` 请求头。

**5. 访问接口返回 401「请先登录」**
先在 `/login` 登录，或调用 `/api/auth/login` 拿到 `access_token` 后通过
`Authorization: Bearer <token>` 访问；access 过期用 `/api/auth/refresh` 换新。

**6. 想重置数据**
停止服务后删除 `data/app.db*`，执行 `alembic upgrade head`，
再启动服务会自动建表并重新创建超级管理员与演示用户。

**7. 本地模型（Ollama）总是超时**
先确认 `ollama serve` 在跑、模型已 pull（`ollama list`）。本项目默认 `qwen2.5:3b`，
纯 CPU 冷启动约 10s；若改用 7B，冷启动可达 57s，需设置 `OLLAMA_KEEP_ALIVE=-1`
让模型常驻，或把 `.env` 的 `LOCAL_MODEL_TIMEOUT_SECONDS` 提到 180。
另外若本机开着系统代理，请确保 `127.0.0.1` 在 `NO_PROXY` 中。
