"""用户领域逻辑 — 查询、试用期判断、归属校验"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    TrialExpiredException,
)
from app.models.conversation import User
from app.schemas.auth import UserPublic

TIME_FMT = "%Y-%m-%d %H:%M:%S"


async def get_user(db: AsyncSession, user_id: int) -> User:
    """按 ID 取用户，不存在抛 404（不泄露其他信息）"""
    # scalar 而非 scalars：只可能有一行（主键查询）
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundException(f"用户 {user_id} 不存在")
    return user


async def get_by_username(db: AsyncSession, username: str) -> User | None:
    """按用户名查询，登录与注册判重使用"""
    return await db.scalar(select(User).where(User.username == username))


async def assign_manager(db: AsyncSession, user: User) -> None:
    """按已分配人数轮询，把普通用户交给下一位活跃管理员

    没有管理员时保持未分配（managed_by=None），等超管任命后再补位。
    """
    managers = list((await db.scalars(
        select(User)
        .where(User.role == "admin", User.status == "active")
        .order_by(User.id)
    )).all())
    if not managers:
        user.managed_by = None
        return
    assigned = int(await db.scalar(
        select(func.count()).select_from(User).where(User.managed_by.is_not(None))
    ) or 0)
    user.managed_by = managers[assigned % len(managers)].id


async def check_owner(current_user_id: int | None, target_user_id: int) -> None:
    """校验当前用户是否有权访问目标用户的资源

    current_user_id 为 None 仅出现在 AUTH_REQUIRED=False 的本地联调模式，
    生产环境由 Token 解析出用户 ID，不允许跨用户访问。
    """
    if current_user_id is not None and current_user_id != target_user_id:
        raise PermissionDeniedException("无权访问其他用户的数据")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], TIME_FMT)
    except ValueError:
        return None


def trial_expired(user: User, now: datetime | None = None) -> bool:
    """待审核用户是否已过试用期；已审核/已禁用账号不适用"""
    if user.status != "pending":
        return False
    expires = parse_time(user.trial_expires_at)
    if expires is None:
        return False
    return expires <= (now or datetime.now())


def trial_days_left(user: User) -> int | None:
    """试用剩余天数（向上取整，最少 0）；非试用账号返回 None"""
    if user.status != "pending":
        return None
    expires = parse_time(user.trial_expires_at)
    if expires is None:
        return None
    seconds = (expires - datetime.now()).total_seconds()
    return max(0, -(-int(seconds) // 86400))


def ensure_chat_allowed(user: User) -> None:
    """待审核且试用期已结束时：允许登录看历史，但禁止发起新问答"""
    if trial_expired(user):
        raise TrialExpiredException(
            "7 天试用期已结束，请联系管理员审核后再继续提问",
            detail={"trial_expires_at": user.trial_expires_at},
        )


def to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        username=user.username,
        role=user.role,
        status=user.status,
        trial_expires_at=user.trial_expires_at,
        trial_expired=trial_expired(user),
        disabled_until=user.disabled_until,
        disabled_reason=user.disabled_reason,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )
