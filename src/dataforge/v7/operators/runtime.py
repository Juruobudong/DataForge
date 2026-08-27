"""Maintainer-provisioned package environments and bounded subprocess calls."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def runtime_fingerprint(value):
    return digest({"dependencies": value.get("dependencies", {}), "packages": value.get("packages", []),
                   "python_version": value.get("python_version", "3.12"), "dependency_lock_digest": value.get("dependency_lock_digest")})


class OperatorRuntime:
    def __init__(self, manifest_path=None):
        self.path = Path(manifest_path or os.getenv("DATAFORGE_OPERATOR_RUNTIME_MANIFEST") or
                         Path(os.getenv("DATAFORGE_STATE_DIR", ".dataforge")) / "operator-runtime.json")

    def resolve(self, spec):
        if not self.path.is_file():
            raise ValueError("OPERATOR_DEPENDENCY_MISSING: 维护人员尚未安装算子运行环境")
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        match = next(((value, item) for value in manifest.get("runtimes", [manifest]) for item in value.get("packages", [])
                      if item["name"] == spec.get("package") and item["version"] == spec.get("package_version")
                      and item["digest"] == spec.get("package_digest")
                      and (not spec.get("dependency_lock_digest") or spec["dependency_lock_digest"] == value.get("dependency_lock_digest"))
                      and spec.get("environment_digest", runtime_fingerprint(value)) == runtime_fingerprint(value)), None)
        if match is None:
            raise ValueError("OPERATOR_PACKAGE_DRIFT: 包版本或摘要与冻结版本不一致")
        value, package = match
        python = Path(value["python"])
        if not python.is_file():
            raise ValueError("OPERATOR_DEPENDENCY_MISSING: 算子 Python 不存在")
        return value, package

    def status(self, spec):
        if spec.get("executor", "dataforge-native") in {"dataforge-native", "dataforge-adapter"}:
            return {"status": "ready"}
        try:
            runtime, _ = self.resolve(spec)
            return {"status": "ready", "runtime_digest": runtime_fingerprint(runtime)}
        except (ValueError, OSError, KeyError) as exc:
            return {"status": "missing", "reason": str(exc)}

    def call(self, spec, *, records, init=None, run_arguments=None, context=None, params=None,
             serving=None, cancelled=None, timeout=None, action="execute", implementation=None):
        runtime, package = self.resolve(spec)
        implementation = implementation or spec["implementation"]
        allowed = {spec["implementation"], *(spec.get("implementations") or {}).values()}
        if implementation not in allowed:
            raise ValueError("算子实现不在已批准白名单")
        request = {"package": package, "implementation": implementation, "executor": spec["executor"],
                   "uses_llm": bool(spec.get("uses_llm")), "records": records, "init": init or {},
                   "run_arguments": run_arguments or {}, "context": context or {}, "params": params or {}, "action": action,
                   "dependencies": runtime.get("dependencies", {}), "python_version": runtime.get("python_version")}
        environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP") if key in os.environ}
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        messages = queue.Queue()
        deadline = time.monotonic() + (timeout if timeout is not None else spec.get("timeout_seconds", 300))
        with tempfile.TemporaryDirectory(prefix="dataforge-operator-") as cwd:
            process = subprocess.Popen([runtime["python"], "-I", str(Path(__file__).with_name("process_worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                cwd=cwd, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def read():
                try:
                    for line in process.stdout:
                        messages.put(json.loads(line))
                except Exception as exc:
                    messages.put({"type": "error", "message": str(exc)})
                finally:
                    messages.put({"type": "eof"})
            thread = threading.Thread(target=read, daemon=True); thread.start()
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            def check():
                if cancelled and cancelled():
                    raise ValueError("OPERATOR_CANCELLED: 算子已取消")
                if time.monotonic() >= deadline:
                    raise ValueError("OPERATOR_TIMEOUT: 算子执行超时")
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n"); process.stdin.flush()
                while True:
                    check()
                    try:
                        message = messages.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if message["type"] == "serving":
                        if not serving or not spec.get("uses_llm"):
                            raise ValueError("算子未获准使用模型服务")
                        future = executor.submit(serving, message)
                        while not future.done():
                            check(); time.sleep(0.05)
                        outputs = future.result()
                        process.stdin.write(json.dumps({"outputs": outputs}, ensure_ascii=True) + "\n"); process.stdin.flush()
                    elif message["type"] == "result":
                        check()
                        if not isinstance(message.get("outputs"), list):
                            raise ValueError("算子必须返回记录数组")
                        return message
                    elif message["type"] == "error":
                        raise ValueError(message.get("message", "算子执行失败"))
                    else:
                        raise ValueError("算子进程未返回有效结果")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdin.close(); process.stdout.close()
                executor.shutdown(wait=False, cancel_futures=True)
