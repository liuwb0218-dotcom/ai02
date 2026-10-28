"""模型会话 — 参数校验、调用模型、落库并返回回答（含调用日志）"""
from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DatabaseException, ModelCallException, ValidationException
from app.models.conversation import APILog, ChatSession
from app.schemas.chat import ChatRequest, ChatResponse
from app.services import llm_client, model_config_service, user_service

logger = logging.getLogger("app.chat")


async def ask(db: AsyncSession, req: ChatRequest) -> ChatResponse:
    """问答主流程：校验用户与模型 → 调模型 → 写 chat_sessions + api_call_logs"""
    # 1) 用户必须存在，否则后续写入会触发外键错误（提前拦截为友好 404）
    await user_service.get_user(db, req.user_id)

    # 2) 去除首尾空白后判空：Pydantic 的 min_length 拦不住 "   " 这种纯空格
    question = req.question.strip()
    if not question:
        raise ValidationException("问题不能为空")

    # 3) 解析模型配置：用户自建优先，其次内置；不存在或已停用抛 404
    target = await model_config_service.resolve(db, req.user_id, req.model_name)

    # 4) 调模型：超时/连接失败/上游 4xx 5xx 统一抛 502
    try:
        answer, latency_ms = await llm_client.chat(question, target)
    except ModelCallException as exc:
        # 失败也要留调用日志，session_id 为空（此时还没有会话记录）
        await _write_log(db, req.user_id, None, req.model_name,
                         "error", 0, str(exc.message))
        raise

    # 5) 成功：先落会话，flush 拿到自增 ID，再写日志，最后统一提交
    session = ChatSession(
        user_id=req.user_id,
        model_name=req.model_name,
        question=question,
        answer=answer,
    )
    db.add(session)
    try:
        await db.flush()                      # 拿到自增 session_id
        await _write_log(db, req.user_id, session.id, req.model_name,
                         "success", latency_ms, None)
        await db.commit()
    except SQLAlchemyError as exc:
        # 会话与日志在同一事务中，任一失败整体回滚，避免数据不一致
        await db.rollback()
        logger.exception("问答落库失败")
        raise DatabaseException("问答记录保存失败") from exc

    return ChatResponse(
        session_id=session.id,
        answer=answer,
        model_name=req.model_name,
        latency_ms=latency_ms,
        status="success",
    )


async def _write_log(db: AsyncSession, user_id: int, session_id: int | None,
                     model_name: str, status: str, latency_ms: int,
                     error_message: str | None) -> None:
    """写一条模型调用日志，成功与失败都调用

    error 分支先单独 commit：即使随后异常向上抛出，日志也不会因事务回滚而丢失。
    """
    db.add(APILog(
        user_id=user_id,
        session_id=session_id,
        model_name=model_name,
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
    ))
    await db.commit()
