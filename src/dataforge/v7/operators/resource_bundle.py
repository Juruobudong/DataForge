"""Dependency-free verification shared by maintenance tooling and runtime."""
import hashlib
from pathlib import Path
import re


def bundle_digest(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("OPERATOR_RESOURCE_MISSING: 模型或分词资源目录不存在")
    checksum = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file() and ".locks" not in path.parts)
    if not files:
        raise ValueError("OPERATOR_RESOURCE_MISSING: 资源目录为空")
    for path in files:
        if not path.resolve().is_relative_to(root):
            raise ValueError("OPERATOR_RESOURCE_INVALID: 资源不能指向目录之外")
        checksum.update(path.relative_to(root).as_posix().encode())
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                checksum.update(block)
    return checksum.hexdigest()


def verify_bundle(bundle):
    if (not isinstance(bundle, dict) or not isinstance(bundle.get("root"), str) or not Path(bundle["root"]).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("digest", "")))):
        raise ValueError("OPERATOR_RESOURCE_INVALID: 资源清单缺少绝对路径或合法摘要")
    if bundle_digest(bundle["root"]) != bundle["digest"]:
        raise ValueError("OPERATOR_RESOURCE_DRIFT: 模型或分词资源与登记摘要不一致")
