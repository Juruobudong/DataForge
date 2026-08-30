"""Encrypted local URI/token targets used by resumable offline imports."""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select

from .milvus_targets import (
    ResolvedMilvusConnection,
    StaleMilvusVerification,
    connection_fingerprint,
    safe_milvus_error,
    validate_milvus_uri,
)
from .models import LocalMilvusConfiguration, utc_now
from .secret_codec import ConfigCipher
from .store import V7Store, new_id
from .vector import V7Milvus


SLOTS = {"current_target", "candidate_target", "package_preset"}
ResolvedMilvusTarget = ResolvedMilvusConnection


class LocalMilvusConfigurationService:
    def __init__(self, store: V7Store, encryption_key: str | None):
        self.store = store
        self.cipher = ConfigCipher(encryption_key)

    @staticmethod
    def _aad(instance_id: str, slot: str) -> str:
        return f"dataforge:local-milvus:{instance_id}:{slot}:v2"

    @staticmethod
    def _payload(value: LocalMilvusConfiguration) -> dict[str, Any]:
        return {
            "id": value.id, "slot": value.slot, "uri": value.uri,
            "token_configured": bool(value.token_ciphertext),
            "config_revision": value.config_revision,
            "connection_fingerprint": value.connection_fingerprint,
            "status": value.status, "verified_fingerprint": value.verified_fingerprint,
            "verified_at": value.verified_at.isoformat() if value.verified_at else None,
            "verification_error": value.verification_error,
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

    def _token(self, value: LocalMilvusConfiguration, instance_id: str, slot: str) -> str | None:
        return self.cipher.decrypt(
            value.token_ciphertext, self._aad(instance_id, slot), value.token_key_version,
        ) if value.token_ciphertext else None

    def put(self, instance_id: str, slot: str, *, uri: str, token: str | None = None,
            preserve_token: bool = True) -> dict[str, Any]:
        if slot not in SLOTS:
            raise ValueError("Milvus 配置槽位无效")
        uri = validate_milvus_uri(uri)
        if slot == "package_preset" and token:
            raise ValueError("发布包预设禁止携带 Milvus Token")
        with self.store.sessions.begin() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ).with_for_update())
            existing_token = self._token(value, instance_id, slot) if value and preserve_token and token is None else None
            chosen_token = token if token is not None else existing_token
            if token is not None:
                desired_ciphertext = self.cipher.encrypt(token, self._aad(instance_id, slot)) if token else None
                desired_key_version = self.cipher.key_version if token else None
            elif value and preserve_token:
                desired_ciphertext, desired_key_version = value.token_ciphertext, value.token_key_version
            else:
                desired_ciphertext = desired_key_version = None
            fingerprint = connection_fingerprint(uri, self.cipher.secret_fingerprint(chosen_token))
            if not value:
                value = LocalMilvusConfiguration(
                    id=new_id("milvuscfg"), dataforge_instance_id=instance_id, slot=slot,
                    uri=uri, config_revision=1, connection_fingerprint=fingerprint,
                )
                session.add(value)
                changed = True
            else:
                changed = value.connection_fingerprint != fingerprint
                if changed:
                    value.config_revision += 1
                value.uri = uri
                value.connection_fingerprint = fingerprint
            value.token_ciphertext, value.token_key_version = desired_ciphertext, desired_key_version
            if changed:
                value.status, value.verified_fingerprint, value.verified_at = "pending_verification", None, None
                value.verification_error = None
            session.flush()
            return self._payload(value)

    def resolve(self, instance_id: str, slot: str) -> ResolvedMilvusConnection | None:
        if slot not in SLOTS:
            raise ValueError("Milvus 配置槽位无效")
        with self.store.sessions() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ))
            if not value:
                return None
            return ResolvedMilvusConnection(
                uri=value.uri, token=self._token(value, instance_id, slot),
                revision_id=str(value.config_revision), fingerprint=value.connection_fingerprint,
                target_id=f"local:{instance_id}:{slot}",
            )

    def verified(self, instance_id: str, slot: str) -> ResolvedMilvusConnection:
        value = self.get(instance_id, slot)
        resolved = self.resolve(instance_id, slot)
        if not value or not resolved or value["status"] != "verified" or value["verified_fingerprint"] != resolved.fingerprint:
            raise ValueError("Local Milvus 配置未通过连接验证")
        return resolved

    def verify(self, instance_id: str, slot: str,
               factory: Callable[[str, str | None], V7Milvus] = V7Milvus) -> dict[str, Any]:
        target = self.resolve(instance_id, slot)
        if not target:
            raise ValueError("Milvus 配置不存在")
        expected_revision, expected_fingerprint = int(target.revision_id), target.fingerprint
        try:
            factory(target.uri, target.token).list_collections()
            passed, message = True, None
        except Exception as exc:
            passed, message = False, safe_milvus_error(exc, target.uri, target.token)
        with self.store.sessions.begin() as session:
            value = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == slot,
            ).with_for_update())
            if (not value or value.config_revision != expected_revision
                    or value.connection_fingerprint != expected_fingerprint):
                raise StaleMilvusVerification("Milvus 配置验证结果已过期")
            value.status = "verified" if passed else "verification_failed"
            value.verified_fingerprint = expected_fingerprint if passed else None
            value.verified_at = utc_now()
            value.verification_error = message
            payload = self._payload(value)
        if not passed:
            raise ValueError(f"Milvus 验证失败：{message}")
        return payload

    def promote_candidate(self, instance_id: str) -> dict[str, Any]:
        candidate = self.verified(instance_id, "candidate_target")
        result = self.put(
            instance_id, "current_target", uri=candidate.uri, token=candidate.token, preserve_token=False,
        )
        # Promotion copies a configuration that was just verified. Rebind the
        # fingerprint rather than issuing a second network request.
        with self.store.sessions.begin() as session:
            current = session.scalar(select(LocalMilvusConfiguration).where(
                LocalMilvusConfiguration.dataforge_instance_id == instance_id,
                LocalMilvusConfiguration.slot == "current_target",
            ).with_for_update())
            current.status = "verified"
            current.verified_fingerprint = current.connection_fingerprint
            current.verified_at = utc_now()
            current.verification_error = None
            return self._payload(current)
