"""ORM 模型 — model_configs（模型配置表）

设计目标：让"模型"从 .env 静态配置变成可管理的数据
  - owner_user_id 为 NULL：系统内置模型（mock / DeepSeek / 本地模型），所有用户可见
  - owner_user_id 非空：用户自建模型，仅本人可见可用
  - name 是 /api/chat 的 model_name 取值，同一用户下不可重名
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base

NOW = text("(datetime('now','localtime'))")


class ModelConfig(Base):
    """模型配置：一条记录代表一个可在 /api/models 中被选中的模型"""

    __tablename__ = "model_configs"
    __table_args__ = (
        # 同一归属下 name 唯一；owner_user_id 为 NULL 的内置配置由代码保证不重复插入
        Index("uidx_model_owner_name", "owner_user_id", "name", unique=True),
        # 只允许四类 provider，避免拼错导致调用分支走错
        CheckConstraint(
            "provider IN ('mock','deepseek','local','custom')",
            name="ck_model_provider",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 归属用户：NULL = 内置（全平台可见），非空 = 用户自建（仅本人可见）
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    # 模型标识：/api/chat 请求里的 model_name 就是它
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 前端下拉框展示名称
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 调用协议：mock（本地模拟）/ deepseek / local / custom，除 mock 外都走 OpenAI 兼容接口
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # 服务地址，如 https://api.deepseek.com/v1；mock 为 NULL
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 上游真实模型名，如 deepseek-chat、qwen2.5:7b
    upstream_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 加密后的 API Key（AES-256-GCM）；为空表示该模型无需鉴权（常见于本地模型）
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    # 是否启用：停用后不在列表中返回、也不能被选择
    is_enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=NOW)
