"""Encrypted local service targets used by resumable offline imports."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from .models import LocalMilvusConfiguration, utc_now
from .store import V7Store, new_id
from .vector import V7Milvus


SLOTS = {"current_target", "candidate_target", "package_preset"}


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
            raise ValueError("保存 Milvus 密码或 Token 前必须配置 DATAFORGE_CONFIG_ENCRYPTION_KEY")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str, aad: str, key_version: str | None) -> str:
        if not self._key or key_version != self.key_version:
            raise ValueError("Milvus 凭据加密密钥缺失或版本不匹配")
        payload = base64.b64decode(value)
        if len(payload) < 29:
            raise ValueError("Milvus 凭据密文无效")
        return AESGCM(self._key).decrypt(payload[:12], payload[12:], aad.encode("utf-8")).decode("utf-8")


@dataclass(frozen=True)
class ResolvedMilvusTarget:
    slot: str
    uri: str
    token: str | None
    database_name: str
    tls_enabled: bool
    fingerprint: str | None


class LocalMilvusConfigurationService:
    def __init__(self, store: V7Store, encryption_key: str | None):
        self.store = store
        self.cipher = ConfigCipher(encryption_key)

    @staticmethod
    def _aad(instance_id: str, slot: str) -> str:
        return f"dataforge:local-milvus:{instance_id}:{slot}:v1"

    @staticmethod
    def _fingerprint(uri: str, database_name: str, tls_enabled: bool, username: str | None,
                     secret_ciphertext: str | None) -> str:
        value = json.dumps({"uri": uri, "database": database_name, "tls": tls_enabled,
                            "username": username, "secret": secret_ciphertext}, sort_keys=True,
                           separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload(value: LocalMilvusConfiguration) -> dict[str, Any]:
        return {
            "id": value.id, "slot": value.slot, "uri": value.uri,
            "database_name": value.database_name, "tls_enabled": value.tls_enabled,
            "username": value.username, "secret_configured": bool(value.secret_ciphertext),
            "status": value.status, "verified_fingerprint": value.verified_fingerprint,
            "verified_at": value.verified_at.isoformat() if value.verified_at else None,
            "updated_at": value.updated_at.isoformat(),
        }

    def list(self, instance_id: str) -> list[dict[str, Any]]:
        with self.store.sessions() as session:
            values = session.scalars(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
            ).order_by(LocalMilvusConfiguration.slot)).all()
            return [self._payload(item) for item in values]

    def get(self, instance_id: str, slot: str) -> dict[str, Any] | None:
        with self.store.sessions() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ))
            return self._payload(value) if value else None

    def put(self, instance_id: str, slot: str, *, uri: str, database_name: str = "default",
            tls_enabled: bool = False, username: str | None = None,
            secret: str | None = None, preserve_secret: bool = True) -> dict[str, Any]:
        if slot not in SLOTS:
            raise ValueError("Milvus 配置槽位无效")
        uri = str(uri or "").strip()
        if not uri:
            raise ValueError("Milvus URI 不能为空")
        parsed_uri = urlsplit(uri)
        if (parsed_uri.username or parsed_uri.password or
                {key.lower() for key in parse_qs(parsed_uri.query)} & {"password", "token", "secret", "api_key"}):
            raise ValueError("Milvus URI 禁止内嵌凭据，请使用独立凭据字段")
        if slot == "package_preset" and secret:
            raise ValueError("发布包预设禁止携带 Milvus 凭据")
        with self.store.sessions.begin() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ).with_for_update())
            if not value:
                value = LocalMilvusConfiguration(
                    id=new_id("milvuscfg"), dataforge_instance_id=instance_id, slot=slot,
                    uri=uri, database_name=database_name or "default", tls_enabled=bool(tls_enabled),
                    username=username or None,
                )
                session.add(value)
            changed = (value.uri != uri or value.database_name != (database_name or "default") or
                       value.tls_enabled != bool(tls_enabled) or value.username != (username or None))
            value.uri, value.database_name = uri, database_name or "default"
            value.tls_enabled, value.username = bool(tls_enabled), username or None
            if secret is not None:
                value.secret_ciphertext = self.cipher.encrypt(secret, self._aad(instance_id, slot)) if secret else None
                value.secret_key_version = self.cipher.key_version if secret else None
                changed = True
            elif not preserve_secret:
                value.secret_ciphertext = value.secret_key_version = None
                changed = True
            if changed:
                value.status, value.verified_fingerprint, value.verified_at = "pending_verification", None, None
            session.flush()
            return self._payload(value)

    def resolve(self, instance_id: str, slot: str) -> ResolvedMilvusTarget | None:
        if slot not in SLOTS:
            raise ValueError("Milvus 配置槽位无效")
        with self.store.sessions() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ))
            if not value:
                return None
            secret = self.cipher.decrypt(
                value.secret_ciphertext, self._aad(instance_id, slot), value.secret_key_version,
            ) if value.secret_ciphertext else None
            token = f"{value.username}:{secret}" if value.username and secret else secret
            return ResolvedMilvusTarget(
                slot=value.slot, uri=value.uri, token=token, database_name=value.database_name,
                tls_enabled=value.tls_enabled, fingerprint=value.verified_fingerprint,
            )

    def verify(self, instance_id: str, slot: str,
               factory: Callable[[str, str | None], V7Milvus] = V7Milvus) -> dict[str, Any]:
        target = self.resolve(instance_id, slot)
        if not target:
            raise ValueError("Milvus 配置不存在")
        try:
            client = factory(target.uri, target.token).client()
            client.list_collections()
        except Exception as exc:
            with self.store.sessions.begin() as session:
                value = session.scalar(select(LocalMilvusConfiguration).where(
                    LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                    LocalMilvusConfiguration.slot == slot,
                ).with_for_update())
                if value:
                    value.status, value.verified_fingerprint, value.verified_at = "verification_failed", None, None
            raise ValueError(f"Milvus 验证失败：{exc}") from exc
        with self.store.sessions.begin() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ).with_for_update())
            if not value:
                raise ValueError("Milvus 配置不存在")
            fingerprint = self._fingerprint(
                value.uri, value.database_name, value.tls_enabled, value.username, value.secret_ciphertext,
            )
            value.status, value.verified_fingerprint, value.verified_at = "verified", fingerprint, utc_now()
            return self._payload(value)

    def promote_candidate(self, instance_id: str) -> dict[str, Any]:
        with self.store.sessions.begin() as session:
            candidate = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == "candidate_target",
            ).with_for_update())
            if not candidate or candidate.status != "verified":
                raise ValueError("只有已验证的 candidate target 可以切换为 current")
            candidate_secret = self.cipher.decrypt(
                candidate.secret_ciphertext, self._aad(instance_id, "candidate_target"),
                candidate.secret_key_version,
            ) if candidate.secret_ciphertext else None
            current = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == "current_target",
            ).with_for_update())
            if not current:
                current = LocalMilvusConfiguration(
                    id=new_id("milvuscfg"), dataforge_instance_id=instance_id,
                    slot="current_target", uri=candidate.uri,
                )
                session.add(current)
            for name in ("uri", "database_name", "tls_enabled", "username",
                         "status", "verified_fingerprint", "verified_at"):
                setattr(current, name, getattr(candidate, name))
            current.secret_ciphertext = self.cipher.encrypt(
                candidate_secret, self._aad(instance_id, "current_target"),
            ) if candidate_secret else None
            current.secret_key_version = self.cipher.key_version if candidate_secret else None
            current.verified_fingerprint = self._fingerprint(
                current.uri, current.database_name, current.tls_enabled,
                current.username, current.secret_ciphertext,
            )
            session.flush()
            return self._payload(current)
