"""
应用配置 — 从 .env 文件 + 环境变量读取，用 Pydantic Settings 校验
变量名对齐《AI 智能问答平台》需求文档 12.1 的 .env.example 要求
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：从本文件(app/core/config.py)向上三级，用于定位 .env 与 data/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """全局配置，所有字段可在 .env 中覆盖"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 应用基础 ────────────────────────────────────
    APP_NAME: str = "AI 智能问答平台"
    APP_ENV: str = "dev"          # dev / prod
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # ── SQLite ──────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    # ── 模型配置（本期 P0 必需）─────────────────────
    DEFAULT_MODEL: str = "deepseek-chat"
    MODEL_BASE_URL: str = "https://api.deepseek.com/v1"
    MODEL_API_KEY: str = ""
    MODEL_TIMEOUT_SECONDS: int = 10

    # ── JWT 双 Token（P1）───────────────────────────
    JWT_SECRET: str = "change_me_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # ── AES-256 加密 API Key 主密钥（P2）───────────
    MASTER_ENCRYPTION_KEY: str = "change_me_aes_key_32bytes_long!!!"

    # ── ChromaDB（P2）───────────────────────────────
    CHROMA_PERSIST_DIR: str = "./data/chroma"
    CHROMA_COLLECTION_NAME: str = "knowledge_docs"

    # ── 安全 / 配额（P2）────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 10
    DEFAULT_DAILY_TOKEN_QUOTA: int = 100_000
    CONTEXT_TRUNCATE_TURNS: int = 20

    # ── 定时任务（P2）───────────────────────────────
    SOFT_DELETE_CLEANUP_DAYS: int = 30

    # ── 上传目录 ────────────────────────────────────
    UPLOAD_DIR: str = "./uploads"

    # ── 辅助属性 ────────────────────────────────────
    @property
    def is_dev(self) -> bool:
        """是否开发环境：为 True 时启动会预置演示用户、放开调试日志"""
        return self.APP_ENV == "dev"

    @property
    def database_path(self) -> Path:
        """把 sqlite+aiosqlite:///./data/app.db 解析为绝对 Path

        用途：创建父目录；避免直接拿 DATABASE_URL 字符串当文件路径。
        """
        rel = self.DATABASE_URL.split("sqlite+aiosqlite:///", 1)[-1]
        return (BASE_DIR / rel).resolve()


# 全局单例：其余模块直接 `from app.core.config import settings`
settings = Settings()
