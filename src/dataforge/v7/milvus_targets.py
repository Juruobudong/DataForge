"""Verified Milvus connection registry and one runtime resolver."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from .models import DataForgeInstance, DeploymentTarget, MilvusTarget, MilvusTargetRevision, ProjectDeployment, utc_now
from .secret_codec import ConfigCipher
from .store import V7Store
from .vector import V7Milvus


LOGGER = logging.getLogger(__name__)


class StaleMilvusVerification(ValueError):
    """The candidate changed while an external connection check was running."""


def validate_milvus_uri(uri: str) -> str:
    value = str(uri or "").strip()
    if not value:
        raise ValueError("Milvus Target URI 不能为空")
    parsed = urlsplit(value)
    sensitive = {"password", "token", "secret", "api_key", "apikey", "access_token"}
    if parsed.username or parsed.password or ({key.lower() for key in parse_qs(parsed.query)} & sensitive):
        raise ValueError("Milvus URI 禁止内嵌凭据，请使用独立 Token 字段")
    return value


def connection_fingerprint(uri: str, credential_fingerprint: str) -> str:
    value = json.dumps({"uri": uri, "credential_fingerprint": credential_fingerprint}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_milvus_error(exc: Exception, uri: str, token: str | None) -> str:
    message = str(exc)
    for secret in (uri, token):
        if secret:
            message = message.replace(secret, "[redacted]")
    return (message.strip() or "Milvus 连接失败")[:1000]


@dataclass(frozen=True)
class ResolvedMilvusConnection:
    uri: str
    token: str | None
    revision_id: str
    fingerprint: str
    target_id: str | None = None

    def client(self) -> V7Milvus:
        return V7Milvus(self.uri, self.token)


class MilvusConnectionResolver:
    def __init__(self, store: V7Store, encryption_key: str | None):
        self.store = store
        self.cipher = ConfigCipher(encryption_key)

    @staticmethod
    def _aad(target_id: str, revision_id: str) -> str:
        return f"dataforge:milvus-target:{target_id}:{revision_id}:v1"

    def _resolved(self, revision: MilvusTargetRevision) -> ResolvedMilvusConnection:
        if revision.verification_status != "verified":
            raise ValueError("Milvus Target Revision 未通过连接验证")
        token = self.cipher.decrypt(
            revision.token_ciphertext,
            self._aad(revision.milvus_target_id, revision.id),
            revision.token_key_version,
        ) if revision.token_ciphertext else None
        return ResolvedMilvusConnection(
            uri=revision.milvus_url, token=token, revision_id=revision.id,
            fingerprint=revision.connection_fingerprint, target_id=revision.milvus_target_id,
        )

    def revision(self, revision_id: str) -> ResolvedMilvusConnection:
        with self.store.sessions() as session:
            value = session.get(MilvusTargetRevision, revision_id)
            if not value:
                raise ValueError("Milvus Target Revision 不存在")
            return self._resolved(value)

    def target(self, target_id: str) -> ResolvedMilvusConnection:
        with self.store.sessions() as session:
            target = session.get(MilvusTarget, target_id)
            revision = session.get(MilvusTargetRevision, target.current_revision_id) \
                if target and target.current_revision_id else None
            if not target or not revision:
                raise ValueError("Milvus Target 尚无已验证配置")
            return self._resolved(revision)

    def authoring(self, instance_id: str | None = None) -> ResolvedMilvusConnection:
        with self.store.sessions() as session:
            instance = session.get(DataForgeInstance, instance_id) if instance_id else session.scalar(select(DataForgeInstance))
            if not instance or not instance.authoring_milvus_target_id:
                raise ValueError("当前实例尚未配置默认知识写入目标")
            target = session.get(MilvusTarget, instance.authoring_milvus_target_id)
            revision = session.get(MilvusTargetRevision, target.current_revision_id) \
                if target and target.current_revision_id else None
            if not revision:
                raise ValueError("默认知识写入目标未通过连接验证")
            return self._resolved(revision)

    def stage(self, boundary_id: str, release_stage: str) -> ResolvedMilvusConnection:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        with self.store.sessions() as session:
            binding = session.get(ProjectDeployment, boundary_id)
            deployment_id = binding.deployment_id if binding else boundary_id
            revision = session.scalar(select(MilvusTargetRevision).join(
                DeploymentTarget,
                DeploymentTarget.milvus_target_revision_id == MilvusTargetRevision.id,
            ).where(
                DeploymentTarget.deployment_id == deployment_id,
                DeploymentTarget.release_stage == release_stage,
                DeploymentTarget.target_kind == "milvus",
            ))
            if not revision:
                raise ValueError(f"Deployment 尚未配置 verified {release_stage} Milvus Target")
            return self._resolved(revision)

    def snapshot(self, snapshot: dict[str, Any]) -> ResolvedMilvusConnection:
        target = snapshot.get("milvus_target") or {}
        revision_id = str(target.get("revision_id") or "")
        if not revision_id:
            raise ValueError("RoutingSnapshot 缺少 Milvus Target Revision")
        resolved = self.revision(revision_id)
        if resolved.uri != target.get("milvus_url") or resolved.fingerprint != target.get("connection_fingerprint"):
            raise ValueError("RoutingSnapshot 的 Milvus 连接身份不匹配")
        return resolved


class InstanceMilvusConnectionResolver:
    """Select central Registry or institution-local current target by instance mode."""

    def __init__(self, central: MilvusConnectionResolver, local_service, instance_getter):
        self.central = central
        self.local_service = local_service
        self.instance_getter = instance_getter

    def _instance(self):
        return self.instance_getter()

    def authoring(self, instance_id: str | None = None) -> ResolvedMilvusConnection:
        instance = self._instance()
        return (
            self.local_service.verified(instance.id, "current_target")
            if instance.mode == "local" else self.central.authoring(instance_id or instance.id)
        )

    def stage(self, boundary_id: str, release_stage: str) -> ResolvedMilvusConnection:
        instance = self._instance()
        return (
            self.local_service.verified(instance.id, "current_target")
            if instance.mode == "local" else self.central.stage(boundary_id, release_stage)
        )

    def revision(self, revision_id: str) -> ResolvedMilvusConnection:
        return self.central.revision(revision_id)

    def snapshot(self, snapshot: dict[str, Any]) -> ResolvedMilvusConnection:
        target = snapshot.get("milvus_target") or {}
        if str(target.get("revision_id") or "").startswith("local:"):
            instance = self._instance()
            resolved = self.local_service.verified(instance.id, "current_target")
            if (f"local:{resolved.revision_id}" != target.get("revision_id")
                    or resolved.uri != target.get("milvus_url")
                    or resolved.fingerprint != target.get("connection_fingerprint")):
                raise ValueError("RoutingSnapshot 的 Local Milvus 连接身份不匹配")
            return resolved
        return self.central.snapshot(snapshot)


class MilvusTargetService:
    def __init__(self, store: V7Store, encryption_key: str | None,
                 factory: Callable[[str, str | None], V7Milvus] = V7Milvus):
        self.store = store
        self.encryption_key = encryption_key
        self.cipher = ConfigCipher(encryption_key)
        self.factory = factory

    @staticmethod
    def _aad(target_id: str, revision_id: str) -> str:
        return f"dataforge:milvus-target:{target_id}:{revision_id}:v1"

    def _decrypt_revision(self, revision: dict[str, Any]) -> str | None:
        return self.cipher.decrypt(
            revision["token_ciphertext"], self._aad(revision["milvus_target_id"], revision["id"]),
            revision["token_key_version"],
        ) if revision["token_ciphertext"] else None

    def verify(self, target_id: str) -> dict[str, Any]:
        revision = self.store.candidate_milvus_target_revision(target_id)
        token = self._decrypt_revision(revision)
        started = time.monotonic()
        try:
            self.factory(revision["milvus_url"], token).check_connection()
            passed, error = True, None
        except Exception as exc:
            passed, error = False, safe_milvus_error(exc, revision["milvus_url"], token)
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            return self.store.finish_milvus_target_verification(
                target_id, expected_revision_id=revision["id"],
                expected_fingerprint=revision["connection_fingerprint"], passed=passed, error=error,
                latency_ms=latency_ms,
            )
        except ValueError as exc:
            if "已过期" in str(exc):
                raise StaleMilvusVerification(str(exc)) from exc
            raise

    def check_current(self, target_id: str) -> dict[str, Any]:
        target = self.store.get_milvus_target(target_id)
        revision_id = target.get("current_revision_id")
        if not revision_id:
            raise ValueError("Milvus Target 尚无已验证的当前 Revision")
        revision = self.store.milvus_target_revision(str(revision_id))
        if revision["verification_status"] != "verified":
            raise ValueError("Milvus Target 当前 Revision 未通过配置验证")
        token = self._decrypt_revision(revision)
        started = time.monotonic()
        try:
            self.factory(revision["milvus_url"], token).check_connection()
            healthy, error = True, None
        except Exception as exc:
            healthy, error = False, safe_milvus_error(exc, revision["milvus_url"], token)
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            return self.store.finish_milvus_target_health_check(
                target_id, expected_revision_id=revision["id"],
                expected_fingerprint=revision["connection_fingerprint"], healthy=healthy,
                latency_ms=latency_ms, error=error,
            )
        except ValueError as exc:
            if "已过期" in str(exc):
                raise StaleMilvusVerification(str(exc)) from exc
            raise

    def check_collections(self, target_id: str) -> dict[str, Any]:
        """List Collection names only after an explicit administrator action."""
        target = self.store.get_milvus_target(target_id)
        revision_id = target.get("current_revision_id")
        if not revision_id:
            raise ValueError("Milvus Target 尚无已验证的当前 Revision")
        revision = self.store.milvus_target_revision(str(revision_id))
        if revision["verification_status"] != "verified":
            raise ValueError("Milvus Target 当前 Revision 未通过配置验证")
        token = self._decrypt_revision(revision)
        try:
            names = sorted(set(self.factory(revision["milvus_url"], token).list_collections()))
        except Exception as exc:
            return {
                "target_id": target_id, "status": "unavailable", "checked_at": utc_now().isoformat(),
                "collection_count": 0, "dataforge_collection_count": 0,
                "dataforge_collections": [],
                "error": safe_milvus_error(exc, revision["milvus_url"], token),
            }
        managed = {item["collection_name"] for item in self.store.list_managed_collections()}
        dataforge = sorted(managed.intersection(names))
        return {
            "target_id": target_id, "status": "available", "checked_at": utc_now().isoformat(),
            "collection_count": len(names), "dataforge_collection_count": len(dataforge),
            "dataforge_collections": dataforge, "error": None,
        }

    def check_startup_targets(self, target_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        """Check the two built-in central targets once without coupling their outcomes."""
        def check_one(target_id: str) -> dict[str, Any]:
            try:
                target = self.store.get_milvus_target(target_id)
                if target.get("current_revision_id"):
                    result = self.check_current(target_id)
                    status = (result.get("current_revision") or {}).get("health_status") or "unknown"
                elif target.get("candidate_revision_id"):
                    result = self.verify(target_id)
                    current = result.get("current_revision") or {}
                    candidate = result.get("candidate_revision") or {}
                    status = (
                        current.get("health_status")
                        if result.get("current_revision_id")
                        else "unavailable" if candidate.get("verification_status") == "verification_failed"
                        else "unknown"
                    )
                else:
                    LOGGER.warning("Milvus startup check skipped: target_id=%s reason=no_revision", target_id)
                    return {"target_id": target_id, "status": "skipped"}
                LOGGER.info("Milvus startup check completed: target_id=%s status=%s", target_id, status)
                return {"target_id": target_id, "status": status}
            except Exception as exc:
                LOGGER.warning(
                    "Milvus startup check failed unexpectedly: target_id=%s error_type=%s",
                    target_id, type(exc).__name__,
                )
                return {"target_id": target_id, "status": "failed", "error_type": type(exc).__name__}

        if not target_ids:
            return []
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(2, len(target_ids)), thread_name_prefix="milvus-startup-target") as pool:
            futures = {pool.submit(check_one, target_id): target_id for target_id in target_ids}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return [results[target_id] for target_id in target_ids]

    def create(self, name: str, milvus_url: str, token: str | None = None) -> dict[str, Any]:
        uri = validate_milvus_uri(milvus_url)
        if token and not self.cipher.key_version:
            raise ValueError("保存 Milvus Token 前必须配置 DATAFORGE_CONFIG_ENCRYPTION_KEY")
        fingerprint = connection_fingerprint(uri, self.cipher.secret_fingerprint(token))
        target = self.store.create_milvus_target(name, uri, connection_fingerprint=fingerprint)
        revision_id = str(target["candidate_revision_id"])
        if token:
            with self.store.sessions.begin() as session:
                revision = session.get(MilvusTargetRevision, revision_id)
                revision.token_ciphertext = self.cipher.encrypt(token, self._aad(target["id"], revision_id))
                revision.token_key_version = self.cipher.key_version
        return self.verify(target["id"])

    def patch(self, target_id: str, *, name: str | None = None,
              milvus_url: str | None = None, token: str | None = None,
              preserve_token: bool = True) -> dict[str, Any]:
        current = self.store.get_milvus_target(target_id)
        current_revision = current.get("current_revision") or current.get("candidate_revision") or {}
        uri = validate_milvus_uri(milvus_url if milvus_url is not None else current_revision.get("milvus_url"))
        changed = milvus_url is not None or token is not None or not preserve_token
        if not changed:
            return self.store.patch_milvus_target(target_id, name=name)
        if token and not self.cipher.key_version:
            raise ValueError("保存 Milvus Token 前必须配置 DATAFORGE_CONFIG_ENCRYPTION_KEY")
        chosen_token = token
        if preserve_token and token is None and current.get("current_revision_id"):
            chosen_token = MilvusConnectionResolver(self.store, self.encryption_key).target(target_id).token
        target = self.store.patch_milvus_target(
            target_id, name=name, milvus_url=uri,
            connection_fingerprint=connection_fingerprint(uri, self.cipher.secret_fingerprint(chosen_token)),
            connection_changed=True,
        )
        revision_id = str(target["candidate_revision_id"])
        if chosen_token:
            with self.store.sessions.begin() as session:
                revision = session.get(MilvusTargetRevision, revision_id)
                revision.token_ciphertext = self.cipher.encrypt(chosen_token, self._aad(target_id, revision_id))
                revision.token_key_version = self.cipher.key_version
        return self.verify(target_id)
