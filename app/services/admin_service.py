"""管理员服务 — 用户列表、审核/启停、批量审核与审计日志"""
from __future__ import annotations

import json
import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.core.security import now_text
from app.models.conversation import AuditLog, User, UserToken
from app.schemas.admin import (
    AdminGroupItem,
    AdminGroupsResponse,
    AdminUserItem,
    AdminUsersResponse,
    BatchReviewResponse,
    BatchSkipped,
    UpdateUserRoleRequest,
    UpdateUserStatusRequest,
    UserCounts,
)
from app.services import user_service

logger = logging.getLogger("app.admin")


async def _counts(db: AsyncSession, scope: list = None) -> UserCounts:
    """统计卡片：总数、待审核、已审核、已禁用、试用已过期

    scope 用于普通管理员：只统计自己管辖的用户。
    """
    scope = scope or []

    async def _count(*conditions) -> int:
        return int(await db.scalar(
            select(func.count()).select_from(User).where(*scope, *conditions)
        ) or 0)

    return UserCounts(
        total=await _count(),
        pending=await _count(User.status == "pending"),
        active=await _count(User.status == "active"),
        disabled=await _count(User.status == "disabled"),
        trial_expired=await _count(
            User.status == "pending",
            User.trial_expires_at.is_not(None),
            User.trial_expires_at <= now_text(),
        ),
    )


def _item(user: User, manager_name: str | None = None) -> AdminUserItem:
    public = user_service.to_public(user)
    return AdminUserItem(
        **public.model_dump(),
        trial_days_left=user_service.trial_days_left(user),
        managed_by=user.managed_by,
        manager_name=manager_name,
    )


async def _active_managers(db: AsyncSession) -> list[User]:
    """当前可接单的管理员：按 ID 升序，作为轮询顺序"""
    return list((await db.scalars(
        select(User)
        .where(User.role == "admin", User.status == "active")
        .order_by(User.id)
    )).all())


async def _assign_next(db: AsyncSession, user: User) -> None:
    """按已分配人数轮询，把用户交给下一位管理员；无管理员则保持未分配"""
    await user_service.assign_manager(db, user)


async def _reassign_group(db: AsyncSession, manager_id: int) -> None:
    """管理员撤销/禁用后，把他组内的用户转给当前人数最少的管理员"""
    members = (await db.scalars(
        select(User).where(User.role == "user", User.managed_by == manager_id)
    )).all()
    if not members:
        return

    managers = await _active_managers(db)
    if not managers:
        for member in members:
            member.managed_by = None
        return

    counts = {m.id: 0 for m in managers}
    rows = await db.execute(
        select(User.managed_by, func.count())
        .where(User.managed_by.in_(list(counts)))
        .group_by(User.managed_by)
    )
    for mid, cnt in rows:
        counts[mid] = int(cnt)

    for member in members:
        target_id = min(counts, key=lambda mid: (counts[mid], mid))
        member.managed_by = target_id
        counts[target_id] += 1


async def list_users(db: AsyncSession, operator: User, *, status: str | None = None,
                     keyword: str | None = None, managed_by: int | None = None,
                     page: int = 1, page_size: int = 20) -> AdminUsersResponse:
    """分页查询用户；普通管理员只能看到自己组内的用户

    managed_by 仅对超管生效：0 表示未分配，其余为管理员 ID。
    """
    scope: list = []
    if operator.role != "super_admin":
        scope.append(User.managed_by == operator.id)
    elif managed_by is not None:
        # 分组页的「未分配」只指普通用户，管理员与超管本身不参与分组
        scope.append(
            and_(User.role == "user", User.managed_by.is_(None)) if managed_by == 0
            else User.managed_by == managed_by
        )

    conditions = list(scope)
    if status:
        conditions.append(User.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(User.username.like(like)))

    total = int(await db.scalar(
        select(func.count()).select_from(User).where(*conditions)
    ) or 0)

    rows = (await db.scalars(
        select(User).where(*conditions)
        .order_by(User.created_at.desc(), User.id.desc())
        .limit(page_size).offset((page - 1) * page_size)
    )).all()

    manager_ids = {u.managed_by for u in rows if u.managed_by}
    manager_names: dict[int, str] = {}
    if manager_ids:
        manager_names = {
            mid: name for mid, name in await db.execute(
                select(User.id, User.username).where(User.id.in_(manager_ids))
            )
        }

    return AdminUsersResponse(
        items=[_item(u, manager_names.get(u.managed_by)) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
        counts=await _counts(db, scope),
    )


async def _revoke_all_tokens(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        UserToken.__table__.update()
        .where(UserToken.user_id == user_id, UserToken.revoked_at.is_(None))
        .values(revoked_at=now_text())
    )


def _state(user: User) -> dict:
    """审计只记录非敏感状态字段"""
    return {
        "status": user.status,
        "role": user.role,
        "managed_by": user.managed_by,
        "disabled_reason": user.disabled_reason,
    }


def _check_operator(operator: User, target: User, action: str) -> None:
    """权限边界：普通管理员不能动超管，任何人都不能禁用超管"""
    if target.role == "super_admin":
        if operator.role != "super_admin":
            raise PermissionDeniedException("无权操作超级管理员")
        if action in ("disable", "reject"):
            raise PermissionDeniedException("超级管理员不能被禁用")
    if target.id == operator.id and action in ("disable", "reject"):
        raise ValidationException("不能禁用自己的账号")


async def set_status(db: AsyncSession, operator: User, target_id: int,
                     req: UpdateUserStatusRequest,
                     ip: str | None = None) -> AdminUserItem:
    """审核/启用/禁用单个用户：active=通过或启用，disabled=拒绝或禁用"""
    target = await user_service.get_user(db, target_id)
    action = "approve" if req.status == "active" else "disable"
    if req.status == "disabled":
        action = "reject" if target.status == "pending" else "disable"
    _check_operator(operator, target, action)
    # 普通管理员只能管理自己组内的用户；超管不受限
    if operator.role != "super_admin" and target.managed_by != operator.id:
        raise PermissionDeniedException("只能管理本组用户")

    before = _state(target)
    if req.status == "active":
        if target.status == "active":
            # 已是永久可用，重复审核不产生副作用
            return _item(target)
        target.status = "active"
        target.trial_expires_at = None
        target.disabled_until = None
        target.disabled_reason = None
        # 首次审核通过的普通用户按轮询分配归属
        if target.role == "user" and target.managed_by is None:
            await _assign_next(db, target)
    else:
        target.status = "disabled"
        target.disabled_until = req.disabled_until
        target.disabled_reason = req.reason or "管理员已禁用该账号"
        # 禁用立即生效：旧 access 因版本变化失效，refresh 全部吊销
        target.token_version += 1
        await _revoke_all_tokens(db, target.id)
        # 被禁用的管理员无法继续履职，组内用户转给其他管理员
        if target.role == "admin":
            await _reassign_group(db, target.id)

    db.add(AuditLog(
        operator_id=operator.id,
        action=action,
        target_type="user",
        target_id=target.id,
        before_state=json.dumps(before, ensure_ascii=False),
        after_state=json.dumps(_state(target), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("管理员 %s %s 用户 %s", operator.id, action, target.id)
    return _item(target)


async def _manager_name(db: AsyncSession, manager_id: int | None) -> str | None:
    """按管理员 ID 取用户名；未分配返回 None"""
    if manager_id is None:
        return None
    return await db.scalar(select(User.username).where(User.id == manager_id))


async def set_role(db: AsyncSession, operator: User, target_id: int,
                   req: UpdateUserRoleRequest,
                   ip: str | None = None) -> AdminUserItem:
    """任命/撤销普通管理员：仅超管可操作，且只能作用于已审核的普通用户"""
    if operator.role != "super_admin":
        raise PermissionDeniedException("只有超级管理员可以任命管理员")

    target = await user_service.get_user(db, target_id)
    if target.id == operator.id:
        raise ValidationException("不能修改自己的角色")
    if target.role == "super_admin":
        raise PermissionDeniedException("不能修改超级管理员的角色")
    if target.status != "active":
        raise ValidationException("只能任命已审核通过的活跃用户")
    if target.role == req.role:
        return _item(target)

    before = _state(target)
    target.role = req.role
    if req.role == "admin":
        # 管理员不再归属任何分组
        target.managed_by = None
    else:
        # 撤销管理员：原组内用户转给其他人，本人作为普通用户重新分配
        await _reassign_group(db, target.id)
        await _assign_next(db, target)
    # 权限变化必须立即生效：旧 Token 失效，全部 refresh Token 吊销
    target.token_version += 1
    await _revoke_all_tokens(db, target.id)

    action = "grant_admin" if req.role == "admin" else "revoke_admin"
    db.add(AuditLog(
        operator_id=operator.id,
        action=action,
        target_type="user",
        target_id=target.id,
        before_state=json.dumps(before, ensure_ascii=False),
        after_state=json.dumps(_state(target), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("超级管理员 %s %s 用户 %s 管理员权限", operator.id, action, target.id)
    return _item(target)


async def set_manager(db: AsyncSession, operator: User, target_id: int,
                      manager_id: int | None,
                      ip: str | None = None) -> AdminUserItem:
    """超管手动调整普通用户的归属管理员：manager_id=None 表示移回未分配"""
    if operator.role != "super_admin":
        raise PermissionDeniedException("只有超级管理员可以调整分组")

    target = await user_service.get_user(db, target_id)
    if target.role != "user":
        raise ValidationException("只有普通用户参与分组")
    if target.managed_by == manager_id:
        return _item(target, await _manager_name(db, manager_id))

    manager_name = None
    if manager_id is not None:
        manager = await user_service.get_user(db, manager_id)
        if manager.role != "admin":
            raise ValidationException("目标用户不是管理员")
        if manager.status != "active":
            raise ValidationException("目标管理员未启用")
        manager_name = manager.username

    before = _state(target)
    target.managed_by = manager_id
    db.add(AuditLog(
        operator_id=operator.id,
        action="reassign_user",
        target_type="user",
        target_id=target.id,
        before_state=json.dumps(before, ensure_ascii=False),
        after_state=json.dumps(_state(target), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("超级管理员 %s reassign_user 用户 %s 归属改为 %s",
                operator.id, target.id, manager_id)
    return _item(target, manager_name)


async def batch_review(db: AsyncSession, operator: User, user_ids: list[int],
                       action: str, reason: str | None,
                       ip: str | None = None) -> BatchReviewResponse:
    """批量审核：approve 通过待审核用户，reject 拒绝（置为禁用）"""
    updated = 0
    skipped: list[BatchSkipped] = []

    for user_id in dict.fromkeys(user_ids):  # 去重且保持顺序
        try:
            target = await user_service.get_user(db, user_id)
        except NotFoundException:
            skipped.append(BatchSkipped(user_id=user_id, reason="用户不存在"))
            continue

        if operator.role != "super_admin" and target.managed_by != operator.id:
            skipped.append(BatchSkipped(user_id=user_id, reason="不属于你管辖的用户"))
            continue

        if action == "approve":
            if target.status == "active":
                skipped.append(BatchSkipped(user_id=user_id, reason="无需重复审核"))
                continue
            if target.status == "disabled":
                skipped.append(BatchSkipped(user_id=user_id, reason="账号已禁用，请单独启用"))
                continue
            req = UpdateUserStatusRequest(status="active")
        else:
            if target.role == "super_admin":
                skipped.append(BatchSkipped(user_id=user_id, reason="不能拒绝超级管理员"))
                continue
            if target.id == operator.id:
                skipped.append(BatchSkipped(user_id=user_id, reason="不能拒绝自己"))
                continue
            req = UpdateUserStatusRequest(
                status="disabled", reason=reason or "审核未通过，请联系管理员"
            )

        try:
            await set_status(db, operator, user_id, req, ip)
            updated += 1
        except (PermissionDeniedException, ValidationException) as exc:
            skipped.append(BatchSkipped(user_id=user_id, reason=exc.message))

    return BatchReviewResponse(updated=updated, action=action, skipped=skipped)


async def group_summary(db: AsyncSession) -> AdminGroupsResponse:
    """超管分组视图：每个活跃管理员管辖的用户分布 + 未分配人数"""
    managers = await _active_managers(db)
    counts = {m.id: {"pending": 0, "active": 0, "disabled": 0} for m in managers}

    if counts:
        rows = await db.execute(
            select(User.managed_by, User.status, func.count())
            .where(User.role == "user", User.managed_by.in_(list(counts)))
            .group_by(User.managed_by, User.status)
        )
        for mid, status, cnt in rows:
            counts[mid][status] = int(cnt)

    unassigned = int(await db.scalar(
        select(func.count()).select_from(User)
        .where(User.role == "user", User.managed_by.is_(None))
    ) or 0)
    unassigned_pending = int(await db.scalar(
        select(func.count()).select_from(User)
        .where(
            User.role == "user",
            User.managed_by.is_(None),
            User.status == "pending",
        )
    ) or 0)

    return AdminGroupsResponse(
        items=[
            AdminGroupItem(
                manager_id=manager.id,
                manager_name=manager.username,
                pending=counts[manager.id]["pending"],
                active=counts[manager.id]["active"],
                disabled=counts[manager.id]["disabled"],
                total=sum(counts[manager.id].values()),
            )
            for manager in managers
        ],
        unassigned=unassigned,
        unassigned_pending=unassigned_pending,
    )
