"""Idempotent provisioning for DataForge-owned Milvus collections."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from .models import (
    DataForgeInstance,
    KnowledgeIndexProfileRevision,
    ManagedCollection,
    StorageContractRevision,
)
from .store import CENTRAL_STAGE_TARGETS, V7Store, new_id
from .vector import ManagedCollectionIncompatible, V7Milvus

if TYPE_CHECKING:
    from dataforge.config import Settings
    from .instance import InstanceContext
    from .milvus_targets import ResolvedMilvusConnection


OWNER_PREFIX = "dataforge-managed:"


class ManagedCollectionProvisioner:
    def __init__(self, store: V7Store, milvus: V7Milvus):
        self.store, self.milvus = store, milvus

    @staticmethod
    def _description(item: ManagedCollection) -> str:
        return f"{OWNER_PREFIX}{item.id}:{item.provisioning_token}:{item.desired_spec_hash}"

    def reconcile_one(self, collection_id: str) -> dict[str, Any]:
        with self.store.sessions.begin() as session:
            item = session.get(ManagedCollection, collection_id, with_for_update=True)
            if not item:
                raise ValueError("受管 Collection 不存在")
            if item.status in {"deleting", "deleted"}:
                raise ValueError("正在删除或已删除的 Collection 不能 Provision")
            revision = session.get(StorageContractRevision, item.storage_contract_revision_id)
            if not revision or revision.status != "published":
                item.status, item.error_summary = "failed", "存储结构修订不存在或未发布"
                return self._payload(item)
            item.status, item.error_summary = "provisioning", None
            schema = revision.schema_json
            description = self._description(item)
        try:
            observed = self.milvus.ensure_managed_collection(
                item.collection_name, schema, revision.dimension, revision.metric_type,
                revision.index_json, description,
            )
            with self.store.sessions.begin() as session:
                current = session.get(ManagedCollection, collection_id, with_for_update=True)
                current.observed_spec_hash = current.desired_spec_hash if observed == description else None
                if observed != description:
                    current.status = "incompatible"
                    current.error_summary = "同名 Collection 不属于当前 DataForge 供应记录"
                else:
                    current.status, current.error_summary = "ready", None
                return self._payload(current)
        except ManagedCollectionIncompatible as exc:
            with self.store.sessions.begin() as session:
                current = session.get(ManagedCollection, collection_id, with_for_update=True)
                current.status = "incompatible"
                current.observed_spec_hash = None
                current.error_summary = str(exc)
                return self._payload(current)
        except Exception as exc:
            with self.store.sessions.begin() as session:
                current = session.get(ManagedCollection, collection_id, with_for_update=True)
                current.status = "failed"
                current.error_summary = str(exc)
                return self._payload(current)

    def reconcile(self) -> list[dict[str, Any]]:
        with self.store.sessions() as session:
            ids = list(session.scalars(select(ManagedCollection.id).where(
                ManagedCollection.status.not_in(("deleting", "deleted")),
            ).order_by(ManagedCollection.collection_name)))
        return [self.reconcile_one(item_id) for item_id in ids]

    def ensure_collection_for_profile(self, profile_revision_id: str) -> dict[str, Any]:
        """Create or verify the locally owned Collection frozen by a Profile Revision."""
        with self.store.sessions.begin() as session:
            profile = session.get(KnowledgeIndexProfileRevision, profile_revision_id)
            if not profile or profile.status != "published" or not profile.storage_contract_revision_id:
                raise ValueError("Profile Revision 没有可供应的已发布 Storage Contract")
            contract = session.get(StorageContractRevision, profile.storage_contract_revision_id)
            if not contract or contract.status != "published":
                raise ValueError("Storage Contract Revision 不存在或未发布")
            item = session.scalar(select(ManagedCollection).where(
                ManagedCollection.collection_name == profile.collection_name
            ))
            if item and (item.storage_contract_revision_id != contract.id or item.desired_spec_hash != contract.storage_spec_hash):
                raise ManagedCollectionIncompatible("同名 Collection 的 Storage Contract 不兼容")
            if not item:
                item = ManagedCollection(id=new_id("mc"), storage_contract_revision_id=contract.id,
                    collection_name=profile.collection_name, provisioning_token=new_id("provision"),
                    desired_spec_hash=contract.storage_spec_hash, status="planned")
                session.add(item); session.flush()
            item_id = item.id
        result = self.reconcile_one(item_id)
        if result["status"] != "ready":
            raise ManagedCollectionIncompatible(result.get("error") or "Collection 供应失败")
        return result

    @staticmethod
    def _payload(item: ManagedCollection) -> dict[str, Any]:
        return {
            "id": item.id, "collection_name": item.collection_name,
            "storage_contract_revision_id": item.storage_contract_revision_id,
            "desired_spec_hash": item.desired_spec_hash,
            "observed_spec_hash": item.observed_spec_hash,
            "status": item.status, "error": item.error_summary,
        }


class ManagedCollectionDeletionService:
    """Preflight and execute explicit deletion of DataForge-owned Collections."""
    def __init__(self, store: V7Store, milvus: V7Milvus | None):
        self.store, self.milvus = store, milvus

    @staticmethod
    def _description(item: ManagedCollection) -> str:
        return ManagedCollectionProvisioner._description(item)

    def preflight(self, collection_id: str) -> dict[str, Any]:
        with self.store.sessions() as session:
            item = session.get(ManagedCollection, collection_id)
            if not item:
                raise ValueError("受管 Collection 不存在")
            expected = self._description(item)
            collection_name = item.collection_name
        if not self.milvus:
            observed = {"error": "未配置 verified Authoring Target，不能验证 Collection 所有权"}
        else:
            try:
                observed = self.milvus.inspect_managed_collection(collection_name, expected)
            except Exception as exc:
                observed = {"error": str(exc)}
        return self.store.managed_collection_delete_check(collection_id, observed)

    def request_delete(self, collection_id: str) -> dict[str, Any]:
        return self.store.create_managed_collection_deletion(collection_id, self.preflight(collection_id))

    def run(self, job_id: str) -> dict[str, Any]:
        try:
            context = self.store.managed_collection_deletion_context(job_id)
            item = context["collection"]
            if not self.milvus:
                return self.store.finish_managed_collection_deletion(job_id, "未配置 verified Authoring Target，不能删除受管 Collection")
            preflight = self.preflight(item.id)
            if preflight["blockers"]:
                messages = "；".join(blocker["message"] for blocker in preflight["blockers"])
                return self.store.finish_managed_collection_deletion(job_id, f"删除执行前预检失败：{messages}")
            self.milvus.drop_managed_collection(item.collection_name, self._description(item))
            return self.store.finish_managed_collection_deletion(job_id)
        except Exception as exc:
            return self.store.finish_managed_collection_deletion(job_id, str(exc))


def _resolve_provisioning_connection(
        store: V7Store, settings: Settings, instance: InstanceContext,
        milvus_factory: Callable[[str, str | None], V7Milvus] = V7Milvus,
) -> ResolvedMilvusConnection | None:
    """Resolve Authoring Milvus, bootstrapping only the central test Target."""
    from .local_config import LocalMilvusConfigurationService
    from .milvus_targets import MilvusConnectionResolver, MilvusTargetService

    if instance.mode == "local":
        local = LocalMilvusConfigurationService(store, settings.config_encryption_key)
        current = local.get(instance.id, "current_target")
        if (not current or current.get("status") != "verified"
                or current.get("verified_fingerprint") != current.get("connection_fingerprint")):
            return None
        return local.verified(instance.id, "current_target")

    resolver = MilvusConnectionResolver(store, settings.config_encryption_key)
    with store.sessions() as session:
        persisted = session.get(DataForgeInstance, instance.id)
        if not persisted:
            raise ValueError("DataForge 实例不存在")
        authoring_target_id = persisted.authoring_milvus_target_id
    if authoring_target_id:
        return resolver.authoring(instance.id)

    test_target_id = CENTRAL_STAGE_TARGETS["test"][0]
    result = MilvusTargetService(
        store, settings.config_encryption_key, milvus_factory,
    ).check_startup_targets((test_target_id,))[0]
    if result.get("status") != "healthy":
        # A human choice may have won the race while the built-in check was in
        # flight. Prefer that verified binding instead of failing or replacing it.
        with store.sessions() as session:
            persisted = session.get(DataForgeInstance, instance.id)
            concurrent_target_id = persisted.authoring_milvus_target_id if persisted else None
        if concurrent_target_id:
            return resolver.authoring(instance.id)
        # The API startup check may have promoted the exact same first
        # candidate before this process committed its CAS result. Accept only
        # that already-healthy current Revision; a replacement candidate with
        # no current Revision remains a fail-closed stale result.
        if result.get("error_type") == "StaleMilvusVerification":
            target = store.get_milvus_target(test_target_id)
            current = target.get("current_revision") or {}
            if (target.get("current_revision_id")
                    and current.get("verification_status") == "verified"
                    and current.get("health_status") == "healthy"):
                store.bind_authoring_milvus_target_if_unset(instance.id, test_target_id)
                return resolver.authoring(instance.id)
        status = str(result.get("status") or "failed")
        raise ValueError(
            f"中心测试 Milvus 启动验证未通过（{status}），不能初始化默认知识写入目标"
        )

    store.bind_authoring_milvus_target_if_unset(instance.id, test_target_id)
    return resolver.authoring(instance.id)


def _reconcile_managed_collections(
        settings: Settings,
        milvus_factory: Callable[[str, str | None], V7Milvus] = V7Milvus,
) -> dict[str, Any]:
    from .instance import InstanceContext

    store = V7Store(settings.database_url)
    instance = InstanceContext.load(store, settings)
    connection = _resolve_provisioning_connection(
        store, settings, instance, milvus_factory,
    )
    if connection is None:
        return {
            "status": "waiting_for_configuration",
            "reason": "local_milvus_not_verified",
            "collections": [],
        }
    results = ManagedCollectionProvisioner(
        store, milvus_factory(connection.uri, connection.token),
    ).reconcile()
    incomplete = [item for item in results if item.get("status") != "ready"]
    if incomplete:
        summary = ", ".join(
            f"{item.get('collection_name') or item.get('id')}:{item.get('status') or 'unknown'}"
            for item in incomplete
        )
        raise ManagedCollectionIncompatible(f"受管 Collection Provision 未全部完成：{summary}")
    return {"status": "completed", "reason": None, "collections": results}


def main() -> None:
    from dataforge.config import Settings

    parser = argparse.ArgumentParser(description="协调 DataForge 受管 Milvus Collection")
    parser.add_argument("--reconcile", action="store_true", help="幂等创建或校验全部受管 Collection")
    parser.parse_args()
    settings = Settings.load()
    results = _reconcile_managed_collections(settings)
    print(json.dumps(results, ensure_ascii=False))
