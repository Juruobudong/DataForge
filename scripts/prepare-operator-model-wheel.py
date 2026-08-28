"""Maintainer-only, hash-checked spaCy wheel prefetch and local lock projection."""
import argparse
import hashlib
import http.client
from pathlib import Path
import shutil
import tempfile
import time
import urllib.error
import urllib.request

FILENAME = "en_core_web_sm-3.7.1-py3-none-any.whl"
URL = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/" + FILENAME
SHA256 = "86cc141f63942d4b2c5fcee06630fd6f904788d2f0ab005cce45aadb8fb73889"


def verify(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != SHA256:
        raise ValueError(f"Model wheel SHA-256 mismatch: {path.name}; refusing to use or overwrite it")


def download(directory, *, timeout=120, attempts=4):
    """Only publish a complete verified file; never log signed redirect URLs."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / FILENAME
    for attempt in range(1, attempts + 1):
        partial = None
        try:
            print(f"Downloading {FILENAME} (attempt {attempt}/{attempts}, timeout {timeout}s)", flush=True)
            request = urllib.request.Request(URL, headers={"User-Agent": "DataForge-model-prefetch/1", "Cache-Control": "no-cache"})
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".spacy-", suffix=".part", delete=False) as output:
                partial = Path(output.name)
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    shutil.copyfileobj(response, output)
            verify(partial)
            partial.replace(target)
            print(f"Verified model wheel: {FILENAME}", flush=True)
            return target
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            # Exception text can include GitHub's temporary signed asset URL.
            print(f"Model download attempt {attempt}/{attempts} failed ({type(error).__name__})", flush=True)
            if attempt == attempts:
                raise RuntimeError("Model download failed. Prefetch on a connected machine and copy the verified wheel to runtime/dataflow/vendor/.") from None
            time.sleep(min(attempt * 2, 6))
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)


def prepare(wheel_dir, *, cache_dir=None, offline=False, timeout=120, attempts=4):
    wheel_dir = wheel_dir.resolve()
    target = wheel_dir / FILENAME
    if target.exists():
        verify(target)
        print(f"Reusing verified model wheel: {FILENAME}", flush=True)
        return target
    cache_dir = cache_dir.resolve() if cache_dir else wheel_dir
    cached = cache_dir / FILENAME
    if cached.exists():
        verify(cached)
    elif offline:
        raise FileNotFoundError("Verified model wheel is missing in offline mode")
    else:
        cached = download(cache_dir, timeout=timeout, attempts=attempts)
    if cached != target:
        wheel_dir.mkdir(parents=True, exist_ok=True)
        # Same filesystem atomic rename avoids exposing a partially copied wheel.
        with tempfile.NamedTemporaryFile(dir=wheel_dir, prefix=".spacy-", suffix=".part", delete=False) as output:
            partial = Path(output.name)
        try:
            shutil.copyfile(cached, partial)
            verify(partial)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    return target


def project_lock(lock, wheel):
    """Change only the fetch location, keeping version/hash and the source lock."""
    verify(wheel)
    entry = f"en-core-web-sm @ {URL} \\\n    --hash=sha256:{SHA256}"
    if lock.count(entry) != 1:
        raise ValueError("Dependency lock does not contain exactly the reviewed model URL and hash")
    return lock.replace(entry, f"en-core-web-sm @ {wheel.resolve().as_uri()} \\\n    --hash=sha256:{SHA256}", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, default=Path(__file__).resolve().parents[1] / "runtime/dataflow/vendor")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--dependency-lock", type=Path)
    parser.add_argument("--output-lock", type=Path)
    args = parser.parse_args()
    if args.timeout < 1 or not 1 <= args.attempts <= 10:
        parser.error("timeout must be positive; attempts must be 1–10")
    if bool(args.dependency_lock) != bool(args.output_lock):
        parser.error("--dependency-lock and --output-lock must be supplied together")
    if args.output_lock and args.output_lock.resolve() == args.dependency_lock.resolve():
        parser.error("Never overwrite the reviewed dependency lock")
    try:
        wheel = prepare(args.wheel_dir, cache_dir=args.cache_dir, offline=args.offline, timeout=args.timeout, attempts=args.attempts)
        if args.output_lock:
            projected = project_lock(args.dependency_lock.read_text(encoding="utf-8"), wheel)
            args.output_lock.write_text(projected, encoding="utf-8", newline="\n")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
