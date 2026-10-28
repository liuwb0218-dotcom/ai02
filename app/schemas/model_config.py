"""模型配置相关的请求 / 响应模型

安全约定：任何响应都只返回 has_api_key 布尔值，
绝不返回 api_key 明文或密文。
"""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# 模型标识：只允许字母数字与 . _ -，2~40 位，避免前端传空格或中文造成匹配问题
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,40}$")

# 用户可创建的 provider；mock 是系统内置，不允许用户自建
CreateProvider = Literal["deepseek", "local", "custom"]


def _validate_base_url(url: str) -> str:
    """校验服务地址：只允许 http/https，且禁止携带账号密码

    说明：本地模型必须允许 127.0.0.1 这类内网地址，因此不做"禁内网"限制，
    仅拦截非 HTTP 协议、URL 内嵌凭据、以及云主机元数据地址（SSRF 高危目标）。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url 只支持 http/https")
    if not parsed.hostname:
        raise ValueError("base_url 缺少主机名")
    if parsed.username or parsed.password:
        raise ValueError("base_url 不允许内嵌账号密码，请使用 api_key 字段")
    if parsed.hostname == "169.254.169.254":
        raise ValueError("禁止指向云主机元数据地址")
    return url.rstrip("/")


class ModelItem(BaseModel):
    """模型列表项：兼容旧字段 model_name，新增配置相关字段"""

    id: int | None = Field(None, description="模型配置 ID，内置模型也有 ID")
    model_name: str = Field(..., description="模型标识，/api/chat 的 model_name 取该值")
    display_name: str = Field(..., description="前端展示名称")
    provider: str = Field(..., description="mock / deepseek / local / custom")
    base_url: str | None = Field(None, description="服务地址，mock 为空")
    upstream_model: str | None = Field(None, description="上游真实模型名")
    has_api_key: bool = Field(False, description="是否已配置 Key（不返回 Key 本身）")
    is_builtin: bool = Field(False, description="true=系统内置，不可删除")
    is_enabled: bool = Field(True, description="是否启用")


class ModelsResponse(BaseModel):
    """/api/models 响应"""

    models: list[ModelItem]


class ModelConfigCreate(BaseModel):
    """新增用户自建模型配置"""

    user_id: int = Field(..., gt=0, description="归属用户")
    name: str = Field(..., description="模型标识，需在本人配置中唯一")
    display_name: str = Field(..., min_length=1, max_length=50)
    provider: CreateProvider = Field("custom", description="deepseek / local / custom")
    base_url: str = Field(..., description="OpenAI 兼容地址，如 http://127.0.0.1:11434/v1")
    upstream_model: str = Field(..., min_length=1, max_length=100,
                                description="上游模型名，如 qwen2.5:7b")
    api_key: str = Field("", description="API Key，本地模型可留空")
    is_enabled: bool = Field(True)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if v.lower() == "mock":
            raise ValueError("mock 为系统内置标识，请换一个名称")
        if not NAME_PATTERN.match(v):
            raise ValueError("name 只允许字母、数字、点、下划线、短横线，长度 2~40")
        return v

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return _validate_base_url(v)

    @field_validator("api_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        # Key 首尾空白通常是从网页复制时带入的，直接清掉
        return v.strip()


class ModelConfigUpdate(BaseModel):
    """修改模型配置：只传需要改的字段"""

    display_name: str | None = Field(None, min_length=1, max_length=50)
    base_url: str | None = None
    upstream_model: str | None = Field(None, min_length=1, max_length=100)
    api_key: str | None = None
    is_enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v) if v else v

    @field_validator("api_key")
    @classmethod
    def _check_key(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else v


class ModelConfigResponse(BaseModel):
    """单个模型配置详情（含权限标记）"""

    id: int
    model_name: str
    display_name: str
    provider: str
    base_url: str | None
    upstream_model: str | None
    has_api_key: bool
    is_builtin: bool
    is_enabled: bool


class DeleteModelResponse(BaseModel):
    """删除用户自建模型配置的响应"""

    deleted: bool
    id: int


class ModelTestResponse(BaseModel):
    """连通性测试结果：返回耗时与回答预览，便于判断配置是否可用"""

    success: bool
    latency_ms: int
    answer_preview: str
