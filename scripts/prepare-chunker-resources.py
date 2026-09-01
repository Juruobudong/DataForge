"""Provision and register one immutable offline Qwen tokenizer bundle."""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

MODEL = "Qwen/Qwen3-32B"
PROFILE = "qwen3-tokenizer-v1"
REVISION = "9216db5781bf21249d130ec9da846c4624c16137"


def environment_path(value):
    return os.path.normcase(os.path.abspath(value))


def load(root, relative, name):
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_lock(root):
    value = json.loads((root / "runtime/dataflow/chunker-tokenizer-v1.lock.json").read_text(encoding="utf-8"))
    if value.get("model") != MODEL or value.get("revision") != REVISION:
        raise ValueError("Chunker tokenizer source lock does not match the reviewed model")
    return value


def verify_tokenizer_files(resources, revision):
    root = Path(__file__).resolve().parents[1]
    reviewed = source_lock(root)
    snapshot = resources / "hf/hub/models--Qwen--Qwen3-32B/snapshots" / revision
    for name, expected in reviewed["files"].items():
        path = snapshot / name
        if not path.is_file() or path.stat().st_size != expected["size"]:
            raise ValueError(f"Chunker tokenizer file missing or wrong size: {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected["sha256"]:
            raise ValueError(f"Chunker tokenizer file hash mismatch: {name}")


def provision(resources, revision, endpoint="https://huggingface.co"):
    root = Path(__file__).resolve().parents[1]
    reviewed = source_lock(root)
    cache = resources / "hf" / "hub"
    snapshot = cache / "models--Qwen--Qwen3-32B" / "snapshots" / revision
    for name, expected in reviewed["files"].items():
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        request = Request(
            f"{endpoint}/{MODEL}/resolve/{revision}/{name}?download=true",
            headers={"User-Agent": "DataForge-chunker-tokenizer-prefetch/1"},
        )
        received = 0
        print(f"Downloading pinned chunker tokenizer: {name} ({expected['size']} bytes)", flush=True)
        with urlopen(request, timeout=120) as response, target.open("xb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                received += len(block)
                if received > expected["size"]:
                    raise ValueError(f"Chunker tokenizer resource exceeds reviewed size: {name}")
                output.write(block)
    verify_tokenizer_files(resources, revision)
    refs = cache / "models--Qwen--Qwen3-32B" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision, encoding="utf-8")


def verify_tokenizer(resources, revision):
    os.environ.update(
        HF_HOME=str(resources / "hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
    )
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL, revision=revision, cache_dir=str(resources / "hf" / "hub"), local_files_only=True,
    )
    tokens = tokenizer.encode("DataForge 知识生产", add_special_tokens=False)
    if not tokens or not all(isinstance(item, int) for item in tokens):
        raise ValueError("Chunker tokenizer smoke test returned invalid tokens")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tokenizer-revision", default=REVISION)
    parser.add_argument(
        "--download-endpoint", choices=["https://huggingface.co", "https://hf-mirror.com"],
        default="https://huggingface.co",
    )
    parser.add_argument("--offline-tokenizer-directory", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download-only", action="store_true")
    mode.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use the isolated Python 3.12 chunker runtime")
    if args.tokenizer_revision != REVISION:
        parser.error("--tokenizer-revision must match the reviewed qwen3-tokenizer-v1 commit")
    if args.offline_tokenizer_directory and args.register_only:
        parser.error("--offline-tokenizer-directory cannot be combined with --register-only")

    root = Path(__file__).resolve().parents[1]
    checker = load(root, Path("src/dataforge/v7/operators/resource_bundle.py"), "resource_bundle")
    archive = load(root, Path("scripts/operator-resource-bundle.py"), "operator_resource_archive")
    resources = args.resources.resolve()
    descriptor = resources.with_suffix(".bundle.json")
    metadata = {"tokenizer_model": MODEL, "tokenizer_revision": args.tokenizer_revision}
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.is_file() else {"runtimes": []}
    registered = next((
        item for item in manifest.get("runtimes", [manifest])
        if environment_path(item["python"]) == environment_path(sys.executable)
    ), None)
    if registered and registered.get("resource_profiles"):
        existing = registered["resource_profiles"]
        if (args.register_only and set(existing) == {PROFILE} and existing[PROFILE].get("root") == str(resources)
                and all(existing[PROFILE].get(key) == value for key, value in metadata.items())):
            checker.verify_bundle(existing[PROFILE])
            verify_tokenizer_files(resources, args.tokenizer_revision)
            verify_tokenizer(resources, args.tokenizer_revision)
            print("Chunker tokenizer runtime is already registered and unchanged")
            return
        raise SystemExit("Refusing to modify an already published resource runtime; use a separate chunker environment")

    if args.register_only:
        if not descriptor.is_file():
            raise SystemExit("Prepared chunker tokenizer descriptor is missing")
        bundle = json.loads(descriptor.read_text(encoding="utf-8"))
    else:
        if resources.exists():
            raise SystemExit("Use a new chunker resource directory; never overwrite prepared assets")
        resources.parent.mkdir(parents=True, exist_ok=True)
        stage = resources.parent / (".chunker-resource-" + uuid4().hex)
        stage.mkdir()
        try:
            if args.offline_tokenizer_directory:
                source_root = args.offline_tokenizer_directory.resolve()
                reviewed = source_lock(root)
                snapshot = stage / "hf/hub/models--Qwen--Qwen3-32B/snapshots" / args.tokenizer_revision
                for name in reviewed["files"]:
                    original = source_root / name
                    if not original.is_file() or not original.resolve().is_relative_to(source_root):
                        raise ValueError(f"Reviewed offline chunker tokenizer is missing or unsafe: {name}")
                    target = snapshot / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, target)
                verify_tokenizer_files(stage, args.tokenizer_revision)
                refs = stage / "hf/hub/models--Qwen--Qwen3-32B/refs"
                refs.mkdir(parents=True, exist_ok=True)
                (refs / "main").write_text(args.tokenizer_revision, encoding="utf-8")
            else:
                provision(stage, args.tokenizer_revision, args.download_endpoint)
            verify_tokenizer(stage, args.tokenizer_revision)
            archive.publish_directory(stage, resources)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        bundle = None

    if bundle is None:
        bundle = {"root": str(resources), "digest": checker.bundle_digest(resources), **metadata}
    if (Path(bundle.get("root", "")).resolve() != resources or bundle.get("tokenizer_model") != MODEL
            or bundle.get("tokenizer_revision") != args.tokenizer_revision):
        raise SystemExit("Chunker tokenizer descriptor does not match the requested revision")
    checker.verify_bundle(bundle)
    verify_tokenizer_files(resources, args.tokenizer_revision)
    verify_tokenizer(resources, args.tokenizer_revision)
    if not args.register_only:
        descriptor.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    if args.download_only:
        print(f"Prepared immutable chunker tokenizer bundle: {bundle['digest']}")
        return

    subprocess.run([
        sys.executable, str(root / "scripts/register-operator-runtime.py"), "--output", str(args.manifest),
        "--dependency-lock", str(args.dependency_lock), "--package", "open-dataflow", "1.0.10", str(args.wheel),
    ], check=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    runtimes = manifest.get("runtimes", [manifest])
    current = next(item for item in runtimes if environment_path(item["python"]) == environment_path(sys.executable))
    profiles = dict(current.get("resource_profiles") or {})
    profiles[PROFILE] = bundle
    current["resource_profiles"] = profiles
    temporary = args.manifest.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(args.manifest)
    print(f"Registered immutable chunker tokenizer bundle: {bundle['digest']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - never expose download response details
        raise SystemExit(
            f"Chunker tokenizer preparation failed ({type(error).__name__}); no runtime resource profile was registered. "
            "Verify the pinned files or use --offline-tokenizer-directory."
        ) from None
