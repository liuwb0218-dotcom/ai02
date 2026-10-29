"""管理员审核相关请求/响应模型"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.auth import UserPublic


class AdminUserItem(UserPublic):
    """管理页用户条目：在公共字段上补充试用状态与归属管理员"""

    trial_days_left: int | None = Field(None, description="试用剩余天数；已审核为 null")
    managed_by: int | None = Field(None, description="归属管理员 ID；未分配或管理员账号为 null")
    manager_name: str | None = Field(None, description="归属管理员用户名")


class AdminGroupItem(BaseModel):
    """分组视图的一行：一个管理员及其管辖用户的分布"""

    manager_id: int
    manager_name: str
    pending: int
    active: int
    disabled: int
    total: int


class AdminGroupsResponse(BaseModel):
    items: list[AdminGroupItem]
    unassigned: int = Field(description="未分配归属的普通用户数")
    unassigned_pending: int = Field(description="未分配且待审核的普通用户数")


class UserCounts(BaseModel):
    total: int
    pending: int
    active: int
    disabled: int
    trial_expired: int


class AdminUsersResponse(BaseModel):
    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int
    counts: UserCounts


class UpdateUserStatusRequest(BaseModel):
    """单一用户审核/启停：active=通过或启用，disabled=拒绝或禁用"""

    status: Literal["active", "disabled"]
    reason: str | None = Field(None, max_length=200)
    disabled_until: str | None = Field(
        None, max_length=19,
        description="禁用到期时间 YYYY-MM-DD HH:MM:SS；为空表示永久",
    )


class UpdateUserRoleRequest(BaseModel):
    """超管任命/撤销普通管理员：admin=设为管理员，user=降为普通用户"""

    role: Literal["admin", "user"]


class UpdateUserManagerRequest(BaseModel):
    """超管手动调整普通用户的归属管理员：null 表示移回未分配"""

    managed_by: int | None = Field(None, ge=1, description="目标管理员 ID；null=未分配")


class BatchReviewRequest(BaseModel):
    user_ids: list[int] = Field(..., min_length=1, max_length=100)
    action: Literal["approve", "reject"]
    reason: str | None = Field(None, max_length=200)


class BatchSkipped(BaseModel):
    user_id: int
    reason: str


class BatchReviewResponse(BaseModel):
    updated: int
    action: str
    skipped: list[BatchSkipped]
