"""Private JSON-lines worker. Launched with an isolated Python, never by a shell.

Only this module imports third-party operators. Stdout is reserved for the
protocol; package output is captured and never interpreted as control messages.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.metadata
import inspect
import io
import json
import sys
from types import SimpleNamespace


def send(value):
    sys.__stdout__.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
    sys.__stdout__.flush()


def distribution_digest(name):
    dist = importlib.metadata.distribution(name)
    digest = hashlib.sha256()
    for file in sorted(dist.files or [], key=str):
        if file.suffix == ".pyc" or "__pycache__" in file.parts:
            continue
        path = dist.locate_file(file)
        digest.update(str(file).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_approved_class(implementation, package_name):
    module, _, name = implementation.partition(":") if ":" in implementation else implementation.rpartition(".")
    dist = importlib.metadata.distribution(package_name)
    files = list(dist.files or [])
    root = module.split(".")[0]
    if not any(str(path).replace("\\", "/").split("/")[0] in {root, root + ".py"} for path in files):
        raise ValueError("OPERATOR_IMPLEMENTATION_MISMATCH: module is not owned by the approved package")
    cls = getattr(importlib.import_module(module), name)
    path = inspect.getfile(cls)
    from pathlib import Path
    if Path(path).resolve() not in {dist.locate_file(file).resolve() for file in files}:
        raise ValueError("OPERATOR_IMPLEMENTATION_MISMATCH: class is not owned by the approved package")
    return cls


def main():
    request = json.loads(sys.stdin.readline())
    if request.get("python_version") and request["python_version"] != ".".join(map(str, sys.version_info[:3])):
        raise ValueError("OPERATOR_DEPENDENCY_DRIFT: Python version changed")
    for name, version in request.get("dependencies", {}).items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f"OPERATOR_DEPENDENCY_DRIFT: {name} version changed")
    package = request["package"]
    if importlib.metadata.version(package["name"]) != package["version"] or distribution_digest(package["name"]) != package["installed_digest"]:
        raise ValueError("OPERATOR_PACKAGE_DRIFT: installed implementation changed")
    if request.get("action") == "check":
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cls = load_approved_class(request["implementation"], package["name"])
        if not callable(cls):
            raise ValueError("Implementation is not callable")
        send({"type": "result", "outputs": [], "checked": True}); return

    calls_failed = []
    class Serving:
        def generate_from_input(self, user_inputs, system_prompt="", **kwargs):
            send({"type": "serving", "user_inputs": user_inputs, "system_prompt": system_prompt,
                  "json_schema": kwargs.get("json_schema")})
            reply = json.loads(sys.stdin.readline())
            if reply.get("error"):
                calls_failed.append(reply["error"])
                raise RuntimeError(reply["error"])
            values = reply["outputs"]
            if len(values) != len(user_inputs):
                raise ValueError("Serving result cardinality mismatch")
            return values

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cls = load_approved_class(request["implementation"], package["name"])
        init = dict(request.get("init") or {})
        if request.get("uses_llm") and request["executor"] != "custom-native":
            init["llm_serving"] = Serving()
        operator = cls(**init)
        if request["executor"] == "custom-native":
            ctx = SimpleNamespace(**request["context"])
            if request.get("uses_llm"):
                ctx.runtime["serving"] = Serving()
            result = operator.execute(inputs=request["records"], params=request["params"], context=ctx)
            outputs = result.get("outputs") if isinstance(result, dict) else result.outputs
        else:
            import pandas as pd
            from dataflow.utils.storage import DataFlowStorage
            class MemoryStorage(DataFlowStorage):
                def __init__(self, records):
                    self.frame = pd.DataFrame(records); self.written = False
                def get_keys_from_dataframe(self):
                    return list(self.frame.columns)
                def read(self, output_type="dataframe"):
                    if output_type != "dataframe":
                        raise ValueError("Only dataframe storage is supported")
                    return self.frame.copy(deep=True)
                def write(self, data):
                    self.frame = data.copy(deep=True); self.written = True
                    return "dataforge-memory"
            storage = MemoryStorage(request["records"])
            operator.run(storage=storage, **request.get("run_arguments", {}))
            if not storage.written or calls_failed:
                raise ValueError("OPERATOR_NO_OUTPUT: upstream failed or did not write storage")
            outputs = storage.frame.to_dict(orient="records")
        if calls_failed:
            raise ValueError("Upstream Serving failed")
        send({"type": "result", "outputs": outputs})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        send({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)
