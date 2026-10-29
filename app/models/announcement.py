"""公告与已读记录

设计说明：
  - 公告对全部可登录用户可见（试用中 / 已审核），发布人只能是管理员
  - 用户关闭弹窗只对本次登录生效，因此“已读”以点击「知道了」为准
  - announcement_reads 按 (announcement_id, user_id) 唯一，记录首次与最近阅读
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base

NOW = text("(datetime('now','localtime'))")


class Announcement(Base):
    """平台公告：草稿 / 已发布 / 已撤回"""

    __tablename__ = "announcements"
    __table_args__ = (
        CheckConstraint(
            "level IN ('info','warning','critical')",
            name="ck_announcement_level",
        ),
        CheckConstraint(
            "status IN ('draft','published','revoked')",
            name="ck_announcement_status",
        ),
        # 用户端拉取生效公告时按状态 + 生效时间过滤
        Index("idx_announcement_status_publish", "status", "publish_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 纯文本正文，前端按换行渲染，不解析 HTML/Markdown
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 级别：info 普通 / warning 重要 / critical 紧急，仅影响前端配色
    level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'info'")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    # 生效时间：定时发布时大于当前时间，用户端只返回 publish_at <= now 的公告
    publish_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    # 到期时间：NULL 表示长期有效
    expire_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    revoked_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnnouncementRead(Base):
    """公告已读记录：用于管理端触达统计"""

    __tablename__ = "announcement_reads"
    __table_args__ = (
        Index("uidx_announcement_read", "announcement_id", "user_id", unique=True),
        Index("idx_announcement_read_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("announcements.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    first_read_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    last_read_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    # 点击「知道了」的累计次数（公告每次登录都会弹，可用于衡量反复阅读）
    read_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
