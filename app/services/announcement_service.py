"""公告服务 — 用户端待阅/已读，管理端增改撤回与已读统计"""
from __future__ import annotations

import json
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.security import now_text
from app.models.announcement import Announcement, AnnouncementRead
from app.models.conversation import AuditLog, User
from app.schemas.announcement import (
    AdminAnnouncementItem,
    AdminAnnouncementsResponse,
    AnnouncementCounts,
    AnnouncementCreateRequest,
    AnnouncementPublic,
    AnnouncementReadResponse,
    AnnouncementUpdateRequest,
    PendingAnnouncementsResponse,
)

logger = logging.getLogger("app.announcement")


def _visible_filter() -> list:
    """生效中公告的条件：已发布 + 已到生效时间 + 未到期"""
    now = now_text()
    return [
        Announcement.status == "published",
        Announcement.publish_at <= now,
        or_(Announcement.expire_at.is_(None), Announcement.expire_at > now),
    ]


def _public(ann: Announcement) -> AnnouncementPublic:
    return AnnouncementPublic(
        id=ann.id,
        title=ann.title,
        content=ann.content,
        level=ann.level,
        publish_at=ann.publish_at,
        expire_at=ann.expire_at,
    )


def _admin_item(ann: Announcement, creator_name: str | None,
                read_users: int = 0, read_count: int = 0) -> AdminAnnouncementItem:
    return AdminAnnouncementItem(
        **_public(ann).model_dump(),
        status=ann.status,
        created_by=ann.created_by,
        creator_name=creator_name,
        created_at=ann.created_at,
        updated_at=ann.updated_at,
        revoked_at=ann.revoked_at,
        read_users=read_users,
        read_count=read_count,
    )


# ═══════════════════════════════════════════════════
# 用户端
# ═══════════════════════════════════════════════════

async def list_pending(db: AsyncSession) -> PendingAnnouncementsResponse:
    """当前所有生效公告（每次登录都会弹，前端负责本次会话内的关闭状态）"""
    rows = (await db.scalars(
        select(Announcement)
        .where(*_visible_filter())
        .order_by(Announcement.publish_at.desc(), Announcement.id.desc())
    )).all()
    return PendingAnnouncementsResponse(
        items=[_public(a) for a in rows],
        total=len(rows),
    )


async def mark_read(db: AsyncSession, user: User,
                    announcement_id: int) -> AnnouncementReadResponse:
    """记录已读：按 (公告, 用户) 去重，重复点击只累加次数"""
    ann = await db.get(Announcement, announcement_id)
    now = now_text()
    if ann is None or not (
        ann.status == "published"
        and ann.publish_at <= now
        and (ann.expire_at is None or ann.expire_at > now)
    ):
        raise NotFoundException("公告不存在或已下线")

    row = await db.scalar(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.user_id == user.id,
        )
    )
    if row is None:
        row = AnnouncementRead(
            announcement_id=announcement_id,
            user_id=user.id,
            first_read_at=now,
            last_read_at=now,
            read_count=1,
        )
        db.add(row)
    else:
        row.read_count += 1
        row.last_read_at = now

    await db.commit()
    return AnnouncementReadResponse(
        announcement_id=announcement_id,
        read_count=row.read_count,
        first_read_at=row.first_read_at,
    )


# ═══════════════════════════════════════════════════
# 管理端
# ═══════════════════════════════════════════════════

async def admin_list(db: AsyncSession, *, status: str | None = None,
                     keyword: str | None = None,
                     page: int = 1, page_size: int = 20
                     ) -> AdminAnnouncementsResponse:
    conditions = []
    if status:
        conditions.append(Announcement.status == status)
    if keyword:
        conditions.append(Announcement.title.like(f"%{keyword.strip()}%"))

    total = int(await db.scalar(
        select(func.count()).select_from(Announcement).where(*conditions)
    ) or 0)

    rows = (await db.scalars(
        select(Announcement).where(*conditions)
        .order_by(Announcement.publish_at.desc(), Announcement.id.desc())
        .limit(page_size).offset((page - 1) * page_size)
    )).all()
    ids = [a.id for a in rows]

    # 已读统计：按公告聚合去重人数与累计次数
    stats: dict[int, tuple[int, int]] = {}
    creator_names: dict[int, str] = {}
    if ids:
        for ann_id, users, times in (await db.execute(
            select(
                AnnouncementRead.announcement_id,
                func.count(AnnouncementRead.id),
                func.coalesce(func.sum(AnnouncementRead.read_count), 0),
            )
            .where(AnnouncementRead.announcement_id.in_(ids))
            .group_by(AnnouncementRead.announcement_id)
        )).all():
            stats[ann_id] = (int(users), int(times))

        for uid, name in (await db.execute(
            select(User.id, User.username).where(
                User.id.in_({a.created_by for a in rows})
            )
        )).all():
            creator_names[uid] = name

    async def _count(*conds) -> int:
        return int(await db.scalar(
            select(func.count()).select_from(Announcement).where(*conds)
        ) or 0)

    return AdminAnnouncementsResponse(
        items=[
            _admin_item(
                a,
                creator_names.get(a.created_by),
                read_users=stats.get(a.id, (0, 0))[0],
                read_count=stats.get(a.id, (0, 0))[1],
            )
            for a in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        user_total=int(await db.scalar(
            select(func.count()).select_from(User)
        ) or 0),
        counts=AnnouncementCounts(
            total=await _count(),
            published=await _count(Announcement.status == "published"),
            draft=await _count(Announcement.status == "draft"),
            revoked=await _count(Announcement.status == "revoked"),
        ),
    )


def _state(ann: Announcement) -> dict:
    return {
        "title": ann.title,
        "level": ann.level,
        "status": ann.status,
        "publish_at": ann.publish_at,
        "expire_at": ann.expire_at,
    }


def _check_window(publish_at: str, expire_at: str | None) -> None:
    """到期时间必须晚于生效时间；字符串已是 YYYY-MM-DD HH:MM:SS，可直接比较"""
    if expire_at and expire_at <= publish_at:
        raise ValidationException("到期时间必须晚于生效时间")


async def _get(db: AsyncSession, announcement_id: int) -> Announcement:
    ann = await db.get(Announcement, announcement_id)
    if ann is None:
        raise NotFoundException("公告不存在")
    return ann


async def create(db: AsyncSession, operator: User,
                 req: AnnouncementCreateRequest,
                 ip: str | None = None) -> AdminAnnouncementItem:
    """新建公告：可立即发布、定时发布或存草稿"""
    now = now_text()
    publish_at = req.publish_at or now
    _check_window(publish_at, req.expire_at)

    ann = Announcement(
        title=req.title,
        content=req.content,
        level=req.level,
        status=req.status,
        publish_at=publish_at,
        expire_at=req.expire_at,
        created_by=operator.id,
        created_at=now,
        updated_at=now,
    )
    db.add(ann)
    await db.flush()

    db.add(AuditLog(
        operator_id=operator.id,
        action="announcement_create",
        target_type="announcement",
        target_id=ann.id,
        before_state=None,
        after_state=json.dumps(_state(ann), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("管理员 %s 创建公告 %s", operator.id, ann.id)
    return _admin_item(ann, operator.username)


async def update(db: AsyncSession, operator: User, announcement_id: int,
                 req: AnnouncementUpdateRequest,
                 ip: str | None = None) -> AdminAnnouncementItem:
    """编辑公告：已撤回的不可再改；expire_at 显式传空表示清除"""
    ann = await _get(db, announcement_id)
    if ann.status == "revoked":
        raise ValidationException("已撤回的公告不能再编辑")

    before = _state(ann)
    fields = req.model_fields_set
    if "title" in fields and req.title is not None:
        ann.title = req.title
    if "content" in fields and req.content is not None:
        ann.content = req.content
    if "level" in fields and req.level is not None:
        ann.level = req.level
    if "publish_at" in fields and req.publish_at is not None:
        ann.publish_at = req.publish_at
    if "expire_at" in fields:
        ann.expire_at = req.expire_at
    if "status" in fields and req.status is not None:
        ann.status = req.status

    _check_window(ann.publish_at, ann.expire_at)
    ann.updated_at = now_text()

    db.add(AuditLog(
        operator_id=operator.id,
        action="announcement_update",
        target_type="announcement",
        target_id=ann.id,
        before_state=json.dumps(before, ensure_ascii=False),
        after_state=json.dumps(_state(ann), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("管理员 %s 编辑公告 %s", operator.id, ann.id)
    return _admin_item(ann, operator.username)


async def revoke(db: AsyncSession, operator: User, announcement_id: int,
                 ip: str | None = None) -> AdminAnnouncementItem:
    """撤回公告：用户端立即不可见，已读记录保留；重复撤回幂等"""
    ann = await _get(db, announcement_id)
    if ann.status == "revoked":
        return _admin_item(ann, None)

    before = _state(ann)
    ann.status = "revoked"
    ann.revoked_at = now_text()
    ann.updated_at = ann.revoked_at

    db.add(AuditLog(
        operator_id=operator.id,
        action="announcement_revoke",
        target_type="announcement",
        target_id=ann.id,
        before_state=json.dumps(before, ensure_ascii=False),
        after_state=json.dumps(_state(ann), ensure_ascii=False),
        ip=ip,
    ))
    await db.commit()
    logger.info("管理员 %s 撤回公告 %s", operator.id, ann.id)
    return _admin_item(ann, operator.username)
