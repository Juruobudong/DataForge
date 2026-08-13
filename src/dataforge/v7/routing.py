"""Durable, atomic RoutingSnapshot publication for downstream read-only mounts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AtomicRoutingPublisher:
    def __init__(self, root: Path):
        self.root = root

    def publish(self, project_code: str, version_no: int, snapshot: dict[str, Any]) -> tuple[str, str]:
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        checksum = hashlib.sha256(encoded).hexdigest()
        target_dir = self.root / project_code
        history = target_dir / "history"
        history.mkdir(parents=True, exist_ok=True)
        payload = {**snapshot, "version": version_no, "checksum": checksum}
        written = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        history_target = history / f"routing-{version_no:06d}.json"
        self._atomic_write(history_target, written)
        self._atomic_write(target_dir / "routing.json", written)
        return checksum, str(history_target.relative_to(self.root)).replace("\\", "/")

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, target)
            # Consumers get a read-only mount of this directory.  Do not make
            # the writer's current file read-only here: on Windows that blocks
            # the next os.replace and breaks atomic rollback publication.
        finally:
            Path(temporary).unlink(missing_ok=True)
