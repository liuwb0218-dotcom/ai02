"""接口层 — 本期 5 个核心接口：/health、/api/models、/api/chat、/api/history

分层约定：路由只做「参数接收 → 调 Service → 返回模型」，不写业务逻辑与 SQL。
所有错误由 Service 抛 AppException 子类，统一在 main.py 注册的处理器中转换。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    DeleteResponse,
    HistoryResponse,
)
from app.schemas.model_config import (
    DeleteModelResponse,
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelsResponse,
    ModelTestResponse,
)
from app.services import chat_service, history_service, model_config_service

router = APIRouter()


# ═══════════════════════════════════════════════════
# 1. 健康检查
# ═══════════════════════════════════════════════════

@router.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    """存活探针：不查库、不调外部服务，仅确认进程可响应"""
    return {"status": "ok", "message": "AI QA Platform is running"}


# ═══════════════════════════════════════════════════
# 2. 模型列表与模型配置管理
# ═══════════════════════════════════════════════════

@router.get("/api/models", tags=["chat"], response_model=ModelsResponse)
async def list_models(
    user_id: int | None = Query(None, gt=0, description="传入后额外返回该用户的自建模型"),
    db: AsyncSession = Depends(get_db),
) -> ModelsResponse:
    """返回可切换的模型列表：内置模型 + 该用户自建模型

    不传 user_id 时只返回内置模型（mock / DeepSeek / 本地模型）。
    """
    return ModelsResponse(models=await model_config_service.list_for_user(db, user_id))


@router.post("/api/models", tags=["models"], response_model=ModelConfigResponse)
async def create_model(
    req: ModelConfigCreate,
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> ModelConfigResponse:
    """新增用户自建模型配置（如接入公司内网模型、其他厂商 API）

    失败语义：重名 409 / 参数非法 422 / 越权 403
    """
    return await model_config_service.create(db, req, current_user_id=x_user_id)


@router.patch("/api/models/{config_id}", tags=["models"],
              response_model=ModelConfigResponse)
async def update_model(
    config_id: int,
    req: ModelConfigUpdate,
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> ModelConfigResponse:
    """修改自建模型配置：支持改地址、模型名、Key、启停；内置模型禁止修改"""
    return await model_config_service.update(db, config_id, req,
                                             current_user_id=x_user_id)


@router.delete("/api/models/{config_id}", tags=["models"],
               response_model=DeleteModelResponse)
async def delete_model(
    config_id: int,
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> DeleteModelResponse:
    """删除自建模型配置；内置模型不可删除（可停用）"""
    await model_config_service.delete(db, config_id, current_user_id=x_user_id)
    return DeleteModelResponse(deleted=True, id=config_id)


@router.post("/api/models/{config_id}/test", tags=["models"],
             response_model=ModelTestResponse)
async def test_model(
    config_id: int,
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> ModelTestResponse:
    """连通性测试：用一句固定提问验证地址/Key/模型名是否正确"""
    return await model_config_service.test(db, config_id, current_user_id=x_user_id)


# ═══════════════════════════════════════════════════
# 3. 发起问答
# ═══════════════════════════════════════════════════

@router.post("/api/chat", tags=["chat"], response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)) -> ChatResponse:
    """单轮问答：校验用户 → 调模型 → 落库会话与调用日志

    失败语义：
      - 用户不存在 → 404 NOT_FOUND
      - 模型不存在 → 404 MODEL_NOT_FOUND
      - 模型调用失败 → 502 MODEL_CALL_FAILED（仍写一条 error 日志）
    """
    return await chat_service.ask(db, req)


# ═══════════════════════════════════════════════════
# 4. 历史查询（按时间倒序，只能查自己的）
# ═══════════════════════════════════════════════════

@router.get("/api/history/{user_id}", tags=["history"],
            response_model=HistoryResponse)
async def list_history(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    # 本期用请求头模拟登录态；接入 JWT 后改为从 Token 解析 current_user
    x_user_id: int | None = Header(None, alias="X-User-Id",
                                  description="当前登录用户，接入 JWT 后由 Token 解析"),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    """查询指定用户未删除的问答记录

    limit 上限 100，防止一次拉取过多数据；不带 X-User-Id 时按本地联调放行。
    """
    return await history_service.list_history(
        db, user_id, limit=limit, offset=offset, current_user_id=x_user_id
    )


# ═══════════════════════════════════════════════════
# 5. 软删除历史（幂等）
# ═══════════════════════════════════════════════════

@router.delete("/api/history/{session_id}", tags=["history"],
               response_model=DeleteResponse)
async def delete_history(
    session_id: int,
    x_user_id: int | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    """软删除指定会话：仅置 is_deleted=1，不物理删除

    重复删除同一 ID 也返回 deleted=True，保证前端重试安全。
    """
    return await history_service.soft_delete(db, session_id, current_user_id=x_user_id)


# ═══════════════════════════════════════════════════
# 根路径
# ═══════════════════════════════════════════════════

@router.get("/", tags=["infra"])
async def root() -> dict[str, str]:
    """根路径：给出应用名与常用入口，方便浏览器直接访问"""
    return {
        "app": settings.APP_NAME,
        "docs": "/docs",
        "health": "/health",
    }
