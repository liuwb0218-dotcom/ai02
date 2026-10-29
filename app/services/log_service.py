"""调用日志 — 用户自查、管理员查询、用量汇总与过期清理

时间窗口统一交给 SQLite 计算（datetime('now','localtime','-N days')），
避免应用侧生成的字符串与 created_at 的存储格式不一致导致比较失效。
"""
from __future__ import annotations

import logging

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DatabaseException
from app.models.conversation import APILog, User
from app.schemas.log import LogItem, LogsResponse, LogSummary

logger = logging.getLogger("app.logs")


def _since(days: int):
    """最近 N 天的起点，返回 SQLite 表达式（与 created_at 同为本地时间格式）"""
    return func.datetime("now", "localtime", f"-{int(days)} days")


async def list_logs(db: AsyncSession, *, user_id: int | None = None,
                    managed_by: int | None = None,
                    status: str | None = None, model_name: str | None = None,
                    days: int | None = None,
                    page: int = 1, page_size: int = 20) -> LogsResponse:
    """分页查询调用日志；user_id 为空表示查全部（仅管理端使用）

    managed_by 用于普通管理员：只返回其管辖用户的日志；
    username 通过外连接补充：日志不设外键，用户删除后记录仍要可查。
    """
    conditions = []
    if user_id is not None:
        conditions.append(APILog.user_id == user_id)
    if managed_by is not None:
        conditions.append(APILog.user_id.in_(
            select(User.id).where(User.managed_by == managed_by)
        ))
    if status:
        conditions.append(APILog.status == status)
    if model_name:
        conditions.append(APILog.model_name == model_name)
    if days is not None:
        conditions.append(APILog.created_at >= _since(days))

    # total 单独统计：count 不需要 join，避免外连接影响行数
    total = await db.scalar(
        select(func.count(APILog.id)).where(*conditions)
    )

    rows = (await db.execute(
        select(APILog, User.username)
        .outerjoin(User, User.id == APILog.user_id)
        .where(*conditions)
        .order_by(APILog.created_at.desc(), APILog.id.desc())
        .limit(page_size).offset((page - 1) * page_size)
    )).all()

    return LogsResponse(
        items=[
            LogItem(
                id=log.id,
                user_id=log.user_id,
                username=username,
                session_id=log.session_id,
                model_name=log.model_name,
                status=log.status,
                latency_ms=log.latency_ms,
                prompt_tokens=log.prompt_tokens,
                completion_tokens=log.completion_tokens,
                total_tokens=log.total_tokens,
                error_message=log.error_message,
                created_at=log.created_at,
            ) for log, username in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


async def user_summary(db: AsyncSession, user_id: int,
                       days: int = 30) -> LogSummary:
    """统计指定用户最近 N 天的调用次数、成功率、平均耗时与 Token 消耗"""
    total, success, avg_latency, tokens = (await db.execute(
        select(
            func.count(APILog.id),
            func.sum(case((APILog.status == "success", 1), else_=0)),
            # 失败调用的 latency 恒为 0，参与平均会拉低结果，因此只统计成功调用
            func.avg(case((APILog.status == "success", APILog.latency_ms))),
            func.coalesce(func.sum(APILog.total_tokens), 0),
        ).where(
            APILog.user_id == user_id,
            APILog.created_at >= _since(days),
        )
    )).one()

    total = int(total or 0)
    success = int(success or 0)
    return LogSummary(
        days=days,
        total_calls=total,
        success_calls=success,
        error_calls=total - success,
        success_rate=round(success / total, 4) if total else 0.0,
        avg_latency_ms=int(round(avg_latency or 0)),
        total_tokens=int(tokens or 0),
    )


async def purge_old_logs(db: AsyncSession, days: int) -> int:
    """删除 days 天前的调用日志，返回删除条数；audit_log 不参与清理

    days=0 表示清空全部历史（仅用于本地验证），负数视为非法直接跳过。
    """
    if days < 0:
        return 0
    try:
        result = await db.execute(
            delete(APILog).where(APILog.created_at < _since(days))
        )
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("清理过期调用日志失败")
        raise DatabaseException("清理调用日志失败") from exc
    return int(result.rowcount or 0)
