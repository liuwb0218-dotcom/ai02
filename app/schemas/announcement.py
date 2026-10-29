"""公告相关请求/响应模型"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def normalize_time(value: str | None) -> str | None:
    """把前端 datetime-local 等格式统一成 SQLite 可比较的 YYYY-MM-DD HH:MM:SS"""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError("时间格式应为 YYYY-MM-DD HH:MM:SS")


class AnnouncementPublic(BaseModel):
    """用户端可见公告：正文纯文本，前端按 textContent 渲染"""

    id: int
    title: str
    content: str
    level: str = Field(..., description="info / warning / critical")
    publish_at: str
    expire_at: str | None = None


class PendingAnnouncementsResponse(BaseModel):
    items: list[AnnouncementPublic]
    total: int


class AnnouncementReadResponse(BaseModel):
    announcement_id: int
    read_count: int = Field(..., description="该用户对这条公告的累计阅读次数")
    first_read_at: str


class AdminAnnouncementItem(AnnouncementPublic):
    """管理端条目：在公共字段上补充状态、发布人与已读统计"""

    status: str = Field(..., description="draft / published / revoked")
    created_by: int
    creator_name: str | None = None
    created_at: str
    updated_at: str
    revoked_at: str | None = None
    read_users: int = Field(0, description="去重已读人数")
    read_count: int = Field(0, description="累计点击「知道了」次数")


class AnnouncementCounts(BaseModel):
    total: int
    published: int
    draft: int
    revoked: int


class AdminAnnouncementsResponse(BaseModel):
    items: list[AdminAnnouncementItem]
    total: int
    page: int
    page_size: int
    user_total: int = Field(..., description="分母：平台用户总数，用于展示已读覆盖率")
    counts: AnnouncementCounts


class AnnouncementCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=2000)
    level: Literal["info", "warning", "critical"] = "info"
    status: Literal["draft", "published"] = Field(
        "published", description="published 立即/定时发布，draft 仅保存草稿"
    )
    publish_at: str | None = Field(None, description="定时生效时间；为空表示立即")
    expire_at: str | None = Field(None, description="到期时间；为空表示长期有效")

    @field_validator("publish_at", "expire_at")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        return normalize_time(value)


class AnnouncementUpdateRequest(BaseModel):
    """部分更新：未传的字段保持不变；expire_at 传空字符串表示清除到期时间"""

    title: str | None = Field(None, min_length=1, max_length=100)
    content: str | None = Field(None, min_length=1, max_length=2000)
    level: Literal["info", "warning", "critical"] | None = None
    status: Literal["draft", "published"] | None = None
    publish_at: str | None = None
    expire_at: str | None = None

    @field_validator("publish_at", "expire_at")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        return normalize_time(value)
