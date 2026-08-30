"""Manual component checks and process-liveness aggregation."""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import httpx
from sqlalchemy import func, select

from ..config import Settings
from .models import KnowledgeAssetVersion, KnowledgeJob, ManagedCollection, VectorSyncJob
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store
from .servings import ServingManager
from .vector import V7Milvus


COMPONENTS = ("mysql", "minio", "disk", "worker", "runner", "mineru", "llm", "embedding", "milvus")
COMPONENT_LABELS = {
    "mysql": "MySQL", "minio": "MinIO", "disk": "磁盘", "worker": "Worker",
    "runner": "Runner", "mineru": "MinerU GPU", "llm": "LLM",
    "embedding": "Embedding", "milvus": "Milvus",
}


def process_instance_id(component: str) -> str:
    return f"{component}:{socket.gethostname()}:{os.getpid()}"


class ComponentCheckService:
    def __init__(self, store: V7Store, settings: Settings, objects: MinioObjectStore | LocalObjectStore,
                 *, milvus_resolver=None):
        self.store, self.settings, self.objects = store, settings, objects
        self.milvus_resolver = milvus_resolver

    @staticmethod
    def validate_components(values: list[str]) -> list[str]:
        if not values:
            raise ValueError("至少选择一个组件")
        if len(values) != len(set(values)):
            raise ValueError("组件列表不能重复")
        unknown = [item for item in values if item not in COMPONENTS]
        if unknown:
            raise ValueError(f"不支持的组件：{unknown[0]}")
        return list(values)

    def run(self, run_id: str, components: list[str]) -> dict[str, Any]:
        probes: dict[str, Callable[[str], dict[str, Any]]] = {
            "mysql": self._mysql, "minio": self._minio, "disk": self._disk,
            "worker": self._worker, "runner": self._runner, "mineru": self._mineru,
            "llm": self._llm, "embedding": self._embedding, "milvus": self._milvus,
        }
        with ThreadPoolExecutor(max_workers=min(4, len(components)), thread_name_prefix="component-check") as pool:
            futures = {pool.submit(probes[name], run_id): name for name in components}
            for future in as_completed(futures):
                component = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": "unavailable", "summary": f"检查失败：{type(exc).__name__}",
                              "latency_ms": None, "error_code": type(exc).__name__, "details": {}}
                self.store.record_component_check_result(run_id, component, **result)
        return self.store.finish_component_check(run_id)

    @staticmethod
    def _timed(action: Callable[[], tuple[str, str, dict[str, Any]]]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            status, summary, details = action()
            return {"status": status, "summary": summary, "latency_ms": round((time.monotonic() - started) * 1000),
                    "details": details, "error_code": None}
        except Exception as exc:
            return {"status": "unavailable", "summary": f"检查失败：{type(exc).__name__}",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error_code": type(exc).__name__, "details": {}}

    def _mysql(self, _: str) -> dict[str, Any]:
        def action():
            with self.store.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return "healthy", "数据库读写链路可用", {"kind": "mysql" if self.settings.database_url else "sqlite-dev"}
        return self._timed(action)

    def _minio(self, run_id: str) -> dict[str, Any]:
        key, payload = f"observability/probes/{run_id}", secrets.token_bytes(32)
        started = time.monotonic()
        try:
            self.objects.put_bytes(key, payload, "application/octet-stream")
            try:
                if self.objects.get_bytes(key) != payload:
                    raise RuntimeError("probe_content_mismatch")
            finally:
                self.objects.delete_key(key)
            return {"status": "healthy", "summary": "对象存储写入、读取和清理通过",
                    "latency_ms": round((time.monotonic() - started) * 1000), "error_code": None,
                    "details": {"probe_key": key}}
        except Exception as exc:
            return {"status": "unavailable", "summary": f"对象存储检查失败：{type(exc).__name__}",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error_code": type(exc).__name__, "details": {"probe_key": key}}

    def _disk(self, _: str) -> dict[str, Any]:
        def action():
            usage = shutil.disk_usage(self.settings.state_dir)
            status = "healthy" if usage.free >= 1024 ** 3 else "degraded"
            return status, "磁盘空间正常" if status == "healthy" else "可用空间不足 1 GiB", {
                "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
            }
        return self._timed(action)

    def _worker(self, _: str) -> dict[str, Any]:
        def action():
            rows = self.store.list_component_heartbeats("worker")
            live = [row for row in rows if not row["stale"]]
            with self.store.sessions() as session:
                queued = session.scalar(select(func.count(KnowledgeJob.id)).where(KnowledgeJob.status == "queued")) or 0
            if not live:
                return "unavailable", "没有 45 秒内的 Worker 心跳", {"queued_jobs": int(queued)}
            return "healthy", f"{len(live)} 个 Worker 实例在线", {"live_instances": len(live), "queued_jobs": int(queued)}
        return self._timed(action)

    def _runner_call(self, path: str, *, timeout: float = 30.0) -> dict[str, Any]:
        if not self.settings.runner_url:
            raise RuntimeError("runner_not_configured")
        headers = ({"Authorization": f"Bearer {self.settings.runner_service_token}"}
                   if self.settings.runner_service_token else {})
        response = httpx.post(f"{self.settings.runner_url.rstrip('/')}{path}", headers=headers, timeout=timeout)
        response.raise_for_status()
        return dict(response.json())

    def _runner(self, _: str) -> dict[str, Any]:
        return self._timed(lambda: ("healthy", "Runner HTTP 与运行时可用", self._runner_call("/internal/diagnostics/runner", timeout=5)))

    def _mineru(self, _: str) -> dict[str, Any]:
        return self._timed(lambda: ("healthy", "MinerU health 与最小 PDF 解析通过", self._runner_call("/internal/diagnostics/mineru", timeout=30)))

    def _llm(self, _: str) -> dict[str, Any]:
        result = self._timed(lambda: ("healthy", "LLM 最小 JSON 调用通过", self._runner_call("/internal/diagnostics/llm", timeout=30)))
        if result["status"] == "healthy" and (result["latency_ms"] or 0) > 5000:
            result["status"], result["summary"] = "degraded", "LLM 可用但延迟超过 5 秒"
        return result

    def _embedding(self, _: str) -> dict[str, Any]:
        def action():
            manager = ServingManager(self.store.sessions, self.settings.config_encryption_key)
            default = next((item for item in manager.list("embedding") if item["is_default"]), None)
            if not default or not default["base_url"]:
                return "not_configured", "Embedding 未配置", {}
            result = manager.test("embedding", default["id"])
            if result["last_check_status"] != "healthy":
                raise RuntimeError(result["last_check_error"] or result["last_check_status"])
            return "healthy", "Embedding 单条向量与维度校验通过", {
                "dimension": result["last_observed_dimension"], "model": result["model_name"],
                "serving_code": result["serving_code"],
            }
        result = self._timed(action)
        if result["status"] == "healthy" and (result["latency_ms"] or 0) > 2000:
            result["status"], result["summary"] = "degraded", "Embedding 可用但延迟超过 2 秒"
        return result

    def _milvus(self, _: str) -> dict[str, Any]:
        def action():
            if not self.milvus_resolver:
                return "not_configured", "知识生产 Milvus 未配置", {}
            connection = self.milvus_resolver.authoring()
            milvus = V7Milvus(connection.uri, connection.token)
            names = set(milvus.list_collections())
            with self.store.sessions() as session:
                managed = session.scalar(select(ManagedCollection).where(ManagedCollection.status == "ready")
                                         .order_by(ManagedCollection.collection_name).limit(1))
                asset = session.scalar(select(KnowledgeAssetVersion).where(KnowledgeAssetVersion.status == "ready")
                                       .order_by(KnowledgeAssetVersion.ready_at.desc()).limit(1))
            if not managed:
                return "degraded", "Milvus 可连接，尚无 Ready 受管 Collection 可验证", {
                    "collection_count": len(names), "write_verified": False,
                    "target_revision_id": connection.revision_id,
                }
            if managed.collection_name not in names:
                raise RuntimeError("managed_collection_missing")
            observed = milvus.inspect_collection(managed.collection_name)
            query_verified = False
            if asset and asset.collection_name == managed.collection_name and asset.partition_name in observed.get("partitions", []):
                milvus.client().query(collection_name=asset.collection_name, partition_names=[asset.partition_name], filter="", limit=1)
                query_verified = True
            status = "healthy" if query_verified else "degraded"
            summary = "Milvus 精确 Collection/Partition 查询通过" if query_verified else "Milvus Collection 可读，暂无可查询 Ready Partition"
            return status, summary, {"collection_name": managed.collection_name, "partition_query_verified": query_verified, "write_verified": False}
        return self._timed(action)


def components_snapshot(store: V7Store, *, detailed: bool) -> dict[str, Any]:
    latest, heartbeats = store.latest_component_results(), store.list_component_heartbeats()
    by_component: dict[str, list[dict[str, Any]]] = {}
    for row in heartbeats:
        by_component.setdefault(row["component"], []).append(row)
    rows = []
    for component in COMPONENTS:
        result = dict(latest.get(component) or {"status": "unknown", "stale": True, "summary": "尚未手动检查"})
        instances = by_component.get(component, [])
        if component in {"worker", "runner"}:
            live = [item for item in instances if not item["stale"]]
            if live:
                result.update({"status": "healthy", "stale": False, "summary": f"{len(live)} 个实例在线",
                               "age_seconds": min(item["age_seconds"] for item in live)})
            elif instances:
                result.update({"status": "unavailable", "stale": True, "summary": "进程心跳已过期"})
        row = {"component": component, "label": COMPONENT_LABELS[component], **result, "checkable": True}
        if detailed:
            row["instances"] = instances
        else:
            row.pop("details", None); row.pop("error_code", None); row.pop("checked_at", None)
        rows.append(row)
    overall = "unavailable" if any(row["status"] == "unavailable" for row in rows) else (
        "degraded" if any(row["status"] in {"degraded", "unknown"} for row in rows) else "healthy")
    statuses = {row["component"]: row["status"] for row in rows}
    with store.sessions() as session:
        queued_jobs = int(session.scalar(select(func.count(KnowledgeJob.id)).where(KnowledgeJob.status == "queued")) or 0)
        queued_vectors = int(session.scalar(select(func.count(VectorSyncJob.id)).where(VectorSyncJob.status == "queued")) or 0)
    diagnoses: list[dict[str, Any]] = []
    if queued_jobs and statuses.get("worker") == "unavailable":
        diagnoses.append({"component": "worker", "message": "可能原因：Worker 不在线", "affected_jobs": queued_jobs})
    elif queued_jobs and statuses.get("runner") == "unavailable":
        diagnoses.append({"component": "runner", "message": "可能原因：Runner 投递链异常", "affected_jobs": queued_jobs})
    for component, message in (("mineru", "可能原因：MinerU/GPU/模型链异常"), ("llm", "可能原因：Model Serving 异常")):
        if queued_jobs and statuses.get(component) == "unavailable":
            diagnoses.append({"component": component, "message": message, "affected_jobs": queued_jobs})
    for component in ("embedding", "milvus"):
        if queued_vectors and statuses.get(component) == "unavailable":
            diagnoses.append({"component": component, "message": f"可能原因：{COMPONENT_LABELS[component]} 向量链异常", "affected_jobs": queued_vectors})
    return {"status": overall, "components": rows, "diagnoses": diagnoses}
