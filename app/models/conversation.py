"""ORM 模型 — 本期三张核心表：users / chat_sessions / api_call_logs

字段设计约定（与需求文档 5.2 一致）：
  - SQLite 无原生 DATETIME，时间统一用 Text 存 ISO 字符串
  - 默认时间由数据库生成（server_default），避免应用侧时钟不一致
  - 主键统一 Integer 自增，不使用 UUID（SQLite 下索引更紧凑）
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base

# SQLite 本地时间默认值（与服务端时钟一致）
NOW = text("(datetime('now','localtime'))")


class User(Base):
    """用户表 — 支撑 user_id 会话隔离

    P0 仅保存最小信息；P1 增加 password_hash / status / role 等字段。
    """

    __tablename__ = "users"

    # 主键：自增整数，会话表通过 user_id 外键关联
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 用户名：唯一约束，注册接口据此判重
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # 创建时间：数据库侧默认值，插入时无需应用传入
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)


class ChatSession(Base):
    """会话历史表 — 一条记录保存一轮问答

    P0 用单表存"一问一答"；P1 拆为 chat_sessions + messages 支持多轮上下文。
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        # 单列索引：按用户查历史（最高频查询）
        Index("idx_session_user", "user_id"),
        # 联合索引：按用户查询并按时间倒序，命中索引避免额外排序
        Index("idx_session_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 归属用户：外键指向 users.id，用于会话隔离与越权校验
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    # 本次问答使用的模型名（mock / 真实模型），便于按模型统计用量
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 用户问题原文
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # 模型回答原文
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # 问答时间：历史列表倒序排序依据
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    # 软删除标记：0=正常，1=已删除；查询历史时过滤掉 1
    is_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 删除时间：NULL 表示未删除，由定时任务按此字段清理过期数据
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class APILog(Base):
    """模型调用日志表 — 每次模型调用无论成功失败都写一条

    失败记录的 session_id 为 NULL（调用失败时还没有会话 ID），
    因此不能对 session_id 加非空约束或外键约束。
    """

    __tablename__ = "api_call_logs"
    __table_args__ = (
        # 状态只允许两种取值，防止写入脏数据
        CheckConstraint("status IN ('success','error')", name="ck_api_log_status"),
        # 联合索引：按用户 + 时间统计调用量与失败率
        Index("idx_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 发起调用的用户 ID（不加外键：日志需长期保留，用户删除后仍要可追溯）
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 关联的会话 ID；调用失败时为 NULL
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 本次调用的模型名
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 调用结果：success / error
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # 端到端耗时（毫秒）；失败时记 0
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 失败原因（如"模型调用超时"）；成功时为 NULL
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 调用发生时间
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
