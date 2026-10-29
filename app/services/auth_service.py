"""认证服务 — 注册（含 7 天试用）、登录、刷新、登出、改密"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AccountDisabledException,
    AuthenticationException,
    ConflictException,
    ValidationException,
)
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    now_text,
    sha256_token,
    user_id_from_payload,
    verify_password,
)
from app.models.conversation import User, UserToken
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.services import user_service

logger = logging.getLogger("app.auth")


def _client_meta(ip: str | None, user_agent: str | None) -> tuple[str | None, str | None]:
    return ip, (user_agent or "")[:300] or None


async def _issue_tokens(db: AsyncSession, user: User,
                        ip: str | None, user_agent: str | None) -> TokenResponse:
    """签发 access + refresh，并把 refresh 哈希写入白名单"""
    access = create_access_token(user)
    refresh, expires_at = create_refresh_token(user)
    meta_ip, meta_ua = _client_meta(ip, user_agent)
    db.add(UserToken(
        user_id=user.id,
        token_hash=sha256_token(refresh),
        expires_at=expires_at,
        user_agent=meta_ua,
        ip=meta_ip,
    ))
    await db.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        user=user_service.to_public(user),
    )


def _validate_password(password: str) -> None:
    """bcrypt 只处理前 72 字节，超出直接拒绝而不是静默截断"""
    if len(password.encode("utf-8")) > 72:
        raise ValidationException("密码过长，请控制在 72 字节以内")


async def register(db: AsyncSession, req: RegisterRequest,
                   ip: str | None = None,
                   user_agent: str | None = None) -> TokenResponse:
    """注册即登录：默认进入 pending 状态并获得 7 天临时使用权"""
    username = req.username.strip()
    _validate_password(req.password)
    if await user_service.get_by_username(db, username):
        raise ConflictException("用户名已被占用")

    approved = settings.AUTO_APPROVE_REGISTER
    trial_expires_at = None
    if not approved:
        trial_expires_at = (
            datetime.now() + timedelta(days=settings.REGISTER_TRIAL_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")

    user = User(
        username=username,
        password_hash=hash_password(req.password),
        role="user",
        status="active" if approved else "pending",
        trial_expires_at=trial_expires_at,
        last_login_at=now_text(),
    )
    db.add(user)
    try:
        await db.flush()
        await db.refresh(user)
    except Exception:  # 并发注册同一用户名时唯一约束兜底
        await db.rollback()
        raise ConflictException("用户名已被占用")

    # 按注册顺序轮询分配归属管理员；没有管理员时保持未分配
    await user_service.assign_manager(db, user)

    logger.info("新用户注册 id=%s username=%s status=%s manager=%s",
                user.id, user.username, user.status, user.managed_by)
    return await _issue_tokens(db, user, ip, user_agent)


async def login(db: AsyncSession, req: LoginRequest,
                ip: str | None = None,
                user_agent: str | None = None) -> TokenResponse:
    """用户名 + 密码登录；禁用账号返回 403，试用到期仍允许登录"""
    user = await user_service.get_by_username(db, req.username.strip())
    if user is None or not verify_password(req.password, user.password_hash):
        # 不区分"用户不存在"与"密码错误"，避免账号枚举
        raise AuthenticationException("用户名或密码错误")

    if user.status == "disabled":
        until = user_service.parse_time(user.disabled_until)
        if until is not None and until <= datetime.now():
            # 限时禁用已到期，自动恢复为永久可用
            user.status = "active"
            user.disabled_until = None
            user.disabled_reason = None
            logger.info("用户 %s 限时禁用到期，已自动恢复", user.id)
        else:
            raise AccountDisabledException(
                user.disabled_reason or "账号已被禁用，请联系管理员",
                detail={
                    "disabled_until": user.disabled_until,
                    "disabled_reason": user.disabled_reason,
                },
            )

    user.last_login_at = now_text()
    return await _issue_tokens(db, user, ip, user_agent)


async def refresh(db: AsyncSession, refresh_token: str,
                  ip: str | None = None,
                  user_agent: str | None = None) -> TokenResponse:
    """刷新：校验签名 → 校验白名单 → 轮换（旧 refresh 立即吊销）"""
    payload = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
    user_id = user_id_from_payload(payload)

    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationException("账号不存在或已被删除")
    if int(payload.get("tv", -1)) != user.token_version:
        raise AuthenticationException("登录状态已失效，请重新登录")
    if user.status == "disabled":
        raise AccountDisabledException(
            user.disabled_reason or "账号已被禁用，请联系管理员"
        )

    token_hash = sha256_token(refresh_token)
    record = await db.scalar(
        select(UserToken).where(
            UserToken.token_hash == token_hash,
            UserToken.revoked_at.is_(None),
        )
    )
    if record is None:
        raise AuthenticationException("登录状态已失效，请重新登录")
    expires_at = user_service.parse_time(record.expires_at)
    if expires_at is not None and expires_at <= datetime.now():
        raise AuthenticationException("登录已过期，请重新登录")

    await db.execute(
        UserToken.__table__.update()
        .where(UserToken.id == record.id)
        .values(revoked_at=now_text())
    )
    return await _issue_tokens(db, user, ip, user_agent)


async def logout(db: AsyncSession, user: User, req: LogoutRequest) -> None:
    """登出：默认只吊销当前 refresh；all_devices=true 时全端下线"""
    if req.all_devices:
        user.token_version += 1
        await db.execute(
            UserToken.__table__.update()
            .where(UserToken.user_id == user.id, UserToken.revoked_at.is_(None))
            .values(revoked_at=now_text())
        )
    elif req.refresh_token:
        await db.execute(
            UserToken.__table__.update()
            .where(
                UserToken.user_id == user.id,
                UserToken.token_hash == sha256_token(req.refresh_token),
                UserToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_text())
        )
    await db.commit()


async def change_password(db: AsyncSession, user: User,
                          old_password: str, new_password: str) -> None:
    """改密：校验旧密码 → 更新哈希 → 全端下线（token_version+1）"""
    if not verify_password(old_password, user.password_hash):
        raise ValidationException("原密码不正确")
    if old_password == new_password:
        raise ValidationException("新密码不能与原密码相同")
    _validate_password(new_password)

    user.password_hash = hash_password(new_password)
    user.token_version += 1
    await db.execute(
        UserToken.__table__.update()
        .where(UserToken.user_id == user.id, UserToken.revoked_at.is_(None))
        .values(revoked_at=now_text())
    )
    await db.commit()


def current_user_public(user: User) -> UserPublic:
    return user_service.to_public(user)
