"""密码哈希与 JWT —— 认证功能的底层工具

约定：
  - 密码用 bcrypt 存储，永不明文落库
  - access token 短效（30 分钟），refresh token 长效（7 天）且白名单入库
  - 两类 Token 都带 type 字段与 token 版本 tv，防止互相冒用与旧版本复用
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import AuthenticationException
from app.models.conversation import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def now_text() -> str:
    """本地时间字符串，与 SQLite datetime('now','localtime') 格式一致"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str) -> str:
    """bcrypt 哈希；超过 72 字节的部分 bcrypt 会截断，先由 schema 限制长度"""
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码；空哈希（迁移前的旧账号）直接判失败，避免 passlib 抛异常"""
    if not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def sha256_token(token: str) -> str:
    """Token 只存哈希：数据库泄露时无法反推明文"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _build_token(user: User, token_type: str, expires_delta: timedelta) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "tv": user.token_version,
        "type": token_type,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
        # jti 仅用于保证同一秒签发的 Token 也唯一
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    return _build_token(
        user, ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES),
    )


def create_refresh_token(user: User) -> tuple[str, str]:
    """返回 (明文 refresh token, 过期时间字符串) —— 明文只回给客户端"""
    delta = timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    token = _build_token(user, REFRESH_TOKEN_TYPE, delta)
    expires_at = (datetime.now() + delta).strftime("%Y-%m-%d %H:%M:%S")
    return token, expires_at


def decode_token(token: str, expected_type: str) -> dict:
    """解析并校验 Token 类型；任何异常统一转 401，不泄露具体原因"""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise AuthenticationException("登录状态已失效，请重新登录") from exc

    if payload.get("type") != expected_type:
        raise AuthenticationException("Token 类型不正确")
    if not payload.get("sub"):
        raise AuthenticationException("Token 缺少用户信息")
    return payload


def user_id_from_payload(payload: dict) -> int:
    try:
        return int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise AuthenticationException("Token 用户信息无效") from exc
