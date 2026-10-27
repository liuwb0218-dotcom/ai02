"""历史记录 — 按用户查询、按时间倒序、软删除并校验归属"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DatabaseException, NotFoundException
from app.models.conversation import ChatSession
from app.schemas.chat import DeleteResponse, HistoryItem, HistoryResponse
from app.services import user_service

logger = logging.getLogger("app.history")


async def list_history(db: AsyncSession, user_id: int,
                       limit: int = 20, offset: int = 0,
                       current_user_id: int | None = None) -> HistoryResponse:
    """查询指定用户未删除的问答记录，按 created_at 倒序"""
    # 先鉴权再查库：避免越权请求通过"404 与否"探测他人记录是否存在
    await user_service.check_owner(current_user_id, user_id)
    await user_service.get_user(db, user_id)

    # 只查未删除记录；后续 total 与分页查询共用同一个过滤条件
    base = (select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.is_deleted == 0))

    # total 与列表分开统计：total 不受 limit/offset 影响，供前端做分页
    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    )
    # 倒序用 created_at，同一秒内多条时用 id 兜底，保证顺序稳定
    rows = (await db.scalars(
        base.order_by(ChatSession.created_at.desc(), ChatSession.id.desc())
        .limit(limit).offset(offset)
    )).all()

    return HistoryResponse(
        user_id=user_id,
        total=int(total or 0),
        items=[
            HistoryItem(
                session_id=r.id,
                question=r.question,
                answer=r.answer,
                model_name=r.model_name,
                created_at=r.created_at,
            ) for r in rows
        ],
    )


async def soft_delete(db: AsyncSession, session_id: int,
                      current_user_id: int | None = None) -> DeleteResponse:
    """软删除会话；重复删除同一 session_id 按幂等处理"""
    session = await db.scalar(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    if session is None:
        raise NotFoundException(f"会话 {session_id} 不存在")

    # 归属校验放在"存在性判断"之后：会话不存在时返回 404 而非 403
    await user_service.check_owner(current_user_id, session.user_id)

    # 已删除直接返回成功，前端重复点击不会报错
    if session.is_deleted:
        return DeleteResponse(deleted=True, session_id=session_id)

    try:
        # 用 UPDATE 而非修改 ORM 对象：避免并发删除时的脏写
        await db.execute(
            ChatSession.__table__.update()
            .where(ChatSession.id == session_id)
            .values(is_deleted=1, deleted_at=func.datetime("now", "localtime"))
        )
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("软删除失败 session_id=%s", session_id)
        raise DatabaseException("删除失败") from exc

    return DeleteResponse(deleted=True, session_id=session_id)
