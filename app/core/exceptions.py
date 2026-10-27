"""业务异常与全局异常处理 — 对应培训文档的 core/exceptions.py"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.error")


class AppException(Exception):
    """业务异常基类：至少包含 message / code / status_code"""

    code = "APP_ERROR"
    status_code = 500

    def __init__(self, message: str, code: str | None = None,
                 status_code: int | None = None, detail: object = None):
        self.message = message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.detail = detail
        super().__init__(message)


class ValidationException(AppException):
    """参数校验失败：空问题、超长、非法格式"""

    code = "VALIDATION_ERROR"
    status_code = 422


class AuthenticationException(AppException):
    """未登录或 Token 无效（P1 接入 JWT 后启用）"""

    code = "UNAUTHORIZED"
    status_code = 401


class PermissionDeniedException(AppException):
    """已登录但越权访问他人资源"""

    code = "FORBIDDEN"
    status_code = 403


class NotFoundException(AppException):
    """资源不存在：用户、会话"""

    code = "NOT_FOUND"
    status_code = 404


class ModelNotFoundException(AppException):
    """请求的模型名不在可用列表，detail 中带回可选模型"""

    code = "MODEL_NOT_FOUND"
    status_code = 404


class ModelCallException(AppException):
    """模型调用失败：Key 未配置、超时、上游 4xx/5xx、返回格式异常"""

    code = "MODEL_CALL_FAILED"
    status_code = 502


class DatabaseException(AppException):
    """数据库操作失败（对上层隐藏原始 SQL 报错，只记日志）"""

    code = "DB_ERROR"
    status_code = 500


class RateLimitException(AppException):
    """请求过于频繁，超出每分钟限额（P2 启用）"""

    code = "RATE_LIMITED"
    status_code = 429


class QuotaExceededException(AppException):
    """当日 Token 配额耗尽（P2 启用），仅当天拒绝，不改用户状态"""

    code = "QUOTA_EXCEEDED"
    status_code = 403


class SensitiveContentException(AppException):
    """命中敏感词，单次拦截该条消息（P2 启用）"""

    code = "SENSITIVE_CONTENT"
    status_code = 400


# ═══════════════════════════════════════════════════
# 全局异常处理器（在 main.py 中注册）
# ═══════════════════════════════════════════════════

def _error_body(request: Request, code: str, message: str,
                detail: object = None) -> dict:
    """构造统一错误响应体

    request_id 由 main.py 的中间件写入 request.state，用于日志与响应关联排查。
    """
    return {
        "success": False,
        "code": code,
        "message": message,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }


def register_exception_handlers(app: FastAPI) -> None:
    """注册三类全局处理器：业务异常、参数校验、兜底未知异常"""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        # 业务异常属于预期内错误，记 warning 即可，不打印堆栈
        logger.warning("业务异常 code=%s path=%s msg=%s",
                       exc.code, request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Pydantic 校验失败：把原始字段错误放进 detail，方便前端定位
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "VALIDATION_ERROR", "请求参数校验失败",
                detail=exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        # 未知异常：打印完整堆栈便于定位，但只向用户返回笼统提示
        logger.exception("未处理异常 path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "APP_ERROR", "服务内部错误"),
        )
