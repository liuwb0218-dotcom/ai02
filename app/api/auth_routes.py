"""认证接口 — 注册、登录、刷新、登出、改密、当前用户"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database.session import get_db
from app.models.conversation import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.services import auth_service, user_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, request: Request,
                   db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """注册并自动登录

    默认 status=pending，获得 REGISTER_TRIAL_DAYS 天临时使用权；
    管理员审核通过后转为 active（永久），到期未审核则只能登录看历史。
    """
    return await auth_service.register(
        db, req, ip=_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request,
                db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """用户名 + 密码登录；待审核账号试用期内可直接使用"""
    return await auth_service.login(
        db, req, ip=_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, request: Request,
                  db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """刷新 Token：旧 refresh 立即作废，返回新的双 Token"""
    return await auth_service.refresh(
        db, req.refresh_token, ip=_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(req: LogoutRequest,
                 current_user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)) -> MessageResponse:
    """登出当前设备；all_devices=true 时所有设备下线"""
    await auth_service.logout(db, current_user, req)
    return MessageResponse(message="已退出登录")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(req: ChangePasswordRequest,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)) -> MessageResponse:
    """修改密码：成功后所有设备需重新登录"""
    await auth_service.change_password(
        db, current_user, req.old_password, req.new_password
    )
    return MessageResponse(message="密码已修改，请重新登录")


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    """返回当前登录用户信息，前端用于渲染角色徽标与试用提示"""
    return user_service.to_public(current_user)
