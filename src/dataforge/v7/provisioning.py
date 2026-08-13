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
            ids = list(session.scalars(select(ManagedCollection.id).order_by(ManagedCollection.collection_name)))
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
