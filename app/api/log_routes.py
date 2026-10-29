"""日志接口 — 用户自查「使用统计」与管理端全员调用日志

权限边界：
  - /api/logs/*     登录用户，服务端强制按当前用户过滤，不接受 user_id 参数
  - /api/admin/logs admin / super_admin，可按用户筛选
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database.session import get_db
from app.models.conversation import User
from app.schemas.log import LogsResponse, LogSummary
from app.services import log_service

router = APIRouter(prefix="/api/logs", tags=["logs"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/summary", response_model=LogSummary)
async def my_summary(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LogSummary:
    """当前登录用户的用量汇总：调用次数、成功率、平均耗时、Token 消耗"""
    return await log_service.user_summary(db, current_user.id, days=days)


@router.get("", response_model=LogsResponse)
async def my_logs(
    status: str | None = Query(None, pattern="^(success|error)$"),
    model_name: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LogsResponse:
    """当前登录用户的调用记录；只返回元信息，不含问题与回答"""
    return await log_service.list_logs(
        db, user_id=current_user.id, status=status,
        model_name=model_name, page=page, page_size=page_size,
    )


@admin_router.get("/logs", response_model=LogsResponse)
async def all_logs(
    user_id: int | None = Query(None, gt=0, description="按用户筛选；为空表示全部"),
    status: str | None = Query(None, pattern="^(success|error)$"),
    model_name: str | None = Query(None, max_length=64),
    days: int | None = Query(None, ge=1, le=365, description="只看最近 N 天"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LogsResponse:
    """管理端调用日志：超管看全员；普通管理员只看本组用户"""
    return await log_service.list_logs(
        db, user_id=user_id, status=status, model_name=model_name,
        days=days, page=page, page_size=page_size,
        managed_by=None if operator.role == "super_admin" else operator.id,
    )
