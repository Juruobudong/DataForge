"""Object storage boundary for new V7 source-object keys."""
from __future__ import annotations

import hashlib
import io
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
