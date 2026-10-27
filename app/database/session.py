"""
数据库层 — SQLAlchemy 2.0 异步引擎 + sessionmaker + get_db 依赖

SQLite + aiosqlite 要点：
  - 创建 engine 时必须 connect_args={"check_same_thread": False}
  - 启用 WAL 模式解决多连接读并发
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# ── Declarative Base ────────────────────────────────
class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


# ── 异步引擎（延迟初始化）────────────────────────────
# 不在模块导入时创建引擎：一是避免导入即建文件，二是测试时可替换 DATABASE_URL
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """获取全局引擎，首次调用时创建并确保数据目录存在"""
    global _engine
    if _engine is None:
        # data/ 目录可能不存在，先建出来，否则 sqlite 无法创建 .db 文件
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,  # True 会打印每条 SQL，调试时可临时打开
            future=True,
            # aiosqlite 在线程池中执行，必须关闭同线程检查
            connect_args={"check_same_thread": False},
        )
    return _engine


# ── sessionmaker（工厂） ────────────────────────────
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """每次调用都新建工厂，绑定当前引擎（引擎替换后工厂随之更新）"""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        # 提交后对象属性仍可读，避免构造响应时触发隐式懒加载查询
        expire_on_commit=False,
        # 关闭自动 flush，由业务代码显式 flush 控制 SQL 执行时机
        autoflush=False,
    )


# ── FastAPI 依赖注入 ────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """每个请求一个 session，请求结束自动 close"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def close_db() -> None:
    """应用关闭时 dispose engine"""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
