"""模型配置服务 — 模型列表、解析、增删改与连通性测试

核心规则：
  1. owner_user_id 为 NULL 的是系统内置模型（mock / DeepSeek / 本地模型），全平台可见
  2. 用户自建模型只对本人可见，且与内置模型重名时优先使用本人配置
  3. 内置模型不允许用户修改或删除（可联系管理员调整 .env）
  4. api_key 写入前 AES-256-GCM 加密，读取时仅在内存解密，响应只回 has_api_key
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_key, encrypt_key
from app.core.exceptions import (
    ConflictException,
    DatabaseException,
    ModelNotFoundException,
    NotFoundException,
    PermissionDeniedException,
)
from app.models.model_config import ModelConfig
from app.schemas.model_config import (
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelItem,
    ModelTestResponse,
)
from app.services import llm_client, user_service

logger = logging.getLogger("app.models")


# ── 内置模型定义 ────────────────────────────────────

def _builtin_definitions() -> list[dict]:
    """内置模型清单，取值全部来自 .env，便于部署时调整"""
    defs = [{
        "name": llm_client.MOCK_MODEL,
        "display_name": "Mock 模型（本地联调）",
        "provider": "mock",
        "base_url": None,
        "upstream_model": None,
        "api_key": "",
    }]
    if settings.MODEL_API_KEY or settings.MODEL_BASE_URL:
        defs.append({
            "name": settings.DEFAULT_MODEL,
            "display_name": f"DeepSeek（{settings.DEFAULT_MODEL}）",
            "provider": "deepseek",
            "base_url": settings.MODEL_BASE_URL,
            "upstream_model": settings.DEFAULT_MODEL,
            "api_key": settings.MODEL_API_KEY,
        })
    defs.append({
        "name": "local",
        "display_name": f"本地模型（{settings.LOCAL_MODEL_NAME}）",
        "provider": "local",
        "base_url": settings.LOCAL_MODEL_BASE_URL,
        "upstream_model": settings.LOCAL_MODEL_NAME,
        "api_key": settings.LOCAL_MODEL_API_KEY,
    })
    return defs


async def seed_builtin(db: AsyncSession) -> None:
    """启动时同步内置模型：不存在则插入，已存在则按 .env 更新地址与 Key

    每次都同步而不是"只插入一次"：这样用户在 .env 填好 Key 后重启即可生效。
    """
    try:
        for item in _builtin_definitions():
            row = await db.scalar(
                select(ModelConfig).where(
                    ModelConfig.owner_user_id.is_(None),
                    ModelConfig.name == item["name"],
                )
            )
            if row is None:
                db.add(ModelConfig(
                    owner_user_id=None,
                    name=item["name"],
                    display_name=item["display_name"],
                    provider=item["provider"],
                    base_url=item["base_url"],
                    upstream_model=item["upstream_model"],
                    api_key_encrypted=encrypt_key(item["api_key"]),
                ))
            else:
                row.display_name = item["display_name"]
                row.base_url = item["base_url"]
                row.upstream_model = item["upstream_model"]
                if item["api_key"]:
                    row.api_key_encrypted = encrypt_key(item["api_key"])
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("内置模型同步失败")
        raise DatabaseException("内置模型初始化失败") from exc


# ── 查询 ───────────────────────────────────────────

async def list_for_user(db: AsyncSession, user_id: int | None,
                        include_disabled: bool = False) -> list[ModelItem]:
    """列出可用模型：内置模型 + 指定用户的自建模型

    顺序上把内置模型放前面，前端下拉框的默认项更稳定。
    include_disabled=True 用于模型管理页，停用的配置也要能显示出来以便重新启用。
    """
    conditions = [] if include_disabled else [ModelConfig.is_enabled == 1]
    if user_id is None:
        rows = (await db.scalars(
            select(ModelConfig).where(*conditions, ModelConfig.owner_user_id.is_(None))
            .order_by(ModelConfig.id)
        )).all()
    else:
        rows = (await db.scalars(
            select(ModelConfig).where(
                *conditions,
                (ModelConfig.owner_user_id.is_(None))
                | (ModelConfig.owner_user_id == user_id),
            ).order_by(ModelConfig.owner_user_id.is_not(None), ModelConfig.id)
        )).all()
    return [_to_item(r) for r in rows]


async def resolve(db: AsyncSession, user_id: int, name: str) -> llm_client.ModelTarget:
    """把 model_name 解析为可调用的目标；优先本人配置，其次内置配置"""
    conditions = (ModelConfig.name == name, ModelConfig.is_enabled == 1)

    row = await db.scalar(
        select(ModelConfig).where(*conditions, ModelConfig.owner_user_id == user_id)
    )
    if row is None:
        row = await db.scalar(
            select(ModelConfig).where(*conditions, ModelConfig.owner_user_id.is_(None))
        )
    if row is None:
        available = [m.model_name for m in await list_for_user(db, user_id)]
        raise ModelNotFoundException(
            f"模型 {name} 不存在或已停用",
            detail={"available_models": available},
        )

    return llm_client.ModelTarget(
        name=row.name,
        provider=row.provider,
        base_url=row.base_url,
        upstream_model=row.upstream_model,
        api_key=decrypt_key(row.api_key_encrypted),
        # 本地模型首次推理要加载权重，超时需要放宽
        timeout=(settings.LOCAL_MODEL_TIMEOUT_SECONDS
                 if row.provider == "local"
                 else settings.MODEL_TIMEOUT_SECONDS),
    )


# ── 用户自建配置的增删改 ────────────────────────────

async def create(db: AsyncSession, req: ModelConfigCreate,
                 current_user_id: int | None = None) -> ModelConfigResponse:
    """新增用户自建模型配置"""
    await user_service.check_owner(current_user_id, req.user_id)
    await user_service.get_user(db, req.user_id)

    # 同用户下重名直接拒绝，避免 /api/chat 解析到哪一条产生歧义
    exists = await db.scalar(
        select(ModelConfig).where(
            ModelConfig.owner_user_id == req.user_id,
            ModelConfig.name == req.name,
        )
    )
    if exists is not None:
        raise ConflictException(f"模型标识 {req.name} 已存在")

    row = ModelConfig(
        owner_user_id=req.user_id,
        name=req.name,
        display_name=req.display_name,
        provider=req.provider,
        base_url=req.base_url,
        upstream_model=req.upstream_model,
        api_key_encrypted=encrypt_key(req.api_key),
        is_enabled=1 if req.is_enabled else 0,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(f"模型标识 {req.name} 已存在") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("新增模型配置失败")
        raise DatabaseException("模型配置保存失败") from exc

    await db.refresh(row)
    return _to_detail(row)


async def update(db: AsyncSession, config_id: int, req: ModelConfigUpdate,
                 current_user_id: int | None = None) -> ModelConfigResponse:
    """修改模型配置：内置模型禁止修改，用户只能改自己的"""
    row = await _get_config(db, config_id)
    if row.owner_user_id is None:
        raise PermissionDeniedException("内置模型不支持修改，请通过 .env 或新增自建模型")
    await user_service.check_owner(current_user_id, row.owner_user_id)

    # 只覆盖显式传入的字段（None 表示未传）
    if req.display_name is not None:
        row.display_name = req.display_name
    if req.base_url is not None:
        row.base_url = req.base_url
    if req.upstream_model is not None:
        row.upstream_model = req.upstream_model
    if req.api_key is not None:
        # 传空字符串表示清除 Key（切回免鉴权的本地模型时常用）
        row.api_key_encrypted = encrypt_key(req.api_key)
    if req.is_enabled is not None:
        row.is_enabled = 1 if req.is_enabled else 0

    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("修改模型配置失败 id=%s", config_id)
        raise DatabaseException("模型配置更新失败") from exc

    await db.refresh(row)
    return _to_detail(row)


async def delete(db: AsyncSession, config_id: int,
                 current_user_id: int | None = None) -> None:
    """删除用户自建配置；内置模型不可删除，如需隐藏请改用停用"""
    row = await _get_config(db, config_id)
    if row.owner_user_id is None:
        raise PermissionDeniedException("内置模型不支持删除")
    await user_service.check_owner(current_user_id, row.owner_user_id)

    try:
        await db.delete(row)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("删除模型配置失败 id=%s", config_id)
        raise DatabaseException("模型配置删除失败") from exc


# ── 连通性测试 ─────────────────────────────────────

async def test(db: AsyncSession, config_id: int,
               current_user_id: int | None = None) -> ModelTestResponse:
    """用一句简单提问验证配置是否可用，返回耗时与回答预览"""
    row = await _get_config(db, config_id)
    if row.owner_user_id is not None:
        await user_service.check_owner(current_user_id, row.owner_user_id)

    target = llm_client.ModelTarget(
        name=row.name,
        provider=row.provider,
        base_url=row.base_url,
        upstream_model=row.upstream_model,
        api_key=decrypt_key(row.api_key_encrypted),
        timeout=(settings.LOCAL_MODEL_TIMEOUT_SECONDS
                 if row.provider == "local"
                 else settings.MODEL_TIMEOUT_SECONDS),
    )
    result = await llm_client.chat("你好，请回复 OK", target)
    return ModelTestResponse(
        success=True,
        latency_ms=result.latency_ms,
        answer_preview=result.answer[:100],
    )


# ── 内部工具 ───────────────────────────────────────

async def _get_config(db: AsyncSession, config_id: int) -> ModelConfig:
    row = await db.scalar(select(ModelConfig).where(ModelConfig.id == config_id))
    if row is None:
        # 用 NotFoundException 而非 ModelNotFoundException：
        # 后者语义是"模型名不可用"，这里是"配置记录不存在"
        raise NotFoundException(f"模型配置 {config_id} 不存在")
    return row


def _to_item(row: ModelConfig) -> ModelItem:
    return ModelItem(
        id=row.id,
        model_name=row.name,
        display_name=row.display_name,
        provider=row.provider,
        base_url=row.base_url,
        upstream_model=row.upstream_model,
        has_api_key=bool(row.api_key_encrypted),
        is_builtin=row.owner_user_id is None,
        is_enabled=bool(row.is_enabled),
    )


def _to_detail(row: ModelConfig) -> ModelConfigResponse:
    return ModelConfigResponse(
        id=row.id,
        model_name=row.name,
        display_name=row.display_name,
        provider=row.provider,
        base_url=row.base_url,
        upstream_model=row.upstream_model,
        has_api_key=bool(row.api_key_encrypted),
        is_builtin=row.owner_user_id is None,
        is_enabled=bool(row.is_enabled),
    )


__all__ = [
    "seed_builtin", "list_for_user", "resolve",
    "create", "update", "delete", "test",
]
