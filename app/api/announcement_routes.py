"""用户端公告接口 — 拉取生效公告、记录已读"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database.session import get_db
from app.models.conversation import User
from app.schemas.announcement import (
    AnnouncementReadResponse,
    PendingAnnouncementsResponse,
)
from app.services import announcement_service

router = APIRouter(prefix="/api/announcements", tags=["announcement"])


@router.get("/pending", response_model=PendingAnnouncementsResponse)
async def list_pending(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PendingAnnouncementsResponse:
    """当前生效的公告；登录后由前端决定弹窗，关闭只对本次登录生效"""
    return await announcement_service.list_pending(db)


@router.post("/{announcement_id}/read", response_model=AnnouncementReadResponse)
async def mark_read(
    announcement_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementReadResponse:
    """点击「知道了」时调用；重复调用只累加阅读次数"""
    return await announcement_service.mark_read(db, user, announcement_id)
