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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin_routes import router as admin_router
from app.api.auth_routes import router as auth_router
from app.api.announcement_routes import router as announcement_router
from app.api.log_routes import admin_router as log_admin_router
from app.api.log_routes import router as log_router
from app.api.routes import router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.database.init_db import init_db
from app.database.session import close_db, get_session_factory
from app.services import log_service

# 日志初始化：控制台 + logs/app.log 双通道，须在任何业务日志产生前完成
setup_logging()
logger = logging.getLogger("app")


async def _purge_expired_logs() -> None:
    """定时任务：删除 LOG_RETENTION_DAYS 天前的调用日志（审计日志不清理）"""
    factory = get_session_factory()
    async with factory() as db:
        removed = await log_service.purge_old_logs(
            db, settings.LOG_RETENTION_DAYS
        )
    logger.info("已清理过期调用日志 %s 条（保留 %s 天）",
                removed, settings.LOG_RETENTION_DAYS)


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
    # 每天 03:00 清理过期调用日志；audit_log 永久保留不清理
    scheduler.add_job(
        _purge_expired_logs,
        trigger="cron", hour=3, minute=0,
        id="purge_expired_api_logs", replace_existing=True,
    )
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
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(announcement_router)
app.include_router(log_router)
app.include_router(log_admin_router)

# ── 前端静态资源 ───────────────────────────────────
# 原生 HTML/CSS/JS 单页，无需构建；/static 提供资源，/ 直接返回页面
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """根路径直接打开问答界面，接口文档仍在 /docs"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login", include_in_schema=False)
async def login_page() -> FileResponse:
    """登录/注册页：与问答页分离，未登录用户由前端跳转到此"""
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    """管理后台：页面内再校验角色，非管理员展示无权限提示页"""
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/me", include_in_schema=False)
async def profile_page() -> FileResponse:
    """我的数据：账号信息、近 30 天调用统计与历史会话（普通用户入口）"""
    return FileResponse(STATIC_DIR / "profile.html")


if __name__ == "__main__":
    # 允许以模块方式启动：python -m app.main（端口取 .env 中的 APP_PORT）
    import uvicorn

    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT)
