"""Bounded, in-memory operator diagnostics. Stdlib-only for the isolated worker.

Raw fragments are joined before redaction (including split credentials), never
persisted. Each node owns one collector across all its package invocations.
"""
from __future__ import annotations

import re
import threading
from urllib.parse import quote

STREAM_LIMIT = 32 * 1024
ERROR_LIMIT = 2048
_SECRET_KEY = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization|credential", re.I)
_FIELD = re.compile(
    r'''(?i)(?<![\w.-])(["']?[\w.-]*(?:password|passwd|secret|token|api[_-]?key|authorization|credential)[\w.-]*["']?\s*[:=]\s*)'''
    r'''(?:\[redacted\]|"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s,;&}\]]+)'''
)
_AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+[^\s\"',;}\]]+")
_URL_CREDENTIAL = re.compile(r"(?i)(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*://)[^\s/@]+@")


def utf8_prefix(value: str, limit: int) -> str:
    # Slice characters first so a huge package write cannot allocate huge bytes.
    return value[:limit].encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def safe_prefix(value: str, limit: int) -> str:
    prefix = utf8_prefix(value, limit)
    if prefix != value:
        for marker in ("[redacted]", "[redacted-url]"):
            if any(prefix.endswith(marker[:size]) for size in range(1, len(marker))):
                return utf8_prefix(value, limit - len(marker)) + marker
    return prefix


def redact(value: str, secrets=(), *, truncated=False) -> str:
    value = str(value)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if not secret:
            continue
        for encoded in {secret, quote(secret, safe="")}:
            value = value.replace(encoded, "[redacted]")
            # A killed process or full buffer can end halfway through a secret.
            if truncated:
                for size in range(min(len(value), len(encoded) - 1), 0, -1):
                    if value.endswith(encoded[:size]):
                        value = value[:-size] + "[redacted]"
                        break
    value = _AUTH.sub("[redacted]", value)
    value = _FIELD.sub(lambda match: match[1] + "[redacted]", value)
    value = _URL_CREDENTIAL.sub(r"\1[redacted]@", value)
    if truncated:
        value = re.sub(r"(?i)(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://[^/\s]*:[^/\s]*$", "[redacted-url]", value)
    return value


class OperatorDiagnostics:
    def __init__(self, secrets=()):
        self._lock = threading.RLock()
        self._text = {"stdout": "", "stderr": ""}
        self._size = {"stdout": 0, "stderr": 0}
        self._truncated = {"stdout": False, "stderr": False}
        self._secrets = set(secrets)

    def add_secrets(self, value):
        with self._lock:
            if isinstance(value, dict):
                for key, item in value.items():
                    if _SECRET_KEY.search(str(key)) and isinstance(item, str) and item:
                        self._secrets.add(item)
                    elif isinstance(item, (dict, list, tuple)):
                        self.add_secrets(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    self.add_secrets(item)

    def append(self, stream, message, *, truncated=False):
        """Return only the admitted fragment and whether this stream is full."""
        if stream not in self._text:
            raise ValueError("Unknown operator log stream")
        message = str(message)
        with self._lock:
            if self._truncated[stream]:
                return "", True
            remaining = STREAM_LIMIT - self._size[stream]
            fragment = utf8_prefix(message, remaining)
            self._text[stream] += fragment
            self._size[stream] += len(fragment.encode("utf-8"))
            self._truncated[stream] |= truncated or len(fragment) < len(message)
            return fragment, self._truncated[stream]

    def extend(self, logs):
        for item in logs or []:
            self.append(item.get("stream", "stderr"), item.get("message", ""), truncated=bool(item.get("truncated")))

    def snapshot(self):
        with self._lock:
            logs = []
            for stream, value in self._text.items():
                if not value and not self._truncated[stream]:
                    continue
                # Treat even a short EOF as potentially ending mid-credential.
                safe = redact(value, self._secrets, truncated=True)
                message = safe_prefix(safe, STREAM_LIMIT)
                logs.append({"stream": stream, "message": message,
                             "truncated": self._truncated[stream] or message != safe})
            return logs

    def error(self, exc):
        message = str(exc)
        with self._lock:
            safe = redact(utf8_prefix(message, STREAM_LIMIT), self._secrets, truncated=True)
        return safe_prefix(safe, ERROR_LIMIT)


class OperatorExecutionError(ValueError):
    def __init__(self, error, diagnostics):
        super().__init__(diagnostics.error(error))
        self.operator_logs = diagnostics.snapshot()
        self.operator_metrics = getattr(error, "operator_metrics", {})


def capture_operator_diagnostics(execute):
    """One collector per execute, including errors after package field mapping."""
    from dataclasses import replace
    from functools import wraps

    @wraps(execute)
    def wrapped(self, *, inputs, params, context):
        diagnostics = OperatorDiagnostics()
        diagnostics.add_secrets(params)
        # Generation outcomes are an existing mutable Runner contract, even
        # when the caller has not initialized the dict yet.
        context.runtime.setdefault("generation", {})
        context = replace(context, runtime={**context.runtime, "_operator_diagnostics": diagnostics})
        try:
            result = execute(self, inputs=inputs, params=params, context=context)
        except Exception as exc:
            diagnostics.append("stderr", diagnostics.error(exc) + "\n")
            raise OperatorExecutionError(exc, diagnostics) from None
        diagnostics.extend(result.logs)
        result.logs = diagnostics.snapshot()
        return result
    return wrapped
