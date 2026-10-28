"""Pydantic 请求/响应模型与参数校验

职责：做接口层第一道校验（长度、正数、必填）。
校验失败由 FastAPI 抛 RequestValidationError，统一转换为 422 VALIDATION_ERROR。
模型列表相关的模型定义在 schemas/model_config.py。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """问答请求体"""

    # gt=0：用户 ID 必须为正整数，0 或负数属于非法参数
    user_id: int = Field(..., gt=0, description="用户 ID")
    # 长度上限 2000：防止超长文本撑爆上下文与日志表
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    # 默认 mock：本地联调无需配置 Key 即可跑通链路
    model_name: str = Field("mock", description="模型名，必须在 /api/models 列表中")


class ChatResponse(BaseModel):
    """问答响应体：会话 ID 用于后续删除与追溯"""

    session_id: int = Field(..., description="本次问答落库后的会话 ID")
    answer: str = Field(..., description="模型回答")
    model_name: str = Field(..., description="实际使用的模型")
    latency_ms: int = Field(..., description="模型调用耗时（毫秒）")
    status: str = Field(..., description="固定 success，失败走异常响应")


class HistoryItem(BaseModel):
    """一条历史问答记录"""

    session_id: int
    question: str
    answer: str
    model_name: str
    created_at: str = Field(..., description="ISO 格式时间字符串")


class HistoryResponse(BaseModel):
    """历史查询响应：total 为未删除记录总数，items 为当前分页数据"""

    user_id: int
    total: int
    items: list[HistoryItem]


class DeleteResponse(BaseModel):
    """软删除响应：重复删除同一会话也返回 deleted=True（幂等）"""

    deleted: bool
    session_id: int
