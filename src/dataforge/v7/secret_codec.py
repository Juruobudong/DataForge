"""Shared AES-GCM codec for persisted service credentials."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ConfigCipher:
    def __init__(self, encoded_key: str | None):
        self._key = self._decode(encoded_key) if encoded_key else None
        self.key_version = hashlib.sha256(self._key).hexdigest()[:12] if self._key else None

    @staticmethod
    def _decode(value: str) -> bytes:
        raw_value = value.strip()
        try:
            raw = bytes.fromhex(raw_value) if len(raw_value) == 64 else base64.b64decode(raw_value, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("DATAFORGE_CONFIG_ENCRYPTION_KEY 必须是 32 字节 Base64 或 64 位十六进制") from exc
        if len(raw) != 32:
            raise ValueError("DATAFORGE_CONFIG_ENCRYPTION_KEY 必须是 AES-256 的 32 字节密钥")
        return raw

    def encrypt(self, value: str, aad: str) -> str:
        if not self._key:
            raise ValueError("保存服务凭据前必须配置 DATAFORGE_CONFIG_ENCRYPTION_KEY")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str, aad: str, key_version: str | None) -> str:
        if not self._key or key_version != self.key_version:
            raise ValueError("服务凭据加密密钥缺失或版本不匹配")
        payload = base64.b64decode(value)
        if len(payload) < 29:
            raise ValueError("服务凭据密文无效")
        return AESGCM(self._key).decrypt(payload[:12], payload[12:], aad.encode("utf-8")).decode("utf-8")
