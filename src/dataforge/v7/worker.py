from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import os
import socket
import time
import uuid

import httpx

from ..config import Settings
from .documents import DocumentDeletionService
from .instance import InstanceContext
from .local_config import LocalMilvusConfigurationService
from .milvus_targets import MilvusConnectionResolver
from .migration.exporter import MigrationExporter
from .migration.importer import MigrationImporter
from .provisioning import ManagedCollectionDeletionService
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store
from .vector import (
    KnowledgeAssetGcService, LibraryDeletionService, V7Milvus,
    VectorDeletionService, VectorSyncService,
)


LOGGER = logging.getLogger(__name__)
LEASE_RENEW_SECONDS = 60.0
HEARTBEAT_SECONDS = 15.0


@dataclass
class ActiveWork:
    kind: str
    work_id: str
    owner: str
    future: Future[int]
    next_renew_at: float


def _owner_token(kind: str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{kind}:{uuid.uuid4().hex}"


def _objects(resolved: Settings):
    return (MinioObjectStore(
        resolved.minio_endpoint, resolved.minio_access_key, resolved.minio_secret_key,
        resolved.minio_bucket,
    ) if resolved.minio_endpoint and resolved.minio_access_key and resolved.minio_secret_key
        else LocalObjectStore(resolved.state_dir / "v7-objects"))


def _authoring_connection(store: V7Store, resolved: Settings):
    instance = InstanceContext.load(store, resolved)
    if instance.mode == "local":
        return LocalMilvusConfigurationService(
            store, resolved.config_encryption_key,
        ).verified(instance.id, "current_target")
    return MilvusConnectionResolver(store, resolved.config_encryption_key).authoring(instance.id)


def _run_migration_once(store: V7Store, resolved: Settings, owner: str) -> int | None:
    claim_migration = getattr(store, "claim_migration_job", None)
    migration_job = claim_migration(owner) if claim_migration else None
    if not migration_job:
        return None
    instance = InstanceContext.load(store, resolved)
    objects = _objects(resolved)
    try:
        if migration_job.direction == "export":
            connection = _authoring_connection(store, resolved)
            milvus = connection.client()
            if not resolved.migration_signing_private_key:
                raise ValueError("未配置 migration Ed25519 签名私钥")
            MigrationExporter(
                store, objects, milvus, migration_dir=resolved.migration_dir,
                private_key=resolved.migration_signing_private_key,
                key_id=resolved.migration_signing_key_id, instance=instance,
            ).run(migration_job.id)
        else:
            if not resolved.migration_trusted_public_keys:
                raise ValueError("未配置 migration 受信 Ed25519 公钥")
            local_service = LocalMilvusConfigurationService(store, resolved.config_encryption_key)
            try:
                milvus = local_service.verified(instance.id, "current_target").client()
            except ValueError:
                milvus = None
            MigrationImporter(
                store, objects, milvus, migration_dir=resolved.migration_dir,
                trusted_public_keys=resolved.migration_trusted_public_keys,
                instance=instance, routing_dir=resolved.routing_dir,
                local_config=local_service,
            ).run(migration_job.id)
        return 1
    except Exception as exc:
        LOGGER.exception("迁移任务失败：%s", exc)
        current = store.get_migration_job(migration_job.id)
        if current["status"] != "failed":
            store.update_migration_job(migration_job.id, status="failed", stage="failed", error=str(exc))
        return 0


def _run_derived_once(store: V7Store, resolved: Settings, owner: str) -> int | None:
    if not getattr(resolved, "derived_runs_enabled", False):
        return None
    claim_derived = getattr(store, "claim_derived_run", None)
    derived_run = claim_derived(owner) if claim_derived else None
    if not derived_run:
        return None
    if not resolved.runner_url:
        store.finish_flow_run(derived_run.id, "DATAFORGE_RUNNER_URL 未配置", status="failed")
        return 0
    headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
    try:
        response = httpx.post(
            f"{resolved.runner_url.rstrip('/')}/internal/jobs",
            json={"flow_run_id": derived_run.id}, headers=headers,
            timeout=resolved.runner_timeout_seconds,
        )
        response.raise_for_status()
        return 1
    except httpx.HTTPError as exc:
        store.finish_flow_run(derived_run.id, f"Runner 投递失败：{exc}", status="failed")
        return 0


def _run_debug_once(store: V7Store, resolved: Settings, owner: str) -> int | None:
    claim_debug = getattr(store, "claim_debug_run", None)
    debug_run = claim_debug(owner) if claim_debug else None
    if not debug_run:
        return None
    if not resolved.runner_url:
        store.finish_flow_run(debug_run.id, "DATAFORGE_RUNNER_URL 未配置", status="failed")
        return 0
    headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
    try:
        response = httpx.post(
            f"{resolved.runner_url.rstrip('/')}/internal/jobs",
            json={"flow_run_id": debug_run.id}, headers=headers,
            timeout=resolved.runner_timeout_seconds,
        )
        response.raise_for_status()
        return 1
    except httpx.HTTPError as exc:
        store.finish_flow_run(debug_run.id, f"Runner 投递失败：{exc}", status="failed")
        return 0


def _run_knowledge_job(store: V7Store, resolved: Settings, job_id: str, owner: str) -> int:
    try:
        if not resolved.runner_url:
            store.mark_job_failed(job_id, "DATAFORGE_RUNNER_URL 未配置")
            return 0
        headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
        response = httpx.post(
            f"{resolved.runner_url.rstrip('/')}/internal/jobs",
            json={"job_id": job_id, "lease_owner": owner}, headers=headers,
            timeout=resolved.runner_timeout_seconds,
        )
        response.raise_for_status()
        return 1
    except httpx.HTTPError as exc:
        current = store.get_job(job_id)
        if current.status == "running" and getattr(current, "lease_owner", owner) == owner:
            store.mark_job_failed(job_id, f"Runner 投递失败：{exc}")
        return 0
    except Exception as exc:
        LOGGER.exception("知识任务失败：%s", exc)
        current = store.get_job(job_id)
        if current.status == "running" and getattr(current, "lease_owner", owner) == owner:
            store.mark_job_failed(job_id, str(exc))
        return 0
    finally:
        release = getattr(store, "release_work_lease", None)
        if release:
            release("knowledge", job_id, owner)


def _run_parse_job(store: V7Store, resolved: Settings, job_id: str, owner: str) -> int:
    try:
        if not resolved.runner_url:
            store.finish_parse_job(job_id, error="DATAFORGE_RUNNER_URL 未配置")
            return 0
        headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
        response = httpx.post(
            f"{resolved.runner_url.rstrip('/')}/internal/jobs",
            json={"parse_job_id": job_id, "lease_owner": owner}, headers=headers,
            timeout=resolved.runner_timeout_seconds,
        )
        response.raise_for_status(); return 1
    except Exception as exc:
        LOGGER.exception("ParseJob 失败：%s", exc)
        store.finish_parse_job(job_id, error=str(exc)); return 0


def _run_vector_sync_job(store: V7Store, job_id: str, owner: str) -> int:
    try:
        resolved = Settings.load()
        result = VectorSyncService.from_connection(
            store, _authoring_connection(store, resolved),
        ).run(job_id, lease_owner=owner)
        if result["status"] == "failed":
            LOGGER.error("向量同步失败：%s", result.get("error"))
        return 1
    except Exception as exc:
        LOGGER.exception("向量同步失败：%s", exc)
        try:
            store.assert_work_lease("vector_sync", job_id, owner)
        except ValueError:
            return 0
        store.finish_vector_sync(job_id, [], str(exc))
        return 0
    finally:
        store.release_work_lease("vector_sync", job_id, owner)


def _run_maintenance_once(store: V7Store, resolved: Settings, owner: str) -> int | None:
    asset_gc_job = store.claim_knowledge_asset_gc_job()
    if asset_gc_job:
        milvus = _authoring_connection(store, resolved).client()
        result = KnowledgeAssetGcService(
            store, milvus, resolver=MilvusConnectionResolver(store, resolved.config_encryption_key),
        ).run(asset_gc_job.id)
        if result["status"] == "failed": LOGGER.error("AssetVersion GC 失败：%s", result.get("error"))
        return 1
    vector_deletion_job = store.claim_vector_deletion_job(owner)
    if vector_deletion_job:
        milvus = _authoring_connection(store, resolved).client()
        result = VectorDeletionService(store, milvus).run(vector_deletion_job.id)
        if result["status"] == "failed": LOGGER.error("向量删除失败：%s", result.get("error"))
        return 1
    deletion_job = store.claim_library_deletion_job(owner)
    if deletion_job:
        milvus = _authoring_connection(store, resolved).client()
        result = LibraryDeletionService(store, milvus).run(deletion_job.id)
        if result["status"] == "failed": LOGGER.error("知识库删除失败：%s", result.get("error"))
        return 1
    document_deletion_job = store.claim_document_deletion_job(owner)
    if document_deletion_job:
        result = DocumentDeletionService.from_environment(store, resolved).run(document_deletion_job)
        if result["status"] == "failed": LOGGER.error("文档删除失败：%s", result.get("error"))
        return 1
    claim_collection_deletion = getattr(store, "claim_managed_collection_deletion_job", None)
    collection_deletion_job = claim_collection_deletion(owner) if claim_collection_deletion else None
    if collection_deletion_job:
        milvus = _authoring_connection(store, resolved).client()
        result = ManagedCollectionDeletionService(store, milvus).run(collection_deletion_job.id)
        if result["status"] == "failed": LOGGER.error("受管 Collection 删除失败：%s", result.get("error"))
        return 1
    return None


def _run_exclusive_once(store: V7Store, resolved: Settings) -> int | None:
    owner = _owner_token("exclusive")
    result = _run_migration_once(store, resolved, owner)
    if result is not None:
        return result
    result = _run_debug_once(store, resolved, owner)
    if result is not None:
        return result
    result = _run_derived_once(store, resolved, owner)
    if result is not None:
        return result
    return _run_maintenance_once(store, resolved, owner)


def run_once(settings: Settings | None = None, *, check_schema: bool = True) -> int:
    resolved = settings or Settings.load()
    store = V7Store(resolved.platform_database_url)
    if check_schema:
        store.assert_schema_current()
    owner = _owner_token("once")
    result = _run_migration_once(store, resolved, owner)
    if result is not None:
        return result
    result = _run_debug_once(store, resolved, owner)
    if result is not None:
        return result
    result = _run_derived_once(store, resolved, owner)
    if result is not None:
        return result
    parse_job = store.claim_parse_job(owner)
    if parse_job:
        return _run_parse_job(store, resolved, parse_job.id, owner)
    job = store.claim_job(owner)
    if job:
        return _run_knowledge_job(store, resolved, job.id, owner)
    vector_job = store.claim_vector_sync_job(owner)
    if vector_job:
        return _run_vector_sync_job(store, vector_job.id, owner)
    result = _run_maintenance_once(store, resolved, owner)
    return result if result is not None else 0


def run_forever(settings: Settings | None = None, *, poll_seconds: float = 2.0, stop_event=None) -> None:
    """Run dedicated knowledge/vector pools with an exclusive maintenance barrier."""
    resolved = settings or Settings.load()
    store = V7Store(resolved.platform_database_url)
    store.assert_schema_current()
    heartbeat_instance = f"worker:{socket.gethostname()}:{os.getpid()}"
    next_heartbeat = 0.0
    def heartbeat(active_work: list[ActiveWork]) -> None:
        nonlocal next_heartbeat
        now = time.monotonic()
        if now < next_heartbeat:
            return
        write_heartbeat = getattr(store, "upsert_component_heartbeat", None)
        if write_heartbeat is None:
            next_heartbeat = now + HEARTBEAT_SECONDS
            return
        current = [item.work_id for item in active_work]
        try:
            write_heartbeat(
                "worker", heartbeat_instance, version="7.0.0", worker_id=heartbeat_instance,
                current_job_id=current[0] if current else None,
                details={"active_job_ids": current, "active_count": len(current),
                         "knowledge_concurrency": getattr(resolved, "knowledge_job_concurrency", 3),
                         "vector_concurrency": getattr(resolved, "vector_sync_concurrency", 2),
                         "parse_concurrency": getattr(resolved, "parse_concurrency", 2)},
            )
        except Exception:
            LOGGER.exception("Worker 心跳写入失败")
        next_heartbeat = now + HEARTBEAT_SECONDS
    if not hasattr(store, "claim_job"):
        while stop_event is None or not stop_event.is_set():
            heartbeat([])
            run_once(resolved, check_schema=False)
            time.sleep(max(poll_seconds, 0.5))
        return
    interval = max(poll_seconds, 0.5)
    active: list[ActiveWork] = []
    draining = False
    knowledge_concurrency = getattr(resolved, "knowledge_job_concurrency", 3)
    vector_concurrency = getattr(resolved, "vector_sync_concurrency", 2)
    parse_concurrency = getattr(resolved, "parse_concurrency", 2)
    with ThreadPoolExecutor(max_workers=parse_concurrency, thread_name_prefix="document-parse") as parse_pool, \
            ThreadPoolExecutor(max_workers=knowledge_concurrency, thread_name_prefix="knowledge") as knowledge_pool, \
            ThreadPoolExecutor(max_workers=vector_concurrency, thread_name_prefix="vector") as vector_pool:
        while stop_event is None or not stop_event.is_set():
            heartbeat(active)
            now = time.monotonic()
            remaining: list[ActiveWork] = []
            for work in active:
                if work.future.done():
                    try:
                        work.future.result()
                    except Exception:
                        LOGGER.exception("并发任务未捕获异常：%s", work.work_id)
                    continue
                if now >= work.next_renew_at:
                    renewed = (store.renew_parse_lease(work.work_id, work.owner)
                               if work.kind == "parse"
                               else store.renew_work_lease(work.kind, work.work_id, work.owner))
                    if not renewed:
                        LOGGER.error("任务租约续期失败：%s %s", work.kind, work.work_id)
                    work.next_renew_at = now + LEASE_RENEW_SECONDS
                remaining.append(work)
            active = remaining

            has_exclusive = getattr(store, "has_pending_exclusive_work", lambda: False)()
            if has_exclusive:
                draining = True
            if draining:
                if not active:
                    _run_exclusive_once(store, resolved)
                    draining = False
                time.sleep(interval)
                continue

            knowledge_active = sum(item.kind == "knowledge" for item in active)
            parse_active = sum(item.kind == "parse" for item in active)
            while parse_active < parse_concurrency:
                owner = _owner_token("parse")
                job = store.claim_parse_job(owner)
                if not job: break
                future = parse_pool.submit(_run_parse_job, store, resolved, job.id, owner)
                active.append(ActiveWork("parse", job.id, owner, future, now + LEASE_RENEW_SECONDS))
                parse_active += 1

            while knowledge_active < knowledge_concurrency:
                owner = _owner_token("knowledge")
                job = store.claim_job(owner)
                if not job:
                    break
                future = knowledge_pool.submit(_run_knowledge_job, store, resolved, job.id, owner)
                active.append(ActiveWork("knowledge", job.id, owner, future, now + LEASE_RENEW_SECONDS))
                knowledge_active += 1

            vector_active = sum(item.kind == "vector_sync" for item in active)
            while vector_active < vector_concurrency:
                owner = _owner_token("vector")
                job = store.claim_vector_sync_job(owner)
                if not job:
                    break
                future = vector_pool.submit(_run_vector_sync_job, store, job.id, owner)
                active.append(ActiveWork("vector_sync", job.id, owner, future, now + LEASE_RENEW_SECONDS))
                vector_active += 1

            if not active:
                _run_exclusive_once(store, resolved)
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="DataForge V7 persistent worker")
    parser.add_argument("--once", action="store_true"); parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(); logging.basicConfig(level=logging.ERROR)
    if args.once: run_once(); return
    run_forever(poll_seconds=args.poll_seconds)
