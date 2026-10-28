"""API Key 加密存储 — AES-256-GCM

为什么加密：用户自建模型配置里的 api_key 属于敏感凭据，
即使数据库文件泄露也不能直接读出明文。

实现要点：
  - 主密钥来自 .env 的 MASTER_ENCRYPTION_KEY，经 SHA-256 派生为 32 字节（AES-256）
  - 每条密文使用独立随机 nonce（12 字节），杜绝相同明文产生相同密文
  - 存储格式 base64(nonce + ciphertext + tag)，单列 TEXT 即可承载
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

logger = logging.getLogger("app.crypto")

_NONCE_LEN = 12


def _derive_key() -> bytes:
    """把配置里的字符串主密钥派生为固定 32 字节密钥"""
    return hashlib.sha256(settings.MASTER_ENCRYPTION_KEY.encode("utf-8")).digest()


def encrypt_key(plain: str) -> str:
    """加密 API Key；空字符串原样返回空，表示"该模型无需 Key"（如本地模型）"""
    if not plain:
        return ""
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(_derive_key()).encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_key(encrypted: str) -> str:
    """解密 API Key；主密钥变更或密文损坏时返回空并记日志，不阻断请求"""
    if not encrypted:
        return ""
    try:
        raw = base64.b64decode(encrypted)
        return AESGCM(_derive_key()).decrypt(
            raw[:_NONCE_LEN], raw[_NONCE_LEN:], None
        ).decode("utf-8")
    except (InvalidTag, ValueError):
        # 典型场景：换了 MASTER_ENCRYPTION_KEY，旧密文无法解开
        logger.error("API Key 解密失败，请检查 MASTER_ENCRYPTION_KEY 是否被修改")
        return ""


def mask_key(plain: str) -> str:
    """生成用于展示的掩码，只暴露首尾各 4 位；绝不把明文返回给前端"""
    if not plain:
        return ""
    if len(plain) <= 8:
        return "****"
    return f"{plain[:4]}****{plain[-4:]}"
