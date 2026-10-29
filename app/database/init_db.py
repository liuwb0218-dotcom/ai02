"""
建表与初始化 — 对应培训文档的 database/init_db.py

启动时调用 init_db()：开启 WAL、打开外键约束、按 ORM 模型建表（幂等），
并在 dev 环境下写入两个演示用户（user_id=1/2），方便接口测试做越权用例。
"""
from __future__ import annotations

import logging

from sqlalchemy import select, text
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
    await _seed_auth_users()
    if settings.is_dev:
        await _seed_demo_users()


async def _seed_auth_users() -> None:
    """确保内置超级管理员存在

    已存在时只修正角色/状态，不覆盖已有密码（避免重启重置管理员密码）。
    """
    from app.core.security import hash_password

    factory = get_session_factory()
    async with factory() as db:  # type: AsyncSession
        admin = await db.scalar(
            select(User).where(User.username == settings.SUPER_ADMIN_USERNAME)
        )
        if admin is None:
            db.add(User(
                username=settings.SUPER_ADMIN_USERNAME,
                password_hash=hash_password(settings.SUPER_ADMIN_PASSWORD),
                role="super_admin",
                status="active",
            ))
            await db.commit()
            logger.info("  ✓ 已创建超级管理员: %s", settings.SUPER_ADMIN_USERNAME)
            return

        changed = False
        if admin.role != "super_admin":
            admin.role = "super_admin"
            changed = True
        if admin.status != "active":
            admin.status = "active"
            admin.trial_expires_at = None
            changed = True
        if not admin.password_hash:
            admin.password_hash = hash_password(settings.SUPER_ADMIN_PASSWORD)
            changed = True
        if changed:
            await db.commit()
            logger.info("  ✓ 已修正超级管理员配置: %s", admin.username)


async def _seed_demo_users() -> None:
    """dev 环境预置两个已审核演示用户，密码统一 demo1234

    已有同名的演示用户时不覆盖，避免本地测试改过的密码被重置。
    """
    from app.core.security import hash_password

    factory = get_session_factory()
    async with factory() as db:  # type: AsyncSession
        for name in DEMO_USERS:
            user = await db.scalar(select(User).where(User.username == name))
            if user is None:
                db.add(User(
                    username=name,
                    password_hash=hash_password("demo1234"),
                    role="user",
                    status="active",
                ))
                continue
            # 迁移前的旧演示用户没有密码，补齐后可直接登录联调
            if not user.password_hash:
                user.password_hash = hash_password("demo1234")
            if user.status != "active":
                user.status = "active"
                user.trial_expires_at = None
        await db.commit()
        logger.info("  ✓ 演示用户已就绪: %s（密码 demo1234）", ", ".join(DEMO_USERS))
