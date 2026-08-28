"""Provision immutable English NLP assets in a new runtime; never called by a Run."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

NER_REVISION = "d1a3e8f13f8c3566299d95fcfc9a8d2382a9affc"
NLTK_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"


def environment_path(value):
    return os.path.normcase(os.path.abspath(value))


def provision(resources):
    resources.mkdir(parents=True, exist_ok=True)
    nltk_root = resources / "nltk"
    nltk_root.mkdir(exist_ok=True)
    os.environ["NLTK_DATA"] = str(nltk_root)
    os.environ["HF_HOME"] = str(resources / "hf")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    for name in ("punkt", "punkt_tab"):
        archive = nltk_root / (name + ".zip")
        url = f"https://raw.githubusercontent.com/nltk/nltk_data/{NLTK_REVISION}/packages/tokenizers/{name}.zip"
        print(f"Provisioning NLTK {name} at {NLTK_REVISION}", flush=True)
        with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as output:
            while block := response.read(1024 * 1024): output.write(block)
        target = nltk_root / "tokenizers"
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                if not (target / entry.filename).resolve().is_relative_to(target.resolve()):
                    raise ValueError("Unsafe NLTK archive path")
            package.extractall(target)
    from huggingface_hub import snapshot_download
    print(f"Provisioning English NER at {NER_REVISION}", flush=True)
    snapshot_download("dslim/bert-base-NER", revision=NER_REVISION, cache_dir=str(resources / "hf" / "hub"),
                      allow_patterns=["*.json", "vocab.txt", "model.safetensors"], max_workers=2)
    # Upstream asks for the main revision. Resolve it locally to the pinned
    # snapshot so both explicit and engine-internal loads remain offline.
    refs = resources / "hf" / "hub" / "models--dslim--bert-base-NER" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(NER_REVISION, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--offline-bundle", type=Path, help="Import a complete reviewed NLP archive without network access")
    parser.add_argument("--resource-lock", type=Path, help="Reviewed portable archive/content hashes")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download-only", action="store_true", help="Prepare assets without registering a runtime")
    mode.add_argument("--register-only", action="store_true", help="Verify a prepared bundle and register without networking")
    args = parser.parse_args()
    if bool(args.offline_bundle) != bool(args.resource_lock) or (args.register_only and args.offline_bundle):
        parser.error("Supply --offline-bundle and --resource-lock together, before offline registration")
    root = Path(__file__).resolve().parents[1]
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use the isolated Python 3.12 runtime")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.exists() else {"runtimes": []}
    runtimes = manifest.get("runtimes", [manifest])
    if any(environment_path(item["python"]) == environment_path(sys.executable) and item.get("resource_profiles") for item in runtimes):
        raise SystemExit("Refusing to modify an already published resource runtime")
    spec = importlib.util.spec_from_file_location("resource_bundle", root / "src/dataforge/v7/operators/resource_bundle.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    resources = args.resources.resolve()
    existing = next((bundle for runtime in runtimes for bundle in runtime.get("resource_profiles", {}).values()
                     if Path(bundle["root"]).resolve() == resources), None)
    descriptor = resources.with_suffix(".bundle.json")
    if args.register_only:
        if not descriptor.is_file():
            raise SystemExit("Prepared resource descriptor is missing")
        existing = json.loads(descriptor.read_text(encoding="utf-8"))
        if Path(existing["root"]).resolve() != resources:
            raise SystemExit("Prepared resource root does not match")
    if existing:
        module.verify_bundle(existing)
        if existing.get("ner_revision") != NER_REVISION or existing.get("nltk_revision") != NLTK_REVISION:
            raise SystemExit("Use a new resource directory for different pinned assets")
        print("Reusing verified immutable resources without modifying them", flush=True)
    elif args.offline_bundle:
        archive_spec = importlib.util.spec_from_file_location("operator_resource_archive", root / "scripts/operator-resource-bundle.py")
        archive_module = importlib.util.module_from_spec(archive_spec); archive_spec.loader.exec_module(archive_module)
        try:
            archive_module.import_bundle(args.offline_bundle, args.resource_lock, resources,
                                         ner_revision=NER_REVISION, nltk_revision=NLTK_REVISION)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            raise SystemExit(f"Offline NLP resources unavailable: {error}") from None
        print("Imported verified complete NLP resources without network access", flush=True)
    else:
        provision(resources)
    import en_core_web_sm
    assert en_core_web_sm.load().meta["version"] == "3.7.1"
    digest = module.bundle_digest(resources)
    bundle = {"root": str(resources), "digest": digest, "ner_revision": NER_REVISION, "nltk_revision": NLTK_REVISION}
    if not args.register_only:
        descriptor.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    if args.download_only:
        print(f"Prepared immutable resource bundle: {digest}", flush=True)
        return
    subprocess.run([sys.executable, str(root / "scripts/register-operator-runtime.py"), "--output", str(args.manifest),
                    "--dependency-lock", str(args.dependency_lock), "--package", "open-dataflow", "1.0.10", str(args.wheel)], check=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    current = next(item for item in manifest.get("runtimes", [manifest]) if environment_path(item["python"]) == environment_path(sys.executable))
    current["resource_profiles"] = {"pii-en-v1": bundle, "nltk-v1": bundle}
    temp = args.manifest.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temp.replace(args.manifest)
    print(f"Registered immutable resource bundle: {digest}", flush=True)


if __name__ == "__main__":
    main()
