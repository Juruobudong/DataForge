"""Record an installed, maintainer-approved runtime. Never installs at runtime.

Run this with the isolated runtime's Python after installing the locked wheels.
Additional custom wheels are registered with --package NAME VERSION WHEEL.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys


def environment_path(value):
    # POSIX venv executables commonly symlink to the same base interpreter.
    # Resolving symlinks would collapse distinct, immutable environments.
    return os.path.normcase(os.path.abspath(value))


def installed_digest(dist):
    checksum = hashlib.sha256()
    for item in sorted(dist.files or [], key=str):
        if item.suffix != ".pyc" and "__pycache__" not in item.parts:
            checksum.update(str(item).replace("\\", "/").encode())
            checksum.update(dist.locate_file(item).read_bytes())
    return checksum.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--dependency-lock", required=True, help="Hash-locked requirements used to provision this environment")
    parser.add_argument("--package", nargs=3, action="append", required=True, metavar=("NAME", "VERSION", "WHEEL"))
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Operator runtime requires Python 3.12")
    packages = []
    for name, version, wheel in args.package:
        dist = importlib.metadata.distribution(name)
        if dist.version != version:
            raise SystemExit(f"Installed {name} version does not match")
        packages.append({"name": name, "version": version, "digest": hashlib.sha256(Path(wheel).read_bytes()).hexdigest(),
                         "installed_digest": installed_digest(dist)})
    environment = {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()}
    target = Path(args.output).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 1, "python": sys.executable, "packages": packages,
             "python_version": ".".join(map(str, sys.version_info[:3])), "dependencies": environment,
             "dependency_lock_digest": hashlib.sha256(Path(args.dependency_lock).read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()}
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        runtimes = previous.get("runtimes", [previous])
        runtimes = [entry for entry in runtimes if environment_path(entry["python"]) != environment_path(sys.executable)]
        if runtimes:
            value = {"schema_version": 2, "runtimes": [*runtimes, value]}
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(target)


if __name__ == "__main__":
    main()
