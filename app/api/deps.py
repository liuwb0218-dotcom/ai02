"""FastAPI 依赖 — 从 Authorization: Bearer 解析当前用户并做角色校验

兼容策略：
  - AUTH_REQUIRED=True（默认）：所有业务接口必须带 access token
  - AUTH_REQUIRED=False（仅本地联调）：允许旧的 X-User-Id 请求头，方便脚本调试
"""
from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AccountDisabledException,
    AuthenticationException,
    PermissionDeniedException,
)
from app.core.security import ACCESS_TOKEN_TYPE, decode_token, user_id_from_payload
from app.database.session import get_db
from app.models.conversation import User

bearer_scheme = HTTPBearer(auto_error=False)


def _reject_disabled(user: User) -> None:
    """禁用账号不允许携带 Token 调任何业务接口"""
    if user.status == "disabled":
        raise AccountDisabledException(
            user.disabled_reason or "账号已被禁用，请联系管理员",
            detail={
                "disabled_until": user.disabled_until,
                "disabled_reason": user.disabled_reason,
            },
        )


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """优先解析 Bearer Token；未登录时按配置决定是否放行调试头"""
    if credentials is not None:
        payload = decode_token(credentials.credentials, ACCESS_TOKEN_TYPE)
        user_id = user_id_from_payload(payload)
        user = await db.get(User, user_id)
        if user is None:
            raise AuthenticationException("账号不存在或已被删除")
        # 版本不一致说明发生了禁用/改密/全端登出，旧 Token 立即失效
        if int(payload.get("tv", -1)) != user.token_version:
            raise AuthenticationException("登录状态已失效，请重新登录")
        _reject_disabled(user)
        return user

    if settings.AUTH_REQUIRED:
        raise AuthenticationException("请先登录")

    # 调试兼容：AUTH_REQUIRED=False 时允许用请求头指定用户
    header = request.headers.get("X-User-Id")
    if header and header.isdigit():
        user = await db.get(User, int(header))
        if user is not None:
            _reject_disabled(user)
            return user
    return None


async def get_current_user(
    user: User | None = Depends(get_optional_user),
) -> User:
    """强制登录的接口使用：拿不到用户直接 401"""
    if user is None:
        raise AuthenticationException("请先登录")
    return user


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """管理后台接口：admin 与 super_admin 均可访问"""
    if user.role not in ("admin", "super_admin"):
        raise PermissionDeniedException("需要管理员权限")
    return user
