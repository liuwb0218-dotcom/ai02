"""用户登录、审核状态、Token 白名单与审计日志

Revision ID: 20261028_0001
Revises:
Create Date: 2026-10-28

说明：create_all 不会为已存在的 users 表补列，所以必须走本迁移。
存量用户统一设为 active（迁移前即可用），新注册用户由接口显式写入 pending。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users：登录凭据、角色与审核状态 ──────────────
    op.add_column("users", sa.Column(
        "password_hash", sa.Text(), nullable=False, server_default=sa.text("''")
    ))
    op.add_column("users", sa.Column(
        "role", sa.Text(), nullable=False, server_default=sa.text("'user'")
    ))
    op.add_column("users", sa.Column(
        "status", sa.Text(), nullable=False, server_default=sa.text("'active'")
    ))
    op.add_column("users", sa.Column("trial_expires_at", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("disabled_until", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("disabled_reason", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.Text(), nullable=True))
    op.add_column("users", sa.Column(
        "token_version", sa.Integer(), nullable=False, server_default=sa.text("0")
    ))
    op.create_index("idx_user_role", "users", ["role"])
    op.create_index("idx_user_status", "users", ["status"])

    # ── refresh token 白名单 ─────────────────────────
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now','localtime'))")),
    )
    op.create_index("idx_token_user", "user_tokens", ["user_id"])
    op.create_index("idx_token_hash", "user_tokens", ["token_hash"])

    # ── 管理员操作审计 ───────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("before_state", sa.Text(), nullable=True),
        sa.Column("after_state", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now','localtime'))")),
    )
    op.create_index("idx_audit_operator", "audit_log", ["operator_id", "created_at"])
    op.create_index("idx_audit_target", "audit_log", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("idx_audit_target", table_name="audit_log")
    op.drop_index("idx_audit_operator", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("idx_token_hash", table_name="user_tokens")
    op.drop_index("idx_token_user", table_name="user_tokens")
    op.drop_table("user_tokens")

    op.drop_index("idx_user_status", table_name="users")
    op.drop_index("idx_user_role", table_name="users")
    for column in (
        "token_version", "last_login_at", "disabled_reason",
        "disabled_until", "trial_expires_at", "status", "role", "password_hash",
    ):
        op.drop_column("users", column)
