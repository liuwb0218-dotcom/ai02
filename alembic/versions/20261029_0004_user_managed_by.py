"""users 增加归属管理员字段

Revision ID: 20261029_0004
Revises: 20261028_0003
Create Date: 2026-10-29

普通用户按注册顺序轮询分配给管理员：
  - managed_by 为空表示未分配（没有管理员时的新用户）
  - 仅普通用户有归属；admin / super_admin 恒为 NULL
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0004"
down_revision = "20261028_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("managed_by", sa.Integer(), nullable=True))
    op.create_index("idx_user_managed_by", "users", ["managed_by"])


def downgrade() -> None:
    op.drop_index("idx_user_managed_by", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("managed_by")
