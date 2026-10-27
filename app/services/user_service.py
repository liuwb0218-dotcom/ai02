"""用户管理 — 识别 user_id、校验会话归属"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.models.conversation import User


async def get_user(db: AsyncSession, user_id: int) -> User:
    """按 ID 取用户，不存在抛 404（不泄露其他信息）"""
    # scalar 而非 scalars：只可能有一行（主键查询）
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundException(f"用户 {user_id} 不存在")
    return user


async def check_owner(current_user_id: int | None, target_user_id: int) -> None:
    """校验当前用户是否有权访问目标用户的资源

    本期用请求头 X-User-Id 模拟登录态；未带该头时按路径参数视为本人（本地联调）。
    接入 JWT 后改为从 Token subject 取 current_user_id，并去掉"为空即放行"的分支。
    """
    # current_user_id 为 None 表示"未携带身份信息"，本地联调阶段放行
    if current_user_id is not None and current_user_id != target_user_id:
        raise PermissionDeniedException("无权访问其他用户的数据")
