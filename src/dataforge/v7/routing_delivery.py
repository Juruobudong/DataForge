"""Online delivery of authorized DataForge partitions to a release-stage Milvus."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .provisioning import ManagedCollectionProvisioner
from .store import V7Store
from .vector import V7Milvus


class RoutingDeliveryService:
    """Copy only the partitions referenced by one immutable routing candidate."""

    def __init__(self, store: V7Store, backup_dir: Path | None = None):
        self.store = store
        self.backup_dir = backup_dir

    @staticmethod
    def _token(stage: str) -> str | None:
        name = (
            "DATAFORGE_PRODUCTION_MILVUS_TOKEN"
            if stage == "production"
            else "DATAFORGE_TEST_MILVUS_TOKEN"
        )
        return os.getenv(name) or os.getenv("DATAFORGE_MILVUS_TOKEN")

    @staticmethod
    def _partitions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for route in snapshot.get("routes", []):
            for library in route.get("libraries", []):
                for index in library.get("indexes", []):
                    collection = str(index.get("collection_name") or "")
                    partition = str(
                        index.get("partition_name")
                        or library.get("partition_name")
                        or ""
                    )
                    if collection and partition:
                        unique[(collection, partition)] = index
        return [unique[key] for key in sorted(unique)]

    def sync(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        stage = str(snapshot.get("release_stage") or "")
        if stage not in {"test", "production"}:
            raise ValueError("RoutingSnapshot release_stage 无效")
        if stage == "test":
            return []
        deployment_id = str((snapshot.get("deployment") or {}).get("id") or "")
        values = self.store.list_shared_deployments(allowed_deployment_id=deployment_id)
        if len(values) != 1:
            raise ValueError("RoutingSnapshot Deployment 不存在")
        stage_targets = values[0].get("stage_targets") or {}
        source_uri = str((stage_targets.get("test") or {}).get("milvus_url") or "")
        target_uri = str((stage_targets.get("production") or {}).get("milvus_url") or "")
        if not source_uri or not target_uri:
            raise ValueError("Deployment 缺少 test 或 production Milvus Target")

        source = V7Milvus(source_uri, self._token("test"))
        target = V7Milvus(target_uri, self._token("production"))
        results: list[dict[str, Any]] = []
        backup_root = None
        backup_manifest: dict[str, Any] = {
            "deployment_code": (snapshot.get("deployment") or {}).get("code"),
            "institution_name": (snapshot.get("deployment") or {}).get(
                "institution_name"
            ),
            "release_stage": stage,
            "created_at": datetime.now(UTC).isoformat(),
            "partitions": [],
        }
        if self.backup_dir:
            identity = hashlib.sha256(
                f"{backup_manifest['deployment_code']}:{backup_manifest['created_at']}".encode()
            ).hexdigest()[:12]
            backup_root = (
                self.backup_dir
                / str(backup_manifest["deployment_code"] or "unknown")
                / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{identity}"
            )
            backup_root.mkdir(parents=True, exist_ok=False)

        def persist_manifest() -> None:
            if not backup_root:
                return
            manifest_path = backup_root / "manifest.json"
            temporary_manifest = manifest_path.with_suffix(".json.tmp")
            temporary_manifest.write_text(
                json.dumps(
                    backup_manifest, ensure_ascii=False, sort_keys=True, indent=2
                ),
                encoding="utf-8",
            )
            temporary_manifest.replace(manifest_path)

        persist_manifest()
        with tempfile.TemporaryDirectory(
            prefix="dataforge-routing-delivery-"
        ) as temporary:
            work = Path(temporary)
            for sequence, index in enumerate(self._partitions(snapshot)):
                collection = str(index["collection_name"])
                partition = str(index["partition_name"])
                profile_revision_id = str(index.get("index_profile_revision_id") or "")
                embedding = index.get("embedding") or {}
                if index.get("collection_policy") == "managed":
                    ManagedCollectionProvisioner(
                        self.store, target
                    ).ensure_collection_for_profile(profile_revision_id)
                else:
                    target.validate_collection(
                        collection,
                        index.get("fields") or {},
                        int(embedding.get("dimension") or 0),
                    )

                exported_path = work / f"source-{sequence}.parquet"
                source_meta = source.export_partition(
                    collection, partition, exported_path
                )
                had_target = target.partition_exists(collection, partition)
                backup_path = (
                    (backup_root / f"{sequence:04d}.parquet")
                    if backup_root
                    else (work / f"backup-{sequence}.parquet")
                )
                backup_meta = (
                    target.export_partition(collection, partition, backup_path)
                    if had_target
                    else None
                )
                if backup_meta:
                    backup_manifest["partitions"].append(
                        {
                            "collection_name": collection,
                            "partition_name": partition,
                            "path": str(backup_path),
                            **backup_meta,
                        }
                    )
                    persist_manifest()
                try:
                    target.reset_partition(collection, partition)
                    target.import_partition(collection, partition, exported_path)
                    verified = target.verify_partition(
                        collection,
                        partition,
                        source_meta["count"],
                        source_meta["digest"],
                        source_meta["primary_field"],
                    )
                    if not verified.get("valid"):
                        raise ValueError(f"Partition {partition} 同步摘要校验失败")
                    target.load_partition(collection, partition)
                    results.append(
                        {
                            "collection_name": collection,
                            "partition_name": partition,
                            **verified,
                        }
                    )
                except Exception:
                    if backup_meta:
                        target.reset_partition(collection, partition)
                        target.import_partition(collection, partition, backup_path)
                        restored = target.verify_partition(
                            collection,
                            partition,
                            backup_meta["count"],
                            backup_meta["digest"],
                            backup_meta["primary_field"],
                        )
                        if restored.get("valid"):
                            target.load_partition(collection, partition)
                    elif target.partition_exists(collection, partition):
                        target.drop_partition(collection, partition)
                    raise
        return results
