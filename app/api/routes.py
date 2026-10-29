"""接口层 — 本期 5 个核心接口：/health、/api/models、/api/chat、/api/history

分层约定：路由只做「参数接收 → 调 Service → 返回模型」，不写业务逻辑与 SQL。
所有错误由 Service 抛 AppException 子类，统一在 main.py 注册的处理器中转换。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_optional_user
from app.database.session import get_db
from app.models.conversation import User
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
from app.services import chat_service, history_service, model_config_service, user_service

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
    user_id: int | None = Query(None, gt=0, description="兼容旧调用；登录后忽略此参数"),
    include_disabled: bool = Query(False, description="true 时额外返回已停用配置（模型管理页用）"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> ModelsResponse:
    """返回可切换的模型列表：内置模型 + 当前用户自建模型"""
    effective_user_id = current_user.id if current_user else user_id
    return ModelsResponse(
        models=await model_config_service.list_for_user(
            db, effective_user_id, include_disabled=include_disabled
        )
    )


@router.post("/api/models", tags=["models"], response_model=ModelConfigResponse)
async def create_model(
    req: ModelConfigCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelConfigResponse:
    """新增用户自建模型配置（归属当前登录用户）

    失败语义：重名 409 / 参数非法 422 / 越权 403
    """
    req = req.model_copy(update={"user_id": current_user.id})
    return await model_config_service.create(db, req, current_user_id=current_user.id)


@router.patch("/api/models/{config_id}", tags=["models"],
              response_model=ModelConfigResponse)
async def update_model(
    config_id: int,
    req: ModelConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelConfigResponse:
    """修改自建模型配置：支持改地址、模型名、Key、启停；内置模型禁止修改"""
    return await model_config_service.update(db, config_id, req,
                                             current_user_id=current_user.id)


@router.delete("/api/models/{config_id}", tags=["models"],
               response_model=DeleteModelResponse)
async def delete_model(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteModelResponse:
    """删除自建模型配置；内置模型不可删除（可停用）"""
    await model_config_service.delete(db, config_id,
                                      current_user_id=current_user.id)
    return DeleteModelResponse(deleted=True, id=config_id)


@router.post("/api/models/{config_id}/test", tags=["models"],
             response_model=ModelTestResponse)
async def test_model(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelTestResponse:
    """连通性测试：用一句固定提问验证地址/Key/模型名是否正确"""
    return await model_config_service.test(db, config_id,
                                           current_user_id=current_user.id)


# ═══════════════════════════════════════════════════
# 3. 发起问答
# ═══════════════════════════════════════════════════

@router.post("/api/chat", tags=["chat"], response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """单轮问答：以登录用户为准 → 校验试用期 → 调模型 → 落库

    失败语义：
      - 未登录 → 401 UNAUTHORIZED
      - 试用期结束且未审核 → 403 TRIAL_EXPIRED（仍可查看历史）
      - 模型不存在 → 404 MODEL_NOT_FOUND
      - 模型调用失败 → 502 MODEL_CALL_FAILED（仍写一条 error 日志）
    """
    user_service.ensure_chat_allowed(current_user)
    req = req.model_copy(update={"user_id": current_user.id})
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    """查询指定用户未删除的问答记录；登录用户只能查自己"""
    await user_service.check_owner(current_user.id, user_id)
    return await history_service.list_history(
        db, user_id, limit=limit, offset=offset,
        current_user_id=current_user.id,
    )


# ═══════════════════════════════════════════════════
# 5. 软删除历史（幂等）
# ═══════════════════════════════════════════════════

@router.delete("/api/history/{session_id}", tags=["history"],
               response_model=DeleteResponse)
async def delete_history(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    """软删除指定会话：仅置 is_deleted=1，不物理删除

    重复删除同一 ID 也返回 deleted=True，保证前端重试安全。
    """
    return await history_service.soft_delete(
        db, session_id, current_user_id=current_user.id
    )
