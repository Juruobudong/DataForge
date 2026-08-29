"""Cohere-shaped reranking, with bounded retries and process-wide admission."""
from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx


class RerankerError(ValueError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str = "invalid_response"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class _Admission:
    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0
        self.limit = 4

    def acquire(self, limit: int, timeout: float):
        with self.condition:
            self.limit = limit
            if not self.condition.wait_for(lambda: self.active < self.limit, timeout=timeout):
                raise RerankerError("Reranker concurrency queue timeout")
            self.active += 1

    def release(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()


_GATES: dict[str, _Admission] = {}
_GATES_LOCK = threading.Lock()


@dataclass(frozen=True)
class RerankerServingConfig:
    id: str
    provider_type: str
    model_name: str
    base_url: str | None
    timeout_seconds: int
    max_retries: int
    max_batch_size: int
    max_concurrency: int


class RerankerServingRegistry:
    def __init__(self, manager, *, client_factory=httpx.Client, sleep=time.sleep):
        self.manager = manager
        self.client_factory = client_factory
        self.sleep = sleep

    def require(self, serving_code: str | None = None) -> RerankerServingConfig:
        value, _ = self.manager.resolved("reranker", serving_code)
        return self._config(value)

    @staticmethod
    def _config(value):
        return RerankerServingConfig(value.serving_code, value.provider_type, value.model_name,
                                     value.base_url, value.timeout_seconds, value.max_retries,
                                     value.max_batch_size, value.max_concurrency)

    @staticmethod
    def _validate(payload, size: int, model: str) -> list[dict]:
        if not isinstance(payload, dict) or payload.get("model", model) != model:
            raise RerankerError("Reranker 响应模型不匹配")
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != size:
            raise RerankerError("Reranker 响应缺少完整候选评分")
        seen, values = set(), []
        for result in results:
            if not isinstance(result, dict):
                raise RerankerError("Reranker 评分格式无效")
            index, score = result.get("index"), result.get("relevance_score")
            if type(index) is not int or index < 0 or index >= size or index in seen:
                raise RerankerError("Reranker 候选索引重复或越界")
            if type(score) not in (int, float) or not math.isfinite(score):
                raise RerankerError("Reranker 评分必须是有限数值")
            seen.add(index)
            values.append({"index": index, "relevance_score": float(score)})
        return values

    def rerank(self, serving_code: str, query: str, documents: list[str], *, expected_identity=None):
        try:
            value, credential = self.manager.resolved("reranker", serving_code)
        except ValueError:
            raise RerankerError("Reranker Serving 不存在、未启用、待配置或凭据不可用；请检查模型服务") from None
        config = self._config(value)
        if expected_identity and any(expected_identity.get(key) != getattr(config, key)
                                     for key in ("provider_type", "model_name")):
            raise RerankerError("Reranker 模型身份与所选 Routing 版本不匹配")
        if config.provider_type != "cohere-compatible-rerank":
            raise RerankerError("Reranker 协议不受支持")
        endpoint = str(config.base_url).rstrip("/")
        if not endpoint.endswith("/rerank"):
            endpoint += "/rerank"
        with _GATES_LOCK:
            gate = _GATES.setdefault(value.id, _Admission())
        started = time.monotonic()
        batches = [(offset, documents[offset:offset + config.max_batch_size])
                   for offset in range(0, len(documents), config.max_batch_size)]

        def run_batch(batch):
            offset, texts = batch
            gate.acquire(config.max_concurrency, config.timeout_seconds)
            try:
                headers = {} if credential in {"", "EMPTY", "fake"} else {"Authorization": f"Bearer {credential}"}
                with self.client_factory(timeout=config.timeout_seconds, follow_redirects=False) as client:
                    for attempt in range(config.max_retries + 1):
                        retry = False
                        try:
                            response = client.post(endpoint, headers=headers, json={
                                "model": config.model_name, "query": query,
                                "documents": texts, "top_n": len(texts),
                            })
                            status = response.status_code
                            if status == 429 or 500 <= status <= 599:
                                error = RerankerError(f"Reranker HTTP {status}", status_code=status)
                                retry = True
                            elif not 200 <= status < 300:
                                raise RerankerError(f"Reranker HTTP {status}", status_code=status)
                            else:
                                try:
                                    payload = response.json()
                                except (ValueError, UnicodeError):
                                    raise RerankerError("Reranker 响应不是合法 JSON") from None
                                return [{**item, "index": item["index"] + offset}
                                        for item in self._validate(payload, len(texts), config.model_name)]
                        except httpx.TimeoutException:
                            error, retry = RerankerError("Reranker request timeout"), True
                        except httpx.TransportError:
                            error, retry = RerankerError("Reranker connection error", code="connection_error"), True
                        if retry:
                            if attempt == config.max_retries:
                                raise error
                            self.sleep(min(0.25 * 2 ** min(attempt, 5), 2.0))
            finally:
                gate.release()

        if batches:
            with ThreadPoolExecutor(max_workers=min(config.max_concurrency, len(batches))) as executor:
                scores = [item for batch in executor.map(run_batch, batches) for item in batch]
        else:
            scores = []
        scores.sort(key=lambda item: (-item["relevance_score"], item["index"]))
        return {"results": scores, "batch_count": len(batches), "model_name": config.model_name,
                "serving_code": config.id, "latency_ms": round((time.monotonic() - started) * 1000)}
