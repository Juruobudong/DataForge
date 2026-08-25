"""Persistent LLM and embedding serving management and runtime registries."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
from openai import OpenAI
from sqlalchemy import delete, select

from .secret_codec import ConfigCipher
from .models import (
    EmbeddingServing,
    FlowExecutionSnapshot,
    KnowledgeAssetVersion,
    KnowledgeFlowTemplate,
    KnowledgeFlowTemplateRevision,
    KnowledgeIndexProfile,
    KnowledgeIndexProfileRevision,
    ModelServing,
    utc_now,
)


DEFAULT_LLM_SERVING_ID = "qwen3_32b"
DEFAULT_EMBEDDING_SERVING_ID = "bce_base_768"
SERVING_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SUPPORTED_LLM_TYPES = {"openai-compatible-chat"}
SUPPORTED_EMBEDDING_TYPES = {"openai-compatible-embedding"}
SERVING_CATEGORIES: tuple[str, ...] = ("llm", "embedding", "reranker", "ocr-vision")
AVAILABLE_SERVING_CATEGORIES: frozenset[str] = frozenset({"llm", "embedding"})
PLACEHOLDER_KEYS = {"", "EMPTY", "fake"}
LOGGER = logging.getLogger(__name__)


def _contains_llm_serving(value: Any, serving_code: str) -> bool:
    if isinstance(value, dict):
        return any(
            key == "llm_serving" and child == serving_code
            or _contains_llm_serving(child, serving_code)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_llm_serving(child, serving_code) for child in value)
    return False


def _safe_error(exc: Exception) -> str:
    value = str(exc).replace("\n", " ").strip()
    return value[:500] or type(exc).__name__


def _classify_error(exc: Exception) -> str:
    if "待配置" in str(exc):
        return "pending_configuration"
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return "authentication_failed"
    if status == 404:
        return "model_not_found"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "timeout" in str(exc).lower():
        return "timeout"
    if isinstance(exc, (ConnectionRefusedError, httpx.ConnectError)):
        return "connection_refused" if "refused" in str(exc).lower() else "connection_error"
    return "invalid_response"


@dataclass(frozen=True)
class LLMServingConfig:
    id: str
    type: str
    model_name: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    max_tokens: int
    disable_thinking: bool


@dataclass(frozen=True)
class EmbeddingServingConfig:
    id: str
    provider_type: str
    model_name: str
    base_url: str
    dimension: int
    batch_size: int
    timeout_seconds: float
    max_retries: int


class ServingManager:
    def __init__(self, sessions, encryption_key: str | None, *, client_factory: Callable[..., Any] = OpenAI):
        self.sessions = sessions
        self.cipher = ConfigCipher(encryption_key)
        self.client_factory = client_factory

    @staticmethod
    def _model(kind: str):
        if kind == "model":
            return ModelServing
        if kind == "embedding":
            return EmbeddingServing
        raise ValueError("Serving 类型无效")

    @staticmethod
    def _aad(kind: str, serving_id: str) -> str:
        return f"dataforge:{kind}-serving:{serving_id}:v1"

    @staticmethod
    def _validate_base_url(value: str | None) -> str | None:
        value = str(value or "").strip().rstrip("/")
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是 http(s) URL")
        if parsed.username or parsed.password or ({key.lower() for key in parse_qs(parsed.query)} & {"api_key", "token", "secret", "password"}):
            raise ValueError("Base URL 禁止内嵌凭据")
        return value

    @staticmethod
    def _validate_common(payload: dict[str, Any]) -> None:
        code = str(payload.get("serving_code") or "").strip()
        if not SERVING_CODE_PATTERN.fullmatch(code):
            raise ValueError("Serving ID 必须是小写字母开头的 1-64 位字母、数字、下划线或连字符")
        if not str(payload.get("name") or "").strip() or not str(payload.get("model_name") or "").strip():
            raise ValueError("名称和 Model Name 不能为空")
        for field in ("timeout_seconds",):
            if int(payload.get(field, 0)) <= 0:
                raise ValueError(f"{field} 必须为正整数")
        if int(payload.get("max_retries", -1)) < 0:
            raise ValueError("max_retries 必须为非负整数")

    @staticmethod
    def _payload(value: ModelServing | EmbeddingServing) -> dict[str, Any]:
        result = {
            "id": value.id, "serving_code": value.serving_code, "name": value.name,
            "category": "llm" if isinstance(value, ModelServing) else "embedding",
            "model_name": value.model_name, "base_url": value.base_url,
            "credential_configured": bool(value.credential_configured),
            "timeout_seconds": value.timeout_seconds, "max_retries": value.max_retries,
            "is_enabled": value.is_enabled, "is_default": value.is_default,
            "last_check_status": value.last_check_status,
            "last_check_at": value.last_check_at.isoformat() if value.last_check_at else None,
            "last_check_latency_ms": value.last_check_latency_ms,
            "last_check_error": value.last_check_error,
            "created_at": value.created_at.isoformat(), "updated_at": value.updated_at.isoformat(),
        }
        if isinstance(value, ModelServing):
            result.update({"serving_type": value.serving_type, "max_tokens": value.max_tokens,
                           "disable_thinking": value.disable_thinking})
        else:
            result.update({"provider_type": value.provider_type, "dimension": value.dimension,
                           "batch_size": value.batch_size,
                           "last_observed_dimension": value.last_observed_dimension})
        return result

    @staticmethod
    def categories() -> list[dict[str, Any]]:
        """Return the stable serving categories with availability for frontend tabs."""
        return [{"key": key, "available": key in AVAILABLE_SERVING_CATEGORIES} for key in SERVING_CATEGORIES]

    def list(self, kind: str) -> list[dict[str, Any]]:
        model = self._model(kind)
        with self.sessions() as session:
            return [self._payload(item) for item in session.scalars(
                select(model).order_by(model.is_default.desc(), model.name, model.serving_code)
            )]

    def get(self, kind: str, serving_id: str) -> dict[str, Any]:
        model = self._model(kind)
        with self.sessions() as session:
            value = session.get(model, serving_id)
            if not value:
                raise ValueError("Serving 不存在")
            return self._payload(value)

    def create(self, kind: str, serving_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._model(kind)
        payload = dict(payload)
        self._validate_common(payload)
        provider_type = payload.get("serving_type") if kind == "model" else payload.get("provider_type")
        supported = SUPPORTED_LLM_TYPES if kind == "model" else SUPPORTED_EMBEDDING_TYPES
        if provider_type not in supported:
            raise ValueError("Serving 协议不受支持")
        if kind == "model" and int(payload.get("max_tokens", 0)) <= 0:
            raise ValueError("max_tokens 必须为正整数")
        if kind == "embedding" and (int(payload.get("dimension", 0)) <= 0 or int(payload.get("batch_size", 0)) <= 0):
            raise ValueError("dimension 和 batch_size 必须为正整数")
        base_url = self._validate_base_url(payload.get("base_url"))
        api_key = str(payload.pop("api_key", "") or "").strip()
        with self.sessions.begin() as session:
            if session.scalar(select(model.id).where(model.serving_code == payload["serving_code"].strip())):
                raise ValueError("Serving ID 已存在")
            values = {
                "id": serving_id, "serving_code": payload["serving_code"].strip(), "name": payload["name"].strip(),
                "model_name": payload["model_name"].strip(), "base_url": base_url,
                "timeout_seconds": int(payload["timeout_seconds"]), "max_retries": int(payload["max_retries"]),
                "is_enabled": bool(payload.get("is_enabled", True)), "is_default": False,
                "last_check_status": "not_checked" if base_url else "pending_configuration",
            }
            if kind == "model":
                values.update({"serving_type": provider_type, "max_tokens": int(payload["max_tokens"]),
                               "disable_thinking": bool(payload.get("disable_thinking", True))})
            else:
                values.update({"provider_type": provider_type, "dimension": int(payload["dimension"]),
                               "batch_size": int(payload["batch_size"])})
            value = model(**values)
            if api_key and api_key not in PLACEHOLDER_KEYS:
                value.credential_ciphertext = self.cipher.encrypt(api_key, self._aad(kind, value.id))
                value.credential_key_version = self.cipher.key_version
                value.credential_configured = True
            session.add(value); session.flush()
            return self._payload(value)

    def update(self, kind: str, serving_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        model = self._model(kind)
        changes = {key: value for key, value in changes.items() if value is not None}
        changes.pop("serving_code", None)
        api_key = str(changes.pop("api_key", "") or "").strip()
        clear_credential = bool(changes.pop("clear_credential", False))
        with self.sessions.begin() as session:
            value = session.get(model, serving_id, with_for_update=True)
            if not value:
                raise ValueError("Serving 不存在")
            if changes.get("is_enabled") is False and value.is_default:
                raise ValueError("默认 Serving 不能停用，请先设置其他默认 Serving")
            if kind == "embedding" and {"provider_type", "model_name", "dimension"} & set(changes):
                published = session.scalar(select(KnowledgeIndexProfileRevision.id).where(
                    KnowledgeIndexProfileRevision.embedding_serving_id == value.serving_code,
                    KnowledgeIndexProfileRevision.status == "published",
                ))
                asset = session.scalar(select(KnowledgeAssetVersion.id).where(
                    KnowledgeAssetVersion.embedding_serving_id == value.serving_code,
                ))
                if published or asset:
                    raise ValueError("Embedding Serving 已被正式 Profile 或 Asset 引用，不能原地修改协议、模型或维度")
            connection_fields = {"base_url", "model_name", "serving_type", "provider_type", "dimension",
                                 "timeout_seconds", "max_retries", "max_tokens", "disable_thinking", "batch_size"}
            changed_connection = bool(connection_fields & set(changes)) or bool(api_key) or clear_credential
            for key, raw in changes.items():
                if key == "base_url":
                    raw = self._validate_base_url(raw)
                if key in {"timeout_seconds", "max_tokens", "dimension", "batch_size"} and int(raw) <= 0:
                    raise ValueError(f"{key} 必须为正整数")
                if key == "max_retries" and int(raw) < 0:
                    raise ValueError("max_retries 必须为非负整数")
                if hasattr(value, key):
                    setattr(value, key, raw.strip() if isinstance(raw, str) else raw)
            if clear_credential:
                value.credential_ciphertext = value.credential_key_version = None
                value.credential_configured = False
            elif api_key:
                value.credential_ciphertext = self.cipher.encrypt(api_key, self._aad(kind, value.id))
                value.credential_key_version = self.cipher.key_version
                value.credential_configured = True
            if changed_connection:
                value.last_check_status = "not_checked" if value.base_url else "pending_configuration"
                value.last_check_at = value.last_check_latency_ms = None
                value.last_check_error = None
                if isinstance(value, EmbeddingServing):
                    value.last_observed_dimension = None
            session.flush()
            return self._payload(value)

    def set_default(self, kind: str, serving_id: str) -> dict[str, Any]:
        model = self._model(kind)
        with self.sessions.begin() as session:
            values = list(session.scalars(select(model).with_for_update()))
            target = next((item for item in values if item.id == serving_id), None)
            if not target:
                raise ValueError("Serving 不存在")
            if not target.is_enabled:
                raise ValueError("已停用 Serving 不能设为默认")
            for item in values:
                item.is_default = item.id == serving_id
            session.flush()
            return self._payload(target)

    def references(self, kind: str, serving_id: str) -> dict[str, Any]:
        model = self._model(kind)
        with self.sessions() as session:
            value = session.get(model, serving_id)
            if not value:
                raise ValueError("Serving 不存在")
            if kind == "model":
                templates = [item.id for item in session.scalars(select(KnowledgeFlowTemplate))
                             if _contains_llm_serving(item.definition_json, value.serving_code)]
                revisions = [item.id for item in session.scalars(select(KnowledgeFlowTemplateRevision))
                             if _contains_llm_serving(item.definition_json, value.serving_code)]
                snapshots = [item.id for item in session.scalars(select(FlowExecutionSnapshot))
                             if _contains_llm_serving(item.compiled_definition_json, value.serving_code)]
                result = {"templates": templates, "revisions": revisions, "snapshots": snapshots}
            else:
                profiles = list(session.scalars(select(KnowledgeIndexProfile.id).where(
                    KnowledgeIndexProfile.embedding_serving_id == value.serving_code)))
                revisions = list(session.scalars(select(KnowledgeIndexProfileRevision.id).where(
                    KnowledgeIndexProfileRevision.embedding_serving_id == value.serving_code)))
                assets = list(session.scalars(select(KnowledgeAssetVersion.id).where(
                    KnowledgeAssetVersion.embedding_serving_id == value.serving_code)))
                result = {"profiles": profiles, "revisions": revisions, "asset_versions": assets}
            return {"serving_id": value.id, "serving_code": value.serving_code,
                    "referenced": any(result.values()), "references": result}

    def delete(self, kind: str, serving_id: str) -> dict[str, Any]:
        model = self._model(kind)
        references = self.references(kind, serving_id)
        if references["referenced"]:
            raise ValueError("Serving 仍有引用，不能删除")
        with self.sessions.begin() as session:
            value = session.get(model, serving_id, with_for_update=True)
            if not value:
                raise ValueError("Serving 不存在")
            if value.is_default:
                raise ValueError("默认 Serving 不能删除")
            session.execute(delete(model).where(model.id == serving_id))
            return {"id": serving_id, "deleted": True}

    def _credential(self, kind: str, value: ModelServing | EmbeddingServing) -> str:
        if not value.credential_ciphertext:
            return "EMPTY"
        return self.cipher.decrypt(value.credential_ciphertext, self._aad(kind, value.id), value.credential_key_version)

    def resolved(self, kind: str, serving_code: str | None = None, *, require_healthy: bool = False,
                 require_configured: bool = True):
        model = self._model(kind)
        with self.sessions() as session:
            query = select(model).where(model.serving_code == serving_code) if serving_code else select(model).where(model.is_default.is_(True))
            value = session.scalar(query)
            if not value:
                raise ValueError(f"{'LLM' if kind == 'model' else 'Embedding'} Serving 未配置：{serving_code or '<default>'}")
            if not value.is_enabled:
                raise ValueError(f"Serving 已停用：{value.serving_code}")
            if require_configured and not value.base_url:
                raise ValueError(f"Serving 待配置：{value.serving_code}")
            if require_healthy and value.last_check_status != "healthy":
                raise ValueError(f"Serving 最近测试不是正常状态：{value.serving_code}")
            credential = self._credential(kind, value)
            session.expunge(value)
            return value, credential

    def test(self, kind: str, serving_id: str) -> dict[str, Any]:
        model = self._model(kind)
        with self.sessions() as session:
            current = session.get(model, serving_id)
            if not current:
                raise ValueError("Serving 不存在")
            code = current.serving_code
        started = time.monotonic()
        status, error, observed = "healthy", None, None
        try:
            value, credential = self.resolved(kind, code)
            client = self.client_factory(base_url=value.base_url, api_key=credential,
                                         timeout=value.timeout_seconds, max_retries=value.max_retries)
            if kind == "model":
                extra_body = {"app_id": "dataforge"}
                if value.disable_thinking:
                    extra_body["chat_template_kwargs"] = {"enable_thinking": False}
                response = client.chat.completions.create(
                    model=value.model_name, messages=[{"role": "user", "content": "Reply with OK."}],
                    max_tokens=min(value.max_tokens, 8), extra_body=extra_body,
                )
                if not getattr(response, "choices", None) or not getattr(response.choices[0].message, "content", None):
                    raise ValueError("chat/completions 响应缺少有效 choices")
            else:
                response = client.embeddings.create(model=value.model_name, input=["DataForge embedding connectivity test"])
                data = list(getattr(response, "data", None) or [])
                vectors = [list(getattr(item, "embedding", None) or []) for item in data]
                if len(vectors) != 1 or not vectors[0] or any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in vectors[0]):
                    raise ValueError("embeddings 响应数量、非空或数值格式无效")
                observed = len(vectors[0])
                if observed != value.dimension:
                    status = "dimension_mismatch"
                    error = f"实际维度 {observed} 与配置维度 {value.dimension} 不一致"
        except Exception as exc:
            if status != "dimension_mismatch":
                status, error = _classify_error(exc), _safe_error(exc)
        latency = round((time.monotonic() - started) * 1000)
        with self.sessions.begin() as session:
            value = session.get(model, serving_id, with_for_update=True)
            if not value:
                raise ValueError("Serving 不存在")
            value.last_check_status, value.last_check_at = status, utc_now()
            value.last_check_latency_ms, value.last_check_error = latency, error
            if isinstance(value, EmbeddingServing):
                value.last_observed_dimension = observed
            session.flush()
            return self._payload(value)

    def check_configured_on_startup(self) -> list[dict[str, Any]]:
        """Check every enabled, configured Serving once without blocking peers on failure."""
        results: list[dict[str, Any]] = []
        for kind in ("model", "embedding"):
            candidates = [item for item in self.list(kind) if item["is_enabled"] and item["base_url"]]
            for item in candidates:
                try:
                    checked = self.test(kind, item["id"])
                    results.append({
                        "kind": kind, "id": checked["id"], "serving_code": checked["serving_code"],
                        "status": checked["last_check_status"],
                    })
                    LOGGER.info(
                        "Serving startup check completed: kind=%s serving_code=%s status=%s",
                        kind, checked["serving_code"], checked["last_check_status"],
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Serving startup check failed unexpectedly: kind=%s serving_code=%s error_type=%s",
                        kind, item["serving_code"], type(exc).__name__,
                    )
                    results.append({
                        "kind": kind, "id": item["id"], "serving_code": item["serving_code"],
                        "status": "check_failed", "error_type": type(exc).__name__,
                    })
        return results


class DatabaseLLMServingRegistry:
    def __init__(self, manager: ServingManager):
        self.manager = manager
        self._clients: dict[str, tuple[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def default_serving(self) -> str:
        value, _ = self.manager.resolved("model", require_configured=False)
        return value.serving_code

    @property
    def serving_ids(self) -> frozenset[str]:
        return frozenset(item["serving_code"] for item in self.manager.list("model"))

    def require(self, serving_id: str | None = None) -> LLMServingConfig:
        value, _ = self.manager.resolved("model", serving_id, require_configured=False)
        return LLMServingConfig(value.serving_code, value.serving_type, value.model_name, value.base_url,
                                value.timeout_seconds, value.max_retries, value.max_tokens, value.disable_thinking)

    def require_healthy(self, serving_id: str | None = None) -> LLMServingConfig:
        value, _ = self.manager.resolved("model", serving_id, require_healthy=True)
        return LLMServingConfig(value.serving_code, value.serving_type, value.model_name, value.base_url,
                                value.timeout_seconds, value.max_retries, value.max_tokens, value.disable_thinking)

    def client(self, serving_id: str | None = None) -> tuple[LLMServingConfig, Any]:
        value, credential = self.manager.resolved("model", serving_id)
        config = LLMServingConfig(value.serving_code, value.serving_type, value.model_name, value.base_url,
                                  value.timeout_seconds, value.max_retries, value.max_tokens, value.disable_thinking)
        fingerprint = hashlib.sha256(json.dumps({
            "url": value.base_url, "model": value.model_name, "credential": value.credential_ciphertext,
            "timeout": value.timeout_seconds, "retries": value.max_retries,
        }, sort_keys=True).encode()).hexdigest()
        with self._lock:
            cached = self._clients.get(value.serving_code)
            if not cached or cached[0] != fingerprint:
                client = self.manager.client_factory(base_url=value.base_url, api_key=credential,
                                                     timeout=value.timeout_seconds, max_retries=value.max_retries)
                self._clients[value.serving_code] = (fingerprint, client)
            return config, self._clients[value.serving_code][1]


class EmbeddingServingRegistry:
    def __init__(self, manager: ServingManager, *, provider_factory=None):
        self.manager = manager
        self.provider_factory = provider_factory

    @property
    def default_serving(self) -> str:
        value, _ = self.manager.resolved("embedding", require_configured=False)
        return value.serving_code

    def require(self, serving_id: str | None = None, *, healthy: bool = False) -> EmbeddingServingConfig:
        value, _ = self.manager.resolved("embedding", serving_id, require_healthy=healthy,
                                         require_configured=healthy)
        return EmbeddingServingConfig(value.serving_code, value.provider_type, value.model_name, value.base_url,
                                      value.dimension, value.batch_size, value.timeout_seconds, value.max_retries)

    def provider(self, serving_id: str | None = None, *, healthy: bool = False):
        value, credential = self.manager.resolved("embedding", serving_id, require_healthy=healthy)
        factory = self.provider_factory
        if factory is None:
            from .vector import OpenAILikeEmbeddingProvider
            factory = OpenAILikeEmbeddingProvider
        return factory(value.base_url, credential, value.batch_size,
                       timeout_seconds=value.timeout_seconds, max_retries=value.max_retries), self.require(value.serving_code)
