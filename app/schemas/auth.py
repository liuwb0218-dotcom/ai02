"""认证相关请求/响应模型"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """注册：用户名 + 密码；注册成功后自动获得 7 天试用期"""

    username: str = Field(
        ..., min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$",
        description="3~32 位字母、数字或下划线",
    )
    password: str = Field(..., min_length=8, max_length=72,
                          description="8~72 位；bcrypt 最多处理 72 字节")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class LogoutRequest(BaseModel):
    """登出：带上 refresh_token 则吊销该设备；all_devices=true 则全端下线"""

    refresh_token: str | None = Field(None, min_length=10)
    all_devices: bool = False


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)


class UserPublic(BaseModel):
    """返回给前端的账号信息（不含密码哈希等敏感字段）"""

    id: int
    username: str
    role: str = Field(..., description="super_admin / admin / user")
    status: str = Field(..., description="pending / active / disabled")
    trial_expires_at: str | None = None
    trial_expired: bool = Field(False, description="是否已过试用期且仍未审核")
    disabled_until: str | None = None
    disabled_reason: str | None = None
    last_login_at: str | None = None
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="access token 有效期（秒）")
    user: UserPublic


class MessageResponse(BaseModel):
    message: str
