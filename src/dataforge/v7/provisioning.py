"""Idempotent provisioning for DataForge-owned Milvus collections."""
from __future__ import annotations

import json
import os
import argparse
from typing import Any

from sqlalchemy import select

from .models import ManagedCollection, StorageContractRevision
from .store import V7Store
from .vector import ManagedCollectionIncompatible, V7Milvus


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

    @classmethod
    def from_environment(cls, store: V7Store) -> "ManagedCollectionDeletionService":
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        return cls(store, V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")) if uri else None)

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
            observed = {"error": "未配置 DATAFORGE_MILVUS_URI，不能验证 Collection 所有权"}
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
                return self.store.finish_managed_collection_deletion(job_id, "未配置 DATAFORGE_MILVUS_URI，不能删除受管 Collection")
            preflight = self.preflight(item.id)
            if preflight["blockers"]:
                messages = "；".join(blocker["message"] for blocker in preflight["blockers"])
                return self.store.finish_managed_collection_deletion(job_id, f"删除执行前预检失败：{messages}")
            self.milvus.drop_managed_collection(item.collection_name, self._description(item))
            return self.store.finish_managed_collection_deletion(job_id)
        except Exception as exc:
            return self.store.finish_managed_collection_deletion(job_id, str(exc))


def main() -> None:
    from dataforge.config import Settings

    parser = argparse.ArgumentParser(description="协调 DataForge 受管 Milvus Collection")
    parser.add_argument("--reconcile", action="store_true", help="幂等创建或校验全部受管 Collection")
    parser.parse_args()
    settings = Settings.load()
    uri = os.getenv("DATAFORGE_MILVUS_URI")
    if not uri:
        raise SystemExit("DATAFORGE_MILVUS_URI 未配置")
    store = V7Store(settings.database_url)
    results = ManagedCollectionProvisioner(
        store, V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")),
    ).reconcile()
    print(json.dumps(results, ensure_ascii=False))
