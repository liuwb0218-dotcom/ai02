"""api_call_logs 增加 Token 用量字段

Revision ID: 20261028_0003
Revises: 20261028_0002
Create Date: 2026-10-28

为调用日志补充 prompt/completion/total tokens：
  - 真实模型取上游响应 usage；mock 按字符数估算；失败记 0
  - SQLite 的 ADD COLUMN 要求非空列必须带默认值，因此统一 server_default=0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0003"
down_revision = "20261028_0002"
branch_labels = None
depends_on = None

COLUMNS = ("prompt_tokens", "completion_tokens", "total_tokens")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "api_call_logs",
            sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    # SQLite 删列用 batch 模式重建表，兼容旧版本 SQLite 不支持 DROP COLUMN
    with op.batch_alter_table("api_call_logs") as batch:
        for name in COLUMNS:
            batch.drop_column(name)
