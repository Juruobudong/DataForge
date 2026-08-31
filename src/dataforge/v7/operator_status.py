"""Bounded, advisory runtime status caching; never used to authorize execution."""
from collections import OrderedDict
from copy import deepcopy
import json
import threading
import time

from .operator_runtime_contract import validate_runtime_requirements, requires_external_runtime


def profile_key(spec):
    validate_runtime_requirements(spec)
    return json.dumps({key: spec.get(key) for key in (
        "driver", "executor", "package", "package_version", "package_digest",
        "environment_digest", "dependency_lock_digest", "resource_profile",
    )}, sort_keys=True, separators=(",", ":"))


def unavailable():
    return {"status": "unknown", "reason": "Runner 依赖状态不可达，请刷新重试"}


class RuntimeStatusCache:
    def __init__(self, *, capacity=1024, clock=time.monotonic):
        self.capacity, self.clock = capacity, clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()

    def _read(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value and value[0] > self.clock():
                self.entries.move_to_end(key)
                return deepcopy(value[1])
            self.entries.pop(key, None)
        return None

    def get_many(self, namespace, requirements, fetch):
        keys = [(namespace, profile_key(spec)) for spec in requirements]
        external = {key: spec for key, spec in zip(keys, requirements) if requires_external_runtime(spec)}
        values = {key: self._read(key) for key in external}
        if any(value is None for value in values.values()):
            # One batch in flight per process; followers reuse its results.
            acquired = self.refresh_lock.acquire(timeout=2)
            try:
                values = {key: self._read(key) for key in external}
                missing = [key for key, value in values.items() if value is None]
                if acquired and missing:
                    try:
                        statuses = fetch([external[key] for key in missing])
                        if not isinstance(statuses, list) or len(statuses) != len(missing):
                            raise ValueError("Invalid runtime status batch")
                        if any(not isinstance(s, dict) or s.get("status") not in {
                            "ready", "missing", "unknown", "incompatible",
                        } for s in statuses):
                            raise ValueError("Invalid runtime status")
                    except Exception:
                        statuses = [unavailable() for _ in missing]
                    with self.lock:
                        for key, status in zip(missing, statuses):
                            ttl = 3 if status["status"] == "unknown" else 20
                            self.entries[key] = (self.clock() + ttl, deepcopy(status))
                            self.entries.move_to_end(key)
                            values[key] = status
                        while len(self.entries) > self.capacity:
                            self.entries.popitem(last=False)
            finally:
                if acquired:
                    self.refresh_lock.release()
        return [deepcopy(values.get(key) or unavailable()) if key in external else {"status": "ready"}
                for key in keys]


API_STATUS_CACHE = RuntimeStatusCache()
RUNNER_STATUS_CACHE = RuntimeStatusCache()
