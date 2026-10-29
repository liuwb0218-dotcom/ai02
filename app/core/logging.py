"""日志配置 — 控制台 + logs/app.log 按天轮转（保留 LOG_RETENTION_DAYS 天）

约束：日志只记元信息，禁止写入 Prompt、回答、API Key、密码哈希等敏感内容。
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from app.core.config import BASE_DIR, settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging() -> None:
    """初始化根日志：控制台与文件双通道，替换 main.py 里原来的 basicConfig"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # 重复调用（--reload / 测试）时不重复挂载，否则每条日志会写两遍
    if any(isinstance(h, TimedRotatingFileHandler) for h in root.handlers):
        return

    formatter = logging.Formatter(LOG_FORMAT)

    # 控制台：保留开发时的实时输出
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    log_dir = (BASE_DIR / settings.LOG_DIR).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    # 按天切分：当前写 app.log，历史文件自动改名 app.log.YYYY-MM-DD
    file_handler = TimedRotatingFileHandler(
        log_dir / "app.log",
        when="midnight",
        interval=1,
        backupCount=settings.LOG_RETENTION_DAYS,
        encoding="utf-8",
        delay=True,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
