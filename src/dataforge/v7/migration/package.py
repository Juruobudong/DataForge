"""ZIP64 package assembly, checksum verification and Ed25519 signatures."""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .manifest import validate_manifest


MAX_ENTRY_COUNT = 1_000_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024 * 1024


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ValueError("migration entry 路径无效")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or ":" in path.parts[0]:
        raise ValueError("migration entry 不能越过包根目录")
    return str(path)


def _private_key(value: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    encoded = value.strip().encode("utf-8")
    if value.lstrip().startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(encoded, password=None)
        if not isinstance(key, Ed25519PrivateKey): raise ValueError("签名私钥必须是 Ed25519")
        return key
    raw = base64.b64decode(value)
    return Ed25519PrivateKey.from_private_bytes(raw)


def _public_key(value: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    encoded = value.strip().encode("utf-8")
    if value.lstrip().startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(encoded)
        if not isinstance(key, Ed25519PublicKey): raise ValueError("受信公钥必须是 Ed25519")
        return key
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(value))


class MigrationPackageBuilder:
    def __init__(self, output_path: Path, *, key_id: str, private_key: str):
        self.output_path, self.key_id, self.private_key = output_path, key_id, private_key
        self._bytes: dict[str, bytes] = {}
        self._files: dict[str, Path] = {}

    def add_bytes(self, name: str, payload: bytes) -> None:
        name = _safe_name(name)
        if name in self._bytes or name in self._files or name in {"checksums.json", "signature.json", "manifest.json"}:
            raise ValueError(f"migration entry 重复或保留：{name}")
        self._bytes[name] = payload

    def add_file(self, name: str, path: Path) -> None:
        name = _safe_name(name)
        if name in self._bytes or name in self._files or name in {"checksums.json", "signature.json", "manifest.json"}:
            raise ValueError(f"migration entry 重复或保留：{name}")
        if not path.is_file(): raise ValueError(f"migration entry 文件不存在：{path}")
        self._files[name] = path

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024): digest.update(chunk)
        return digest.hexdigest()

    def build(self, manifest: dict[str, Any]) -> dict[str, Any]:
        validate_manifest(manifest)
        manifest_bytes = _json_bytes(manifest)
        checksums = {"manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}
        checksums.update({name: hashlib.sha256(payload).hexdigest() for name, payload in self._bytes.items()})
        checksums.update({name: self._file_hash(path) for name, path in self._files.items()})
        checksum_bytes = _json_bytes({"algorithm": "sha256", "entries": dict(sorted(checksums.items()))})
        signature = _private_key(self.private_key).sign(manifest_bytes + b"\n" + checksum_bytes)
        signature_bytes = _json_bytes({"algorithm": "Ed25519", "key_id": self.key_id,
                                       "signature": base64.b64encode(signature).decode("ascii")})
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                for name, payload in sorted(self._bytes.items()): archive.writestr(name, payload)
                for name, path in sorted(self._files.items()): archive.write(path, arcname=name)
                archive.writestr("checksums.json", checksum_bytes)
                archive.writestr("signature.json", signature_bytes)
            temporary.replace(self.output_path)
        finally:
            temporary.unlink(missing_ok=True)
        package_digest = self._file_hash(self.output_path)
        return {"path": str(self.output_path), "sha256": package_digest, "package_id": manifest["package_id"],
                "entry_count": len(checksums) + 2}


def _trusted_keys(value: str | dict[str, str]) -> dict[str, str]:
    if isinstance(value, dict): return value
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise ValueError("受信公钥配置必须是 key_id 到公钥的 JSON 对象")
    return parsed


def inspect_package(path: Path, trusted_public_keys: str | dict[str, str]) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRY_COUNT: raise ValueError("migration package entry 数量过多")
        names, total = set(), 0
        for info in infos:
            name = _safe_name(info.filename)
            if name in names: raise ValueError("migration package 包含重复 entry")
            if info.is_dir(): raise ValueError("migration package 不允许空目录 entry")
            names.add(name); total += int(info.file_size)
            if total > MAX_UNCOMPRESSED_BYTES: raise ValueError("migration package 解压后体积超过上限")
        required = {"manifest.json", "checksums.json", "signature.json"}
        if not required.issubset(names): raise ValueError("migration package 缺少 manifest/checksum/signature")
        manifest_bytes = archive.read("manifest.json")
        checksum_bytes = archive.read("checksums.json")
        signature_info = json.loads(archive.read("signature.json"))
        if signature_info.get("algorithm") != "Ed25519": raise ValueError("migration package 签名算法无效")
        key_id = signature_info.get("key_id"); keys = _trusted_keys(trusted_public_keys)
        if key_id not in keys: raise ValueError("migration package 签名 key_id 不受信任")
        try:
            _public_key(keys[key_id]).verify(base64.b64decode(signature_info["signature"]),
                                             manifest_bytes + b"\n" + checksum_bytes)
        except Exception as exc:
            raise ValueError("migration package 签名验证失败") from exc
        manifest = validate_manifest(json.loads(manifest_bytes))
        checksum_doc = json.loads(checksum_bytes)
        if checksum_doc.get("algorithm") != "sha256" or not isinstance(checksum_doc.get("entries"), dict):
            raise ValueError("checksums.json 无效")
        expected_entries = set(checksum_doc["entries"])
        actual_payloads = names - {"checksums.json", "signature.json"}
        if expected_entries != actual_payloads: raise ValueError("checksums.json 与包 entry 范围不一致")
        for name, expected in checksum_doc["entries"].items():
            digest = hashlib.sha256()
            with archive.open(name) as handle:
                while chunk := handle.read(1024 * 1024): digest.update(chunk)
            if digest.hexdigest() != expected: raise ValueError(f"migration entry checksum 失败：{name}")
        return {"manifest": manifest, "key_id": key_id, "signature_status": "verified",
                "entry_count": len(infos), "uncompressed_bytes": total}


def extract_verified_entry(package_path: Path, entry_name: str, target: Path) -> None:
    entry_name = _safe_name(entry_name)
    with zipfile.ZipFile(package_path, "r", allowZip64=True) as archive:
        if entry_name not in archive.namelist(): raise ValueError("migration entry 不存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry_name) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
