"""Maintainer-only provisioning of the curated CPU environment, never a web action."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Activate a Python 3.12 environment before provisioning")
    if args.environment.exists():
        raise SystemExit("Use a new environment directory; never upgrade a published runtime in place")
    root = Path(__file__).resolve().parents[1]
    upstream = (root / "runtime/dataflow/upstream.lock").read_text(encoding="utf-8")
    match = re.search(r"open-dataflow==([\d.]+) --hash=sha256:([a-f0-9]{64})", upstream)
    if not match or hashlib.sha256(args.wheel.read_bytes()).hexdigest() != match[2]:
        raise SystemExit("Upstream wheel checksum does not match the reviewed lock")
    lock = root / "runtime/dataflow/requirements.lock"
    subprocess.run([args.uv, "venv", "--python", sys.executable, str(args.environment)], check=True)
    python = args.environment.resolve() / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([args.uv, "pip", "install", "--python", str(python), "--require-hashes", "-r", str(lock)], check=True)
    subprocess.run([args.uv, "pip", "install", "--python", str(python), "--no-deps", str(args.wheel.resolve())], check=True)
    subprocess.run([str(python), str(root / "scripts/register-operator-runtime.py"), "--output", str(args.manifest),
                    "--dependency-lock", str(lock), "--package", "open-dataflow", match[1], str(args.wheel.resolve())], check=True)


if __name__ == "__main__":
    main()
