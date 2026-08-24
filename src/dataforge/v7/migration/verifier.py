"""Content and activation gates for offline migration."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from ..instance import InstanceContext
from ..local_config import LocalMilvusConfigurationService
from ..models import KnowledgeAssetVersion, KnowledgeLibrary
from ..store import V7Store
from ..vector import V7Milvus


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


class ActivationPreflightVerifier:
    """Re-verify prepared data immediately before any Routing activation."""

    def __init__(self, store: V7Store, local_config: LocalMilvusConfigurationService,
                 instance: InstanceContext,
                 milvus_factory: Callable[[str, str | None], V7Milvus] | None = None):
        self.store = store
        self.local_config = local_config
        self.instance = instance
        self.milvus_factory = milvus_factory or V7Milvus

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_migration_job(job_id)
        if job["direction"] != "import" or job["package_kind"] not in {
                "deployment_seed", "institution_release"}:
            raise ValueError("只有 Seed/Institution Release 导入任务可以执行激活检查")
        checks: list[dict[str, Any]] = []
        partitions: list[dict[str, Any]] = []

        def check(code: str, passed: bool, *, subject: dict[str, Any] | None = None,
                  expected: Any = True, observed: Any = True, message: str) -> None:
            checks.append({"code": code, "status": "passed" if passed else "blocked",
                           "subject": subject or {}, "expected": expected,
                           "observed": observed, "message": message})

        checkpoint = dict(job.get("checkpoint") or {})
        prepared_slot = str(checkpoint.get("selected_import_target") or "")
        prepared_fingerprint = checkpoint.get("prepared_target_fingerprint")
        prepared_uri = checkpoint.get("prepared_target_uri")
        prepared = bool(job["status"] == "completed" and prepared_slot and prepared_fingerprint)
        check("ACTIVATION.JOB.PREPARED", prepared, expected="completed Prepare with fingerprint",
              observed={"status": job["status"], "slot": prepared_slot,
                        "fingerprint": prepared_fingerprint}, message="Prepare 已完成并冻结 Milvus Target")

        configuration = self.local_config.get(self.instance.id, prepared_slot) if prepared_slot else None
        current_fingerprint = configuration.get("verified_fingerprint") if configuration else None
        target_unchanged = bool(configuration and configuration.get("status") == "verified" and
                                current_fingerprint == prepared_fingerprint and
                                configuration.get("uri") == prepared_uri)
        check("ACTIVATION.MILVUS.TARGET_UNCHANGED", target_unchanged,
              expected={"slot": prepared_slot, "fingerprint": prepared_fingerprint, "uri": prepared_uri},
              observed=configuration or "missing", message="Milvus Target 与 Prepare 时一致")

        target = None
        milvus = None
        reachable = False
        if target_unchanged:
            try:
                target = self.local_config.resolve(self.instance.id, prepared_slot)
                if target:
                    milvus = self.milvus_factory(target.uri, target.token)
                    milvus.client().list_collections()
                    reachable = True
            except Exception as exc:
                check("ACTIVATION.MILVUS.REACHABLE", False, expected="reachable", observed=str(exc),
                      message="Milvus 连接失败")
        if not any(item["code"] == "ACTIVATION.MILVUS.REACHABLE" for item in checks):
            check("ACTIVATION.MILVUS.REACHABLE", reachable, expected="reachable",
                  observed="reachable" if reachable else "unavailable", message="Milvus 可连接")

        candidates = self.store.list_imported_route_candidates(job_id)
        candidate_ready = bool(candidates) and all(item["status"] in {"ready", "activated"}
                                                   for item in candidates)
        check("ACTIVATION.CANDIDATE.READY", candidate_ready,
              expected="all ready or activated",
              observed={item["id"]: item["status"] for item in candidates},
              message="ImportedRouteCandidate 全部 Ready")
        asset_ids: set[str] = set()
        for candidate in candidates:
            asset_ids.update(self.store._asset_ids_in_json(candidate.get("snapshot") or {}))
        with self.store.sessions() as session:
            assets = {item.id: item for item in session.scalars(select(KnowledgeAssetVersion).where(
                KnowledgeAssetVersion.id.in_(asset_ids)))} if asset_ids else {}
            libraries = {item.id: item for item in session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_({item.knowledge_library_id for item in assets.values()})))} \
                if assets else {}
        assets_ready = bool(asset_ids) and set(assets) == asset_ids and all(
            item.status == "ready" for item in assets.values())
        check("ACTIVATION.ASSET.READY", assets_ready, expected="all ready",
              observed={asset_id: assets[asset_id].status if asset_id in assets else "missing"
                        for asset_id in sorted(asset_ids)}, message="AssetVersion 全部 Ready")

        id_maps = checkpoint.get("id_maps") or {}
        partition_map = id_maps.get("partitions") or {}
        asset_map = id_maps.get("asset_versions") or {}
        for item in job.get("items") or []:
            detail = item.get("detail") or {}
            source_asset_id = str(detail.get("asset_version_id") or detail.get("id") or "")
            local_asset_id = str(asset_map.get(source_asset_id) or source_asset_id)
            asset = assets.get(local_asset_id)
            collection_name = asset.collection_name if asset else item["collection_name"]
            partition_name = asset.partition_name if asset else str(
                partition_map.get(item["partition_name"]) or item["partition_name"])
            row = {
                "knowledge_library_id": asset.knowledge_library_id if asset else item["knowledge_library_id"],
                "knowledge_library_name": libraries.get(asset.knowledge_library_id).name
                    if asset and libraries.get(asset.knowledge_library_id) else item["knowledge_library_id"],
                "asset_version_id": local_asset_id or None,
                "collection_name": collection_name, "partition_name": partition_name,
                "source_count": int(item.get("source_count") or 0),
                "target_count": int(item.get("target_count") or 0),
                "source_digest": item.get("source_digest"),
                "target_digest": item.get("target_digest"),
                "status": "blocked",
            }
            error = None
            if not milvus:
                error = "Milvus Target 不可用"
            else:
                try:
                    if not milvus.partition_exists(collection_name, partition_name):
                        raise ValueError("Collection 或 Partition 不存在")
                    milvus.load_partition(collection_name, partition_name)
                    verified = milvus.verify_partition(collection_name, partition_name)
                    row["target_count"] = int(verified["count"])
                    row["target_digest"] = verified["digest"]
                    if row["source_count"] != row["target_count"]:
                        error = "Partition count 不一致"
                    elif not row["source_digest"] or row["source_digest"] != row["target_digest"]:
                        error = "Partition digest 不一致"
                    else:
                        row["status"] = "verified"
                except Exception as exc:
                    error = str(exc)
            row["error"] = error
            partitions.append(row)
            self.store.update_migration_item(
                job_id, item["knowledge_library_id"], item["collection_name"],
                target_count=row["target_count"], target_digest=row["target_digest"],
                status="verified" if not error else "verification_failed", error=error,
            )
            code = "ACTIVATION.PARTITION.VERIFIED"
            if error and "不存在" in error:
                code = "ACTIVATION.PARTITION.MISSING"
            elif error and "count" in error:
                code = "ACTIVATION.PARTITION.COUNT_MISMATCH"
            elif error and "digest" in error:
                code = "ACTIVATION.PARTITION.DIGEST_MISMATCH"
            elif error:
                code = "ACTIVATION.PARTITION.UNQUERYABLE"
            check(code, not error, subject={"collection_name": collection_name,
                  "partition_name": partition_name},
                  expected={"count": row["source_count"], "digest": row["source_digest"]},
                  observed={"count": row["target_count"], "digest": row["target_digest"],
                            "error": error}, message="Partition 可访问且 count/digest 一致")

        blocked = sum(item["status"] == "blocked" for item in checks)
        return {
            "job_id": job_id, "ready": blocked == 0, "blocked": blocked,
            "target": {"slot": prepared_slot, "uri": prepared_uri,
                       "prepared_fingerprint": prepared_fingerprint,
                       "current_fingerprint": current_fingerprint, "reachable": reachable},
            "summary": {"candidates": len(candidates),
                        "ready_candidates": sum(item["status"] in {"ready", "activated"}
                                                for item in candidates),
                        "collections": len({item["collection_name"] for item in partitions}),
                        "partitions": len(partitions),
                        "verified_partitions": sum(item["status"] == "verified" for item in partitions),
                        "source_rows": sum(item["source_count"] for item in partitions),
                        "target_rows": sum(item["target_count"] for item in partitions)},
            "candidates": candidates, "partitions": partitions, "checks": checks,
        }
