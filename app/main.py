"""FastAPI 应用入口 — 路由注册、全局异常处理、request_id 中间件

启动：
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import chromadb
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request

from app.api.routes import router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.database.init_db import init_db
from app.database.session import close_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化资源，关闭时按相反顺序释放"""
    logger.info("应用启动中...")

    # ── 数据库：WAL + 建表 ──────────────────────────
    await init_db()
    logger.info("  ✓ SQLite: %s", settings.DATABASE_URL)

    # ── ChromaDB（向量检索，本期不参与 P0 链路）───
    # 挂到 app.state 而非模块全局变量：便于测试替换，也避免多实例互相污染
    chroma_path = (Path(__file__).resolve().parent.parent
                   / settings.CHROMA_PERSIST_DIR).resolve()
    chroma_path.mkdir(parents=True, exist_ok=True)
    app.state.chroma_client = chromadb.PersistentClient(
        path=str(chroma_path),
        # 关闭匿名遥测；注意 Chroma 0.5.x 的 posthog 钩子仍会打印 capture() 报错，
        # 属其内部缺陷，不影响功能与数据
        settings=chromadb.Settings(anonymized_telemetry=False),
    )
    app.state.chroma_client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_NAME
    )
    logger.info("  ✓ ChromaDB: %s", chroma_path)

    # ── APScheduler（定时任务，阶段 3 注册 job）────
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("  ✓ APScheduler 已启动")

    logger.info("Application startup complete.")
    yield

    # ── 关闭顺序：先停调度器（避免任务持有连接），再释放数据库 ──
    logger.info("应用关闭中...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await close_db()
    logger.info("Application shutdown complete.")


# 创建 FastAPI 实例：/docs 与 /openapi.json 保持默认路径，方便直接访问
app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ── request_id 中间件：错误响应与日志可对齐 ─────────
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """为每个请求生成短 ID，写入 request.state 并回写到响应头

    用途：用户报错时只需提供 X-Request-Id，就能在日志中定位到具体请求。
    """
    request.state.request_id = "req_" + uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


# 异常处理器必须在挂载路由前注册，否则部分异常不会被接管
register_exception_handlers(app)
app.include_router(router)
