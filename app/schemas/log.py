"""调用日志相关请求/响应模型

对外只返回元信息（模型、状态、耗时、Token），不返回问题与回答内容。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LogItem(BaseModel):
    """一条调用日志"""

    id: int
    user_id: int
    username: str | None = Field(None, description="用户名；用户已删除时为 null")
    session_id: int | None = Field(None, description="失败调用没有会话 ID")
    model_name: str
    status: str = Field(..., description="success / error")
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error_message: str | None = Field(None, description="失败原因；成功时为 null")
    created_at: str


class LogsResponse(BaseModel):
    """分页日志列表"""

    items: list[LogItem]
    total: int
    page: int
    page_size: int


class LogSummary(BaseModel):
    """用量汇总：按最近 N 天统计"""

    days: int
    total_calls: int
    success_calls: int
    error_calls: int
    success_rate: float = Field(..., description="0-1 之间的小数；无调用时为 0")
    avg_latency_ms: int = Field(..., description="仅统计成功调用的平均耗时")
    total_tokens: int
