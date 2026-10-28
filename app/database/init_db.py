"""
建表与初始化 — 对应培训文档的 database/init_db.py

启动时调用 init_db()：开启 WAL、打开外键约束、按 ORM 模型建表（幂等），
并在 dev 环境下写入两个演示用户（user_id=1/2），方便接口测试做越权用例。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import Base, get_engine, get_session_factory
from app.models.conversation import User

logger = logging.getLogger("app.database")

DEMO_USERS = ("demo_user_1", "demo_user_2")


async def init_db() -> None:
    """应用启动时调用：建表 + 基础 PRAGMA 设置 + dev 演示数据"""
    # 触发模型 import，保证 Base.metadata 里能看到三张核心表
    import app.models  # noqa: F401

    engine = get_engine()

    # 建表与 PRAGMA 放在同一事务内执行，失败时整体回滚
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            # WAL：读写不互相阻塞，缓解 SQLite 单写锁问题
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            # SQLite 默认不校验外键，必须显式开启
            await conn.execute(text("PRAGMA foreign_keys=ON"))
        # create_all 幂等：表已存在时跳过，可重复执行
        await conn.run_sync(Base.metadata.create_all)

    logger.info("  ✓ 数据表已就绪: %s", ", ".join(sorted(Base.metadata.tables)))

    # 同步内置模型（mock / DeepSeek / 本地模型），保证 /api/models 至少有可选项
    # 放在这里而不是 main.py：建表之后立刻执行，避免首次请求时列表为空
    from app.services import model_config_service  # 延迟 import，避免循环依赖
    factory = get_session_factory()
    async with factory() as db:  # type: AsyncSession
        await model_config_service.seed_builtin(db)
    logger.info("  ✓ 内置模型已同步")

    # 仅 dev 环境预置演示数据，生产不写入任何默认账号
    if settings.is_dev:
        await _seed_demo_users()


async def _seed_demo_users() -> None:
    """dev 环境预置两个演示用户，保证 user_id=1/2 可做越权测试"""
    factory = get_session_factory()
    async with factory() as db:  # type: AsyncSession
        # 已有用户则跳过，避免每次重启重复插入（且 username 唯一约束会冲突）
        count = await db.scalar(select(func.count()).select_from(User))
        if count:
            return
        for name in DEMO_USERS:
            db.add(User(username=name))
        await db.commit()
        logger.info("  ✓ 已预置演示用户: %s", ", ".join(DEMO_USERS))
