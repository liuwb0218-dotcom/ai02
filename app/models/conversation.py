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
    """用户表 — 登录凭据、角色与审核状态

    三层角色：super_admin（内置超管）/ admin（管理员）/ user（普通用户）。
    审核状态：pending（待审核，7 天试用）/ active（已审核，永久）/ disabled（禁用）。
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('super_admin','admin','user')",
                        name="ck_user_role"),
        CheckConstraint("status IN ('pending','active','disabled')",
                        name="ck_user_status"),
        Index("idx_user_role", "role"),
        Index("idx_user_status", "status"),
        Index("idx_user_managed_by", "managed_by"),
    )

    # 主键：自增整数，会话表通过 user_id 外键关联
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 用户名：唯一约束，注册接口据此判重
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # bcrypt 哈希；空字符串表示该账号尚未设置密码（如迁移前的演示用户）
    password_hash: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # 角色：决定可访问的接口范围
    role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'user'")
    )
    # 状态：pending=待审核（试用期）/ active=已审核 / disabled=禁用
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    # 归属管理员：普通用户审核通过时按顺序轮询分配；管理员与超管为 NULL
    managed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 试用到期时间：仅 pending 有效；审核通过后允许为空表示永久
    trial_expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 禁用到期时间：为空表示永久禁用（由管理员手动恢复）
    disabled_until: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 禁用原因（对用户可见的提示文案）
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 最近一次登录时间
    last_login_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Token 版本：+1 后旧 access/refresh 全部失效（禁用、改密、全端登出）
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 创建时间：数据库侧默认值，插入时无需应用传入
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)


class UserToken(Base):
    """Refresh Token 白名单 — 只存 SHA-256，不落明文

    签发时写入一条；刷新时校验并轮换（旧记录置 revoked_at）；
    登出/禁用/改密时批量置 revoked_at，或直接提升 users.token_version。
    """

    __tablename__ = "user_tokens"
    __table_args__ = (
        Index("idx_token_user", "user_id"),
        Index("idx_token_hash", "token_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    # refresh token 的 SHA-256 十六进制串，数据库泄露也无法直接复用
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)


class AuditLog(Base):
    """管理员操作审计 — 审核、启用、禁用、改角色都留痕

    before_state / after_state 仅保存 status/role/disabled_reason 等非敏感字段。
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_operator", "operator_id", "created_at"),
        Index("idx_audit_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 操作人管理员 ID（不设外键：管理员账号删除后仍需可追溯）
    operator_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 动作：approve / reject / enable / disable
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    before_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # ── Token 用量：真实模型取上游 usage，mock 按字符估算，失败时为 0 ──
    # 提问消耗的 token
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 回答消耗的 token
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 合计 = prompt + completion
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 失败原因（如"模型调用超时"）；成功时为 NULL
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 调用发生时间
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
