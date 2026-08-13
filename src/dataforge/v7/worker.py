from __future__ import annotations

import argparse
import logging
import os
import socket
import time

import httpx

from ..config import Settings
from .documents import DocumentDeletionService
from .store import V7Store
from .vector import LibraryDeletionService, VectorDeletionService, VectorSyncService


LOGGER = logging.getLogger(__name__)


def run_once(settings: Settings | None = None, *, check_schema: bool = True) -> int:
    resolved = settings or Settings.load()
    store = V7Store(resolved.platform_database_url)
    if check_schema:
        store.assert_schema_current()
    owner = f"{socket.gethostname()}:{os.getpid()}"
    job = store.claim_job(owner)
    if job:
        if not resolved.runner_url:
            store.mark_job_failed(job.id, "DATAFORGE_RUNNER_URL 未配置"); return 0
        headers = {"Authorization": f"Bearer {resolved.runner_service_token}"} if resolved.runner_service_token else {}
        try:
            response = httpx.post(f"{resolved.runner_url.rstrip('/')}/internal/jobs", json={"job_id": job.id}, headers=headers, timeout=300)
            response.raise_for_status(); return 1
        except httpx.HTTPError as exc:
            store.mark_job_failed(job.id, f"Runner 投递失败：{exc}"); return 0
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
