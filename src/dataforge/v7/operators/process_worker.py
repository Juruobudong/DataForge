"""Private JSON-lines worker. Launched with an isolated Python, never by a shell.

Only this module imports third-party operators. Stdout is reserved for the
protocol; package output is captured and never interpreted as control messages.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .diagnostics import OperatorDiagnostics
else:
    # -I intentionally excludes the script directory from sys.path. Load only
    # our adjacent stdlib-only helper, never add package-search directories.
    spec = importlib.util.spec_from_file_location("operator_diagnostics", Path(__file__).with_name("diagnostics.py"))
    diagnostics_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostics_module)
    OperatorDiagnostics = diagnostics_module.OperatorDiagnostics

_PROTOCOL_LOCK = threading.RLock()


def send(value):
    with _PROTOCOL_LOCK:
        sys.__stdout__.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
        sys.__stdout__.flush()


class LogWriter(io.TextIOBase):
    def __init__(self, stream, diagnostics):
        self.stream, self.diagnostics = stream, diagnostics
        self.notified_truncation = False

    @property
    def encoding(self):
        return "utf-8"

    def write(self, value):
        with _PROTOCOL_LOCK:
            fragment, truncated = self.diagnostics.append(self.stream, value)
            for offset in range(0, len(fragment), 4096):
                send({"type": "operator_log", "stream": self.stream, "message": fragment[offset:offset + 4096]})
            if truncated and not self.notified_truncation:
                send({"type": "operator_log", "stream": self.stream, "message": "", "truncated": True})
                self.notified_truncation = True
        return len(value)


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


def execute(request):
    governance = None
    if str(request.get("adapter_version", "")).startswith("governance-"):
        spec = importlib.util.spec_from_file_location("governance_worker", Path(__file__).with_name("governance_worker.py"))
        governance = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(governance)
        governance.lock_network()
    if request.get("python_version") and request["python_version"] != ".".join(map(str, sys.version_info[:3])):
        raise ValueError("OPERATOR_DEPENDENCY_DRIFT: Python version changed")
    for name, version in request.get("dependencies", {}).items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f"OPERATOR_DEPENDENCY_DRIFT: {name} version changed")
    package = request["package"]
    if importlib.metadata.version(package["name"]) != package["version"] or distribution_digest(package["name"]) != package["installed_digest"]:
        raise ValueError("OPERATOR_PACKAGE_DRIFT: installed implementation changed")
    if request.get("action") == "check":
        cls = load_approved_class(request["implementation"], package["name"])
        if not callable(cls):
            raise ValueError("Implementation is not callable")
        return {"outputs": [], "checked": True}

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

    cls = load_approved_class(request["implementation"], package["name"])
    serving = Serving() if request.get("uses_llm") and request["executor"] != "custom-native" else None
    if governance:
        special = governance.execute_special(request, cls, serving)
        if special is not None:
            return {"outputs": special}
    init = dict(request.get("init") or {})
    if governance:
        init = governance.prepare_init(request, init)
    if serving is not None:
        init["llm_serving"] = serving
    operator = cls(**init)
    if governance:
        governance.configure_operator(request, operator)
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
    return {"outputs": outputs}


def main():
    diagnostics = OperatorDiagnostics()
    sensitive = False
    try:
        request = json.loads(sys.stdin.readline())
        sensitive = bool(request.get("sensitive"))
        diagnostics.add_secrets({"init": request.get("init"), "params": request.get("params"), "context": request.get("context")})
        class DiscardWriter(io.TextIOBase):
            def write(self, value): return len(value)
        with contextlib.redirect_stdout(DiscardWriter() if sensitive else LogWriter("stdout", diagnostics)), contextlib.redirect_stderr(DiscardWriter() if sensitive else LogWriter("stderr", diagnostics)):
            result = execute(request)
        send({"type": "result", "ok": True, "error": None, "logs_streamed": True,
              "operator_logs": diagnostics.snapshot(), **result})
        return 0
    except Exception as exc:
        message = "PII_EXECUTION_FAILED: 英文PII模型执行失败，请检查受控环境与资源" if sensitive else diagnostics.error(f"{type(exc).__name__}: {exc}")
        send({"type": "error", "ok": False, "message": message, "error": message,
              "logs_streamed": True, "operator_logs": diagnostics.snapshot()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
