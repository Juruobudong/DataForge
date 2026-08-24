"""Live Milvus inventory joined with immutable DataForge asset metadata."""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .store import V7Store
from .vector import V7Milvus


INVENTORY_STATUSES = ("USING", "PENDING", "HISTORY", "GC_ELIGIBLE", "INCONSISTENT", "UNMANAGED")
VERSIONED_PARTITION = re.compile(r"^kl_[A-Za-z0-9][A-Za-z0-9_-]*__v[1-9][0-9]*$")
STATUS_PRIORITY = {
    "INCONSISTENT": 0, "USING": 1, "PENDING": 2,
    "GC_ELIGIBLE": 3, "HISTORY": 4, "UNMANAGED": 5,
}


def _safe_target(uri: str | None) -> str | None:
    value = str(uri or "").strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if not parsed.scheme:
            target = value.split("?", 1)[0].split("#", 1)[0]
            return target.rsplit("@", 1)[-1]
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]


def _field_params(field: dict[str, Any]) -> dict[str, Any]:
    return dict(field.get("params") or field.get("type_params") or {})


class MilvusInventoryService:
    def __init__(self, store: V7Store, milvus: V7Milvus | None = None, *, target: str | None = None):
        self.store, self.milvus = store, milvus
        self.target = _safe_target(target or (milvus.uri if milvus else None))

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.milvus:
            for secret in (getattr(self.milvus, "token", None), getattr(self.milvus, "uri", None)):
                if secret and secret != self.target:
                    message = message.replace(str(secret), str(self.target or "[redacted]"))
        return message

    @classmethod
    def from_environment(cls, store: V7Store) -> "MilvusInventoryService":
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        return cls(
            store,
            V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")) if uri else None,
            target=uri,
        )

    @staticmethod
    def _observed_contract(actual: dict[str, Any]) -> tuple[int | None, str | None, set[str]]:
        schema = actual.get("schema") or {}
        fields = [dict(item) for item in schema.get("fields", []) if isinstance(item, dict)]
        names = {str(item.get("name")) for item in fields if item.get("name")}
        vector = next((item for item in fields if str(item.get("name")) == "vector"), None)
        dimension = None
        if vector:
            raw = _field_params(vector).get("dim")
            dimension = int(raw) if raw is not None else None
        metric = None
        for index in actual.get("indexes") or []:
            if index.get("field_name") == "vector" or index.get("index_name") == "vector":
                metric = str(index.get("metric_type") or (index.get("params") or {}).get("metric_type") or "").upper() or None
                break
        return dimension, metric, names

    def _collection_contract(self, actual: dict[str, Any], managed: dict[str, Any] | None) -> tuple[bool, list[str]]:
        if not managed:
            return False, []
        reasons: list[str] = []
        if not actual.get("exists"):
            return False, ["COLLECTION_MISSING"]
        if actual.get("description") != managed.get("expected_description"):
            reasons.append("OWNERSHIP_MARKER_MISMATCH")
        contract = managed.get("storage_contract") or {}
        dimension, metric, names = self._observed_contract(actual)
        expected_names = {
            str(item.get("name")) for item in (contract.get("schema") or {}).get("fields", []) if item.get("name")
        }
        if expected_names and names != expected_names:
            reasons.append("SCHEMA_FIELDS_MISMATCH")
        if contract.get("dimension") is not None and int(contract["dimension"]) != dimension:
            reasons.append("DIMENSION_MISMATCH")
        if contract.get("metric_type") and str(contract["metric_type"]).upper() != metric:
            reasons.append("METRIC_MISMATCH")
        return not reasons, reasons

    @staticmethod
    def _asset_status(asset: dict[str, Any], *, exists: bool, actual_count: int | None,
                      collection_consistent: bool) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if asset.get("asset_error"):
            reasons.append("ASSET_ERROR")
        verification = asset.get("verification") or {}
        if verification.get("status") in {"inconsistent", "error"}:
            reasons.append("LAST_DEEP_VERIFICATION_FAILED")
        if asset.get("asset_status") == "ready":
            if not exists:
                reasons.append("PARTITION_MISSING")
            elif actual_count is not None and int(asset.get("expected_count") or 0) != actual_count:
                reasons.append("COUNT_MISMATCH")
        if not collection_consistent and asset.get("collection_policy") == "managed":
            reasons.append("COLLECTION_CONTRACT_MISMATCH")
        if reasons:
            return "INCONSISTENT", reasons
        if asset.get("routing_ref_count", 0) > 0:
            return "USING", []
        if asset.get("latest_current_ready") or asset.get("asset_status") in {"building", "verifying"}:
            return "PENDING", ["ASSET_BUILDING"] if asset.get("asset_status") != "ready" else []
        if asset.get("gc_eligible"):
            return "GC_ELIGIBLE", []
        return "HISTORY", []

    def _build(self) -> list[dict[str, Any]]:
        if not self.milvus:
            raise RuntimeError("DATAFORGE_MILVUS_URI 未配置")
        metadata = self.store.vector_inventory_metadata()
        managed_by_name = metadata["managed_collections"]
        assets = metadata["assets"]
        assets_by_pair = {(item["collection_name"], item["partition_name"]): item for item in assets}
        expected_names = set(managed_by_name)
        expected_names.update(item["collection_name"] for item in assets)
        actual_names = set(self.milvus.list_collections())
        rows: list[dict[str, Any]] = []
        for collection_name in sorted(actual_names | expected_names):
            managed = managed_by_name.get(collection_name)
            actual = self.milvus.inspect_collection(collection_name) if collection_name in actual_names else {
                "collection_name": collection_name, "exists": False, "partitions": [], "entity_count": 0,
                "schema": {}, "indexes": [], "description": "",
            }
            collection_consistent, collection_reasons = self._collection_contract(actual, managed)
            actual_partitions = set(actual.get("partitions") or [])
            expected_partitions = {
                item["partition_name"] for item in assets if item["collection_name"] == collection_name
            }
            default_count = 0
            if "_default" in actual_partitions:
                default_count = int(self.milvus.inspect_partition(collection_name, "_default").get("entity_count") or 0)
                if default_count:
                    collection_reasons.append("DEFAULT_PARTITION_NOT_EMPTY")
            partition_rows = []
            for partition_name in sorted((actual_partitions - {"_default"}) | expected_partitions):
                asset = assets_by_pair.get((collection_name, partition_name))
                shallow = self.milvus.inspect_partition(collection_name, partition_name) \
                    if partition_name in actual_partitions else {
                        "collection_name": collection_name, "partition_name": partition_name,
                        "exists": False, "entity_count": 0,
                    }
                exists = bool(shallow.get("exists"))
                actual_count = int(shallow.get("entity_count") or 0) if exists else None
                if not asset:
                    status, reasons = "UNMANAGED", ["ASSET_MAPPING_MISSING"]
                else:
                    status, reasons = self._asset_status(
                        asset, exists=exists, actual_count=actual_count,
                        collection_consistent=collection_consistent,
                    )
                can_manage = bool(
                    managed and collection_consistent and asset and exists
                    and VERSIONED_PARTITION.fullmatch(partition_name)
                )
                partition_rows.append({
                    "collection_name": collection_name,
                    "partition_name": partition_name,
                    "exists": exists,
                    "managed": bool(managed),
                    "ownership_valid": bool(managed and collection_consistent),
                    "status": status,
                    "status_reasons": reasons,
                    "actual_count": actual_count,
                    "expected_count": asset.get("expected_count") if asset else None,
                    "expected_digest": asset.get("expected_digest") if asset else None,
                    "verification": asset.get("verification") if asset else None,
                    "asset_version_id": asset.get("asset_version_id") if asset else None,
                    "asset_version_no": asset.get("asset_version_no") if asset else None,
                    "asset_status": asset.get("asset_status") if asset else None,
                    "knowledge_library_id": asset.get("knowledge_library_id") if asset else None,
                    "knowledge_library_name": asset.get("knowledge_library_name") if asset else None,
                    "knowledge_type": asset.get("knowledge_type") if asset else None,
                    "index_profile_code": asset.get("index_profile_code") if asset else None,
                    "routing_ref_count": asset.get("routing_ref_count", 0) if asset else 0,
                    "routing_refs": asset.get("routing_refs", []) if asset else [],
                    "release_refs": asset.get("release_refs", []) if asset else [],
                    "candidate_refs": asset.get("candidate_refs", []) if asset else [],
                    "migration_refs": asset.get("migration_refs", []) if asset else [],
                    "gc_eligible": bool(asset and asset.get("gc_eligible")),
                    "actions": {
                        "verify": bool(asset and exists),
                        "load": can_manage,
                        "release": can_manage,
                    },
                })
            unknown_in_managed = bool(managed and any(item["status"] == "UNMANAGED" for item in partition_rows))
            if unknown_in_managed:
                collection_reasons.append("UNMANAGED_PARTITION_PRESENT")
            if not managed:
                collection_status = "UNMANAGED"
            elif collection_reasons or any(item["status"] == "INCONSISTENT" for item in partition_rows):
                collection_status = "INCONSISTENT"
            elif not partition_rows:
                collection_status = "PENDING"
            else:
                collection_status = min((item["status"] for item in partition_rows), key=lambda value: STATUS_PRIORITY[value])
            knowledge_types = sorted({item["knowledge_type"] for item in partition_rows if item.get("knowledge_type")})
            dimension, metric, _ = self._observed_contract(actual)
            contract = (managed or {}).get("storage_contract") or {}
            rows.append({
                "collection_name": collection_name,
                "exists": bool(actual.get("exists")),
                "managed": bool(managed),
                "ownership_valid": bool(managed and collection_consistent),
                "management_status": "DataForge" if managed else "Unmanaged",
                "status": collection_status,
                "status_reasons": list(dict.fromkeys(collection_reasons)),
                "knowledge_types": knowledge_types,
                "schema": actual.get("schema") or {},
                "index": actual.get("indexes") or [],
                "dimension": dimension if dimension is not None else contract.get("dimension"),
                "metric_type": metric or contract.get("metric_type"),
                "entity_count": int(actual.get("entity_count") or 0) if actual.get("exists") else None,
                "partition_count": len([item for item in partition_rows if item["exists"]]),
                "default_partition_count": default_count,
                "partitions": partition_rows,
                "routing_ref_count": sum(int(item["routing_ref_count"]) for item in partition_rows),
            })
        return sorted(rows, key=lambda item: (not item["managed"], item["collection_name"].casefold()))

    def overview(self) -> dict[str, Any]:
        if not self.milvus:
            return {
                "configured": False, "target": None, "healthy": False,
                "collection_count": 0, "managed_collection_count": 0,
                "partition_count": 0, "entity_count": 0,
                "inconsistent_count": 0, "unmanaged_count": 0,
                "error_code": "MILVUS_NOT_CONFIGURED",
                "error_message": "当前实例没有配置 DATAFORGE_MILVUS_URI",
            }
        try:
            rows = self._build()
        except Exception as exc:
            return {
                "configured": True, "target": self.target, "healthy": False,
                "collection_count": 0, "managed_collection_count": 0,
                "partition_count": 0, "entity_count": 0,
                "inconsistent_count": 0, "unmanaged_count": 0,
                "error_code": "MILVUS_UNAVAILABLE", "error_message": self._safe_error(exc),
            }
        actual = [item for item in rows if item["exists"]]
        return {
            "configured": True, "target": self.target, "healthy": True,
            "collection_count": len(actual),
            "managed_collection_count": sum(bool(item["managed"] and item["ownership_valid"]) for item in actual),
            "partition_count": sum(item["partition_count"] for item in actual),
            "entity_count": sum(int(item["entity_count"] or 0) for item in actual),
            "inconsistent_count": sum(item["status"] == "INCONSISTENT" for item in rows),
            "unmanaged_count": sum(item["status"] == "UNMANAGED" for item in actual),
        }

    def collections(self, *, q: str = "", knowledge_type: str = "", status: str = "",
                    only_anomaly: bool = False, only_unused: bool = False) -> list[dict[str, Any]]:
        try:
            rows = self._build()
        except Exception as exc:
            raise RuntimeError(self._safe_error(exc)) from exc
        needle = q.strip().casefold()
        result = []
        for item in rows:
            partitions = item["partitions"]
            if needle:
                haystack = [item["collection_name"]]
                haystack.extend(part["partition_name"] for part in partitions)
                haystack.extend(part.get("knowledge_library_name") or "" for part in partitions)
                if not any(needle in str(value).casefold() for value in haystack):
                    continue
            if knowledge_type and knowledge_type not in item["knowledge_types"]:
                continue
            if status and status not in ({item["status"]} | {part["status"] for part in partitions}):
                continue
            if only_anomaly and not (item["status"] == "INCONSISTENT" or any(
                    part["status"] == "INCONSISTENT" for part in partitions)):
                continue
            if only_unused and any(part["routing_ref_count"] > 0 for part in partitions):
                continue
            result.append(item)
        return result

    def collection_detail(self, collection_name: str) -> dict[str, Any]:
        try:
            rows = self._build()
        except Exception as exc:
            raise RuntimeError(self._safe_error(exc)) from exc
        item = next((row for row in rows if row["collection_name"] == collection_name), None)
        if not item:
            raise ValueError("Milvus Collection 不存在")
        return item

    def partition_detail(self, collection_name: str, partition_name: str) -> dict[str, Any]:
        collection = self.collection_detail(collection_name)
        item = next((row for row in collection["partitions"] if row["partition_name"] == partition_name), None)
        if not item:
            raise ValueError("Milvus Partition 不存在")
        return item

    def verify_partition(self, collection_name: str, partition_name: str) -> dict[str, Any]:
        if not self.milvus:
            raise RuntimeError("DATAFORGE_MILVUS_URI 未配置")
        asset = self.store.vector_partition_metadata(collection_name, partition_name)
        if not asset:
            raise ValueError("未托管 Partition 没有可校验的 AssetVersion")
        try:
            verified = self.milvus.verify_partition(collection_name, partition_name)
            observed_count = int(verified["count"])
            observed_digest = str(verified["digest"])
            consistent = observed_count == int(asset["expected_count"] or 0) and (
                not asset.get("expected_digest") or observed_digest == asset["expected_digest"]
            )
            recorded = self.store.record_vector_partition_verification(
                asset["asset_version_id"], status="consistent" if consistent else "inconsistent",
                observed_count=observed_count, observed_digest=observed_digest,
            )
            return {
                **recorded,
                "collection_name": collection_name, "partition_name": partition_name,
                "expected_count": asset["expected_count"], "expected_digest": asset["expected_digest"],
                "consistent": consistent,
                "inventory_status": "INCONSISTENT" if not consistent else (
                    "USING" if asset.get("routing_ref_count") else
                    "PENDING" if asset.get("latest_current_ready") else
                    "GC_ELIGIBLE" if asset.get("gc_eligible") else "HISTORY"
                ),
            }
        except Exception as exc:
            safe_error = self._safe_error(exc)
            self.store.record_vector_partition_verification(
                asset["asset_version_id"], status="error", error=safe_error,
            )
            raise RuntimeError(safe_error) from exc

    def _assert_manageable(self, collection_name: str, partition_name: str) -> None:
        item = self.partition_detail(collection_name, partition_name)
        if not VERSIONED_PARTITION.fullmatch(partition_name):
            raise ValueError("只允许操作 kl_*__vN 版本化 Partition")
        if not item["actions"]["load"] or not item["actions"]["release"]:
            raise ValueError("仅允许操作 ownership 与 Contract 均有效的 DataForge managed Partition")

    def load_partition(self, collection_name: str, partition_name: str) -> dict[str, Any]:
        if not self.milvus:
            raise RuntimeError("DATAFORGE_MILVUS_URI 未配置")
        self._assert_manageable(collection_name, partition_name)
        try:
            self.milvus.load_partition(collection_name, partition_name)
        except Exception as exc:
            raise RuntimeError(self._safe_error(exc)) from exc
        return {"collection_name": collection_name, "partition_name": partition_name, "loaded": True}

    def release_partition(self, collection_name: str, partition_name: str) -> dict[str, Any]:
        if not self.milvus:
            raise RuntimeError("DATAFORGE_MILVUS_URI 未配置")
        self._assert_manageable(collection_name, partition_name)
        try:
            self.milvus.release_partition(collection_name, partition_name)
        except Exception as exc:
            raise RuntimeError(self._safe_error(exc)) from exc
        return {"collection_name": collection_name, "partition_name": partition_name, "released": True}
