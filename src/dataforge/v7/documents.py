"""Document-library background services owned exclusively by V7."""
from __future__ import annotations

from typing import Any

from ..config import Settings
from .models import DocumentDeletionJob
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store


class DocumentDeletionService:
    def __init__(self, store: V7Store, objects: Any):
        self.store, self.objects = store, objects

    @classmethod
    def from_environment(cls, store: V7Store, settings: Settings | None = None) -> "DocumentDeletionService":
        resolved = settings or Settings.load()
        if resolved.minio_endpoint and resolved.minio_access_key and resolved.minio_secret_key:
            objects = MinioObjectStore(resolved.minio_endpoint, resolved.minio_access_key, resolved.minio_secret_key, resolved.minio_bucket)
        else:
            objects = LocalObjectStore(resolved.state_dir / "v7-objects")
        return cls(store, objects)

    def run(self, job: DocumentDeletionJob) -> dict[str, Any]:
        try:
            for object_key in job.object_keys:
                self.objects.delete_key(object_key)
            return self.store.finish_document_deletion(job.id)
        except Exception as exc:
            return self.store.finish_document_deletion(job.id, str(exc))
