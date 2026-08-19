from __future__ import annotations

import argparse
import logging
import os
import socket
import time

import httpx

from ..config import Settings
from .documents import DocumentDeletionService
from .instance import InstanceContext
from .migration.exporter import MigrationExporter
from .migration.importer import MigrationImporter
from .provisioning import ManagedCollectionDeletionService
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store
from .vector import LibraryDeletionService, V7Milvus, VectorDeletionService, VectorSyncService


LOGGER = logging.getLogger(__name__)


def run_once(settings: Settings | None = None, *, check_schema: bool = True) -> int:
    resolved = settings or Settings.load()
    store = V7Store(resolved.platform_database_url)
    if check_schema:
        store.assert_schema_current()
    owner = f"{socket.gethostname()}:{os.getpid()}"
    claim_migration = getattr(store, "claim_migration_job", None)
    migration_job = claim_migration(owner) if claim_migration else None
    if migration_job:
        instance = InstanceContext.load(store, resolved)
        objects = (MinioObjectStore(resolved.minio_endpoint, resolved.minio_access_key, resolved.minio_secret_key,
                                    resolved.minio_bucket)
                   if resolved.minio_endpoint and resolved.minio_access_key and resolved.minio_secret_key
                   else LocalObjectStore(resolved.state_dir / "v7-objects"))
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        if not uri:
            store.update_migration_job(migration_job.id, status="failed", stage="failed",
                                       error="DATAFORGE_MILVUS_URI 未配置")
            return 0
        try:
            milvus = V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN"))
            if migration_job.direction == "export":
                if not resolved.migration_signing_private_key:
                    raise ValueError("未配置 migration Ed25519 签名私钥")
                MigrationExporter(store, objects, milvus, migration_dir=resolved.migration_dir,
                    private_key=resolved.migration_signing_private_key, key_id=resolved.migration_signing_key_id,
                    instance=instance).run(migration_job.id)
            else:
                if not resolved.migration_trusted_public_keys:
                    raise ValueError("未配置 migration 受信 Ed25519 公钥")
                MigrationImporter(store, objects, milvus, migration_dir=resolved.migration_dir,
                    trusted_public_keys=resolved.migration_trusted_public_keys, instance=instance,
                    routing_dir=resolved.routing_dir).run(migration_job.id)
            return 1
        except Exception as exc:
            LOGGER.exception("迁移任务失败：%s", exc)
            current = store.get_migration_job(migration_job.id)
            if current["status"] != "failed":
                store.update_migration_job(migration_job.id, status="failed", stage="failed", error=str(exc))
            return 0
    # Keep compatibility with focused worker fakes and older store adapters while
    # preferring queued derived runs in the real V7 store.
    claim_derived = getattr(store, "claim_derived_run", None)
    derived_run = claim_derived(owner) if claim_derived else None
    if derived_run:
        if not resolved.runner_url:
            store.finish_flow_run(derived_run.id, "DATAFORGE_RUNNER_URL 未配置", status="failed"); return 0
        headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
        try:
            response = httpx.post(f"{resolved.runner_url.rstrip('/')}/internal/jobs", json={"flow_run_id": derived_run.id}, headers=headers,
                                  timeout=resolved.runner_timeout_seconds)
            response.raise_for_status(); return 1
        except httpx.HTTPError as exc:
            store.finish_flow_run(derived_run.id, f"Runner 投递失败：{exc}", status="failed"); return 0
    job = store.claim_job(owner)
    if job:
        if not resolved.runner_url:
            store.mark_job_failed(job.id, "DATAFORGE_RUNNER_URL 未配置"); return 0
        headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
        try:
            response = httpx.post(f"{resolved.runner_url.rstrip('/')}/internal/jobs", json={"job_id": job.id}, headers=headers,
                                  timeout=resolved.runner_timeout_seconds)
            response.raise_for_status(); return 1
        except httpx.HTTPError as exc:
            if store.get_job(job.id).status != "failed":
                store.mark_job_failed(job.id, f"Runner 投递失败：{exc}")
            return 0
    vector_job = store.claim_vector_sync_job(owner)
    if vector_job:
        result = VectorSyncService.from_environment(store).run(vector_job.id)
        if result["status"] == "failed": LOGGER.error("向量同步失败：%s", result.get("error"))
        return 1
    vector_deletion_job = store.claim_vector_deletion_job(owner)
    if vector_deletion_job:
        result = VectorDeletionService.from_environment(store).run(vector_deletion_job.id)
        if result["status"] == "failed": LOGGER.error("向量删除失败：%s", result.get("error"))
        return 1
    deletion_job = store.claim_library_deletion_job(owner)
    if deletion_job:
        result = LibraryDeletionService.from_environment(store).run(deletion_job.id)
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
        result = ManagedCollectionDeletionService.from_environment(store).run(collection_deletion_job.id)
        if result["status"] == "failed": LOGGER.error("受管 Collection 删除失败：%s", result.get("error"))
        return 1
    return 0


def run_forever(settings: Settings | None = None, *, poll_seconds: float = 2.0) -> None:
    """Run the persistent worker after verifying the schema once at startup."""
    resolved = settings or Settings.load()
    V7Store(resolved.platform_database_url).assert_schema_current()
    while True:
        run_once(resolved, check_schema=False)
        time.sleep(max(poll_seconds, 0.5))


def main() -> None:
    parser = argparse.ArgumentParser(description="DataForge V7 persistent worker")
    parser.add_argument("--once", action="store_true"); parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(); logging.basicConfig(level=logging.ERROR)
    if args.once: run_once(); return
    run_forever(poll_seconds=args.poll_seconds)
