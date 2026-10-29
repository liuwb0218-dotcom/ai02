"""公告表与已读记录

Revision ID: 20261028_0002
Revises: 20261028_0001
Create Date: 2026-10-28

新建两张表：announcements（公告）、announcement_reads（按用户去重的已读记录）。
create_all 也能建新表，但线上以 Alembic 为准，保证迁移历史完整。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0002"
down_revision = "20261028_0001"
branch_labels = None
depends_on = None

NOW = sa.text("(datetime('now','localtime'))")


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False, server_default=sa.text("'info'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("publish_at", sa.Text(), nullable=False, server_default=NOW),
        sa.Column("expire_at", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=NOW),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "level IN ('info','warning','critical')",
            name="ck_announcement_level",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','revoked')",
            name="ck_announcement_status",
        ),
    )
    op.create_index(
        "idx_announcement_status_publish", "announcements", ["status", "publish_at"]
    )

    op.create_table(
        "announcement_reads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "announcement_id", sa.Integer(),
            sa.ForeignKey("announcements.id"), nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("first_read_at", sa.Text(), nullable=False, server_default=NOW),
        sa.Column("last_read_at", sa.Text(), nullable=False, server_default=NOW),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_index(
        "uidx_announcement_read", "announcement_reads",
        ["announcement_id", "user_id"], unique=True,
    )
    op.create_index(
        "idx_announcement_read_user", "announcement_reads", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_announcement_read_user", table_name="announcement_reads")
    op.drop_index("uidx_announcement_read", table_name="announcement_reads")
    op.drop_table("announcement_reads")

    op.drop_index("idx_announcement_status_publish", table_name="announcements")
    op.drop_table("announcements")
