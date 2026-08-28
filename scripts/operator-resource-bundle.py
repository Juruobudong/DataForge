"""Portable, verified NLP resource export/import; never downloads or runs a model."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from uuid import uuid4
import zipfile

SCHEMA = "dataforge.operator-resources.v1"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith("/") or "\\" in name or ":" in name
            or any(part in {".", "..", ".locks"} for part in parts)
            or PurePosixPath(name).as_posix() != name):
        raise ValueError("Unsafe resource archive path")
    return parts


def publish_directory(stage, target):
    if target.exists():
        raise ValueError("Resource target appeared during import; refusing to overwrite")
    if os.name == "nt":
        # Windows can deny directory renames even after all archive handles
        # close. Copy verified data into a NEW directory with inherited ACLs.
        # No runtime descriptor/registration exists until this succeeds.
        shutil.copytree(stage, target)
    else:
        stage.replace(target)


def inventory(root):
    root = root.resolve()
    files = [p for p in root.rglob("*") if p.is_file() and ".locks" not in p.relative_to(root).parts]
    # Explicit string ordering is identical on Windows and Linux; do not change
    # the historical runtime bundle_digest algorithm or any frozen fingerprint.
    files.sort(key=lambda p: p.relative_to(root).as_posix())
    digest = hashlib.sha256()
    names = set()
    size = 0
    for path in files:
        name = path.relative_to(root).as_posix()
        safe_name(name)
        if not path.resolve().is_relative_to(root) or name.casefold() in names:
            raise ValueError("External link or case-colliding resource path")
        names.add(name.casefold())
        length = path.stat().st_size
        digest.update(json.dumps([name, length, file_hash(path)], separators=(",", ":"), ensure_ascii=True).encode())
        size += length
    if not files:
        raise ValueError("Resource bundle is empty")
    return files, {"content_digest": digest.hexdigest(), "file_count": len(files), "unpacked_bytes": size}


def export_bundle(resources, descriptor, archive, lock):
    resources = resources.resolve()
    if archive.exists() or lock.exists():
        raise ValueError("Refusing to overwrite a published archive or resource lock")
    if archive.resolve().is_relative_to(resources) or lock.resolve().is_relative_to(resources):
        raise ValueError("Bundle output must be outside the source resource directory")
    spec = importlib.util.spec_from_file_location("resource_bundle", Path(__file__).resolve().parents[1] / "src/dataforge/v7/operators/resource_bundle.py")
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    original = json.loads(descriptor.read_text(encoding="utf-8"))
    if Path(original["root"]).resolve() != resources:
        raise ValueError("Source descriptor root mismatch")
    checker.verify_bundle(original)
    files, content = inventory(resources)
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Create directly under the destination so Windows does not carry the
    # private TemporaryDirectory ACL into the published archive on rename.
    with tempfile.NamedTemporaryFile(dir=archive.parent, prefix=".resource-export-", suffix=".part", delete=False) as output:
        partial = Path(output.name)
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
            for path in files:
                # Store regular files, including dereferenced internal HF links.
                output.write(path, path.relative_to(resources).as_posix())
        checker.verify_bundle(original)
        manifest = {"schema": SCHEMA, "archive_sha256": file_hash(partial), **content,
                    "ner_revision": original["ner_revision"], "nltk_revision": original["nltk_revision"]}
        lock.parent.mkdir(parents=True, exist_ok=True)
        partial.replace(archive)
        lock.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        partial.unlink(missing_ok=True)
    return manifest


def import_bundle(archive, lock, resources, *, ner_revision, nltk_revision):
    manifest = json.loads(lock.read_text(encoding="utf-8"))
    if (manifest.get("schema") != SCHEMA or manifest.get("ner_revision") != ner_revision
            or manifest.get("nltk_revision") != nltk_revision
            or not all(re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(key, ""))) for key in ("archive_sha256", "content_digest"))
            or type(manifest.get("file_count")) is not int or not 1 <= manifest["file_count"] <= 10000
            or type(manifest.get("unpacked_bytes")) is not int or not 0 < manifest["unpacked_bytes"] <= 4 * 1024**3):
        raise ValueError("Invalid or incompatible resource lock")
    if not archive.is_file():
        raise FileNotFoundError("Offline NLP bundle is missing; sync runtime/dataflow/vendor-resources/pii-en-v1.zip with the source checkout")
    if file_hash(archive) != manifest["archive_sha256"]:
        raise ValueError("Offline NLP archive SHA-256 mismatch")
    resources = resources.resolve()
    expected = {key: manifest[key] for key in ("content_digest", "file_count", "unpacked_bytes")}
    if resources.exists():
        if inventory(resources)[1] != expected:
            raise ValueError("Existing resource directory differs; refusing to overwrite")
        return
    resources.parent.mkdir(parents=True, exist_ok=True)
    stage = resources.parent / (".resource-import-" + uuid4().hex)
    stage.mkdir(mode=0o755)
    try:
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) != manifest["file_count"] or sum(item.file_size for item in entries) != manifest["unpacked_bytes"]:
                raise ValueError("Resource archive size or entry count mismatch")
            names = set()
            for item in entries:
                parts = safe_name(item.filename)
                if (item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                        or item.filename.casefold() in names):
                    raise ValueError("Resource archive links or duplicate paths are forbidden")
                names.add(item.filename.casefold())
                target = stage.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        if inventory(stage)[1] != expected:
            raise ValueError("Offline NLP resource content mismatch")
        publish_directory(stage, resources)
        if inventory(resources)[1] != expected:
            raise ValueError("Published NLP resource content mismatch; no runtime may be registered")
    finally:
        if stage.exists():
            if stage.resolve().parent != resources.parent:
                raise ValueError("Refusing cleanup outside the resource parent")
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_bundle(args.resources, args.descriptor, args.archive, args.lock)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
