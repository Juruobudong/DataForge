from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from .errors import NotFoundError


class BlobStore:
    """Content-addressed immutable storage for sources and generated assets."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, source: Path) -> tuple[str, str, int]:
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        sha256 = digest.hexdigest()
        target = self._path_for_hash(sha256)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return f"blob://{sha256}", sha256, size

    def put_bytes(self, payload: bytes) -> tuple[str, str, int]:
        sha256 = hashlib.sha256(payload).hexdigest()
        target = self._path_for_hash(sha256)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_bytes(payload)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return f"blob://{sha256}", sha256, len(payload)

    def resolve(self, uri: str) -> Path:
        if not uri.startswith("blob://"):
            raise NotFoundError(f"Unsupported blob URI: {uri}")
        sha256 = uri.removeprefix("blob://")
        path = self._path_for_hash(sha256)
        if not path.is_file():
            raise NotFoundError(f"Blob does not exist: {uri}")
        return path

    def _path_for_hash(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / sha256
