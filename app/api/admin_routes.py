"""管理员接口 — 待审核队列、审核/启停、批量审核、公告管理

权限：admin / super_admin 可访问；普通管理员不能操作超级管理员。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.exceptions import PermissionDeniedException
from app.database.session import get_db
from app.models.conversation import User
from app.schemas.admin import (
    AdminGroupsResponse,
    AdminUserItem,
    AdminUsersResponse,
    BatchReviewRequest,
    BatchReviewResponse,
    UpdateUserManagerRequest,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
)
from app.schemas.announcement import (
    AdminAnnouncementItem,
    AdminAnnouncementsResponse,
    AnnouncementCreateRequest,
    AnnouncementUpdateRequest,
)
from app.services import admin_service, announcement_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    status: str | None = Query(None, pattern="^(pending|active|disabled)$"),
    keyword: str | None = Query(None, max_length=32),
    managed_by: int | None = Query(
        None, ge=0, description="仅超管：按归属管理员筛选，0 表示未分配"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUsersResponse:
    """分页查询用户，并返回总览统计；普通管理员只能看到本组用户"""
    return await admin_service.list_users(
        db, operator, status=status, keyword=keyword,
        managed_by=managed_by, page=page, page_size=page_size,
    )


@router.patch("/users/{user_id}/status", response_model=AdminUserItem)
async def update_user_status(
    user_id: int,
    req: UpdateUserStatusRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    """审核通过 / 拒绝 / 启用 / 禁用单个用户

    通过后 trial_expires_at 清空，表示永久可用；禁用会让该用户全部 Token 失效。
    """
    return await admin_service.set_status(
        db, operator, user_id, req, ip=_ip(request)
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserItem)
async def update_user_role(
    user_id: int,
    req: UpdateUserRoleRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    """任命/撤销普通管理员：仅超级管理员可操作

    角色变更会让目标用户全部 Token 失效，需要重新登录后生效。
    """
    return await admin_service.set_role(
        db, operator, user_id, req, ip=_ip(request)
    )


@router.patch("/users/{user_id}/manager", response_model=AdminUserItem)
async def update_user_manager(
    user_id: int,
    req: UpdateUserManagerRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    """超管手动调整普通用户的归属管理员：`{"managed_by": 4}`，null 表示移回未分配"""
    return await admin_service.set_manager(
        db, operator, user_id, req.managed_by, ip=_ip(request)
    )


@router.post("/users/batch-review", response_model=BatchReviewResponse)
async def batch_review(
    req: BatchReviewRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BatchReviewResponse:
    """批量审核：approve 通过待审核用户，reject 置为禁用；逐条返回跳过原因"""
    return await admin_service.batch_review(
        db, operator, req.user_ids, req.action, req.reason, ip=_ip(request)
    )


# ═══════════════════════════════════════════════════
# 分组视图
# ═══════════════════════════════════════════════════

@router.get("/groups", response_model=AdminGroupsResponse)
async def admin_groups(
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminGroupsResponse:
    """超管分组视图：各管理员及管辖用户分布；普通管理员无权查看"""
    if operator.role != "super_admin":
        raise PermissionDeniedException("只有超级管理员可以查看分组")
    return await admin_service.group_summary(db)


# ═══════════════════════════════════════════════════
# 公告管理
# ═══════════════════════════════════════════════════

@router.get("/announcements", response_model=AdminAnnouncementsResponse)
async def list_announcements(
    status: str | None = Query(None, pattern="^(draft|published|revoked)$"),
    keyword: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnnouncementsResponse:
    """公告列表：含状态、生效/到期时间与去重已读人数"""
    return await announcement_service.admin_list(
        db, status=status, keyword=keyword, page=page, page_size=page_size
    )


@router.post("/announcements", response_model=AdminAnnouncementItem)
async def create_announcement(
    req: AnnouncementCreateRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnnouncementItem:
    """新建公告：可立即发布、定时发布或存草稿"""
    return await announcement_service.create(db, operator, req, ip=_ip(request))


@router.patch("/announcements/{announcement_id}",
              response_model=AdminAnnouncementItem)
async def update_announcement(
    announcement_id: int,
    req: AnnouncementUpdateRequest,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnnouncementItem:
    """编辑公告；已撤回的不可再改"""
    return await announcement_service.update(
        db, operator, announcement_id, req, ip=_ip(request)
    )


@router.post("/announcements/{announcement_id}/revoke",
             response_model=AdminAnnouncementItem)
async def revoke_announcement(
    announcement_id: int,
    request: Request,
    operator: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminAnnouncementItem:
    """撤回公告：用户端立即不再弹出，已读记录保留"""
    return await announcement_service.revoke(
        db, operator, announcement_id, ip=_ip(request)
    )
