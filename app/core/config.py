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

    # ── 本地模型（Ollama / vLLM / LM Studio 等 OpenAI 兼容服务）──
    # 本地模型通常免鉴权，且首次推理要加载权重，所以超时单独放大
    LOCAL_MODEL_BASE_URL: str = "http://127.0.0.1:11434/v1"
    LOCAL_MODEL_NAME: str = "qwen2.5:7b"
    LOCAL_MODEL_API_KEY: str = ""
    LOCAL_MODEL_TIMEOUT_SECONDS: int = 60

    # ── JWT 双 Token（P1）───────────────────────────
    JWT_SECRET: str = "change_me_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # ── 账号与审批（P1）─────────────────────────────
    # 内置超级管理员：首次启动自动创建，已存在时不覆盖密码
    SUPER_ADMIN_USERNAME: str = "superadmin"
    SUPER_ADMIN_PASSWORD: str = "Admin@123456"
    # 注册后先给临时使用权，管理员审核通过才转为永久
    REGISTER_TRIAL_DAYS: int = 7
    # 仅开发环境可打开：注册即视为审核通过（跳过试用与审核流程）
    AUTO_APPROVE_REGISTER: bool = False
    # 是否强制登录。False 时允许旧的 X-User-Id 调试方式（仅本地联调）
    AUTH_REQUIRED: bool = True

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

    # ── 日志（技术日志落文件 + 调用日志保留）──────────
    LOG_DIR: str = "./logs"
    LOG_LEVEL: str = "INFO"
    # 文件日志与 api_call_logs 统一保留天数；audit_log 永久保留
    LOG_RETENTION_DAYS: int = 30

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
