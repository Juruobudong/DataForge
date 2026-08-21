"""Object storage boundary for new V7 source-object keys."""
from __future__ import annotations

import hashlib
import io
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size_bytes: int


class MinioObjectStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False):
        from minio import Minio
        self.client = Minio(endpoint.removeprefix("http://").removeprefix("https://"), access_key, secret_key, secure=secure or endpoint.startswith("https://"))
        self.bucket = bucket
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def put_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        self.client.put_object(self.bucket, key, io.BytesIO(payload), len(payload), content_type=content_type)
        return StoredObject(key, hashlib.sha256(payload).hexdigest(), len(payload))

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close(); response.release_conn()

    def delete_key(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)

    def put_stream(self, key: str, source, size_bytes: int, content_type: str = "application/octet-stream") -> StoredObject:
        digest = hashlib.sha256()
        class HashingReader:
            def read(self, size: int = -1):
                chunk = source.read(size)
                if chunk: digest.update(chunk)
                return chunk
        self.client.put_object(self.bucket, key, HashingReader(), size_bytes, content_type=content_type)
        return StoredObject(key, digest.hexdigest(), size_bytes)

    def copy_to(self, key: str, target: Path, chunk_size: int = 1024 * 1024) -> StoredObject:
        response = self.client.get_object(self.bucket, key)
        digest, total = hashlib.sha256(), 0
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as handle:
                while chunk := response.read(chunk_size):
                    handle.write(chunk); digest.update(chunk); total += len(chunk)
        finally:
            response.close(); response.release_conn()
        return StoredObject(key, digest.hexdigest(), total)


class LocalObjectStore:
    def __init__(self, root: Path):
        self.root = root; root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        target = self.root / key; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
        return StoredObject(key, hashlib.sha256(payload).hexdigest(), len(payload))

    def get_bytes(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete_key(self, key: str) -> None:
        target = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in target.parents:
            raise ValueError("拒绝删除 V7 对象存储根目录之外的路径")
        target.unlink(missing_ok=True)

    def put_stream(self, key: str, source, size_bytes: int, content_type: str = "application/octet-stream") -> StoredObject:
        target = self._safe_target(key); target.parent.mkdir(parents=True, exist_ok=True)
        digest, total = hashlib.sha256(), 0
        with target.open("wb") as handle:
            while chunk := source.read(1024 * 1024):
                handle.write(chunk); digest.update(chunk); total += len(chunk)
        if total != size_bytes:
            target.unlink(missing_ok=True)
            raise ValueError("对象流长度与声明长度不一致")
        return StoredObject(key, digest.hexdigest(), total)

    def copy_to(self, key: str, target: Path, chunk_size: int = 1024 * 1024) -> StoredObject:
        source = self._safe_target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, total = hashlib.sha256(), 0
        with source.open("rb") as reader, target.open("wb") as writer:
            while chunk := reader.read(chunk_size):
                writer.write(chunk); digest.update(chunk); total += len(chunk)
        return StoredObject(key, digest.hexdigest(), total)

    def _safe_target(self, key: str) -> Path:
        target = (self.root / key).resolve(); root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("拒绝访问 V7 对象存储根目录之外的路径")
        return target
