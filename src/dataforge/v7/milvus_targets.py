"""Central Milvus service registration and minimal connection verification."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .store import V7Store
from .vector import V7Milvus


class MilvusTargetService:
    def __init__(self, store: V7Store,
                 factory: Callable[[str, str | None], V7Milvus] = V7Milvus):
        self.store = store
        self.factory = factory

    @staticmethod
    def _safe_error(exc: Exception, uri: str) -> str:
        message = str(exc).replace(uri, "[redacted]").strip() or "Milvus 连接失败"
        return message[:1000]

    def verify(self, target_id: str) -> dict[str, Any]:
        target = self.store.get_milvus_target(target_id)
        uri = target.get("candidate_milvus_url") or target["milvus_url"]
        candidate = bool(target.get("candidate_milvus_url"))
        try:
            self.factory(uri, None).list_collections()
        except Exception as exc:
            return self.store.finish_milvus_target_verification(
                target_id, candidate=candidate, passed=False,
                error=self._safe_error(exc, uri),
            )
        return self.store.finish_milvus_target_verification(
            target_id, candidate=candidate, passed=True, error=None,
        )

    def create(self, name: str, milvus_url: str) -> dict[str, Any]:
        target = self.store.create_milvus_target(name, milvus_url)
        return self.verify(target["id"])

    def patch(self, target_id: str, *, name: str | None = None,
              milvus_url: str | None = None) -> dict[str, Any]:
        target = self.store.patch_milvus_target(target_id, name=name, milvus_url=milvus_url)
        return self.verify(target_id) if milvus_url is not None else target
