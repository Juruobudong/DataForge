"""DataForge-owned Model Serving registry for controlled LLM operators."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

from .servings import DatabaseLLMServingRegistry, ServingManager


DEFAULT_LLM_SERVING_ID = "qwen3_32b"
_SERVING_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_database_registry: DatabaseLLMServingRegistry | None = None


@dataclass(frozen=True)
class LLMServingConfig:
    id: str
    type: str
    model_name: str
    base_url: str
    api_key_env: str
    default_api_key: str
    timeout_seconds: float
    max_retries: int
    max_tokens: int
    disable_thinking: bool


class LLMServingRegistry:
    """Resolve stable Serving IDs without exposing their connection details."""

    def __init__(self, path: Path, default_serving: str, servings: dict[str, LLMServingConfig]):
        self.path = path
        self.default_serving = default_serving
        self._servings = servings
        self._clients: dict[str, OpenAI] = {}

    @property
    def serving_ids(self) -> frozenset[str]:
        return frozenset(self._servings)

    def require(self, serving_id: str | None = None) -> LLMServingConfig:
        resolved_id = str(serving_id or self.default_serving).strip()
        serving = self._servings.get(resolved_id)
        if not serving:
            raise ValueError(f"LLM Serving 未配置：{resolved_id or '<empty>'}")
        return serving

    def client(self, serving_id: str | None = None) -> tuple[LLMServingConfig, OpenAI]:
        serving = self.require(serving_id)
        client = self._clients.get(serving.id)
        if client is None:
            api_key = os.getenv(serving.api_key_env, "").strip() or serving.default_api_key
            client = OpenAI(
                base_url=serving.base_url,
                api_key=api_key,
                timeout=serving.timeout_seconds,
                max_retries=serving.max_retries,
            )
            self._clients[serving.id] = client
        return serving, client


def _config_path(path: str | Path | None = None) -> Path:
    configured = path or os.getenv("DATAFORGE_LLM_SERVINGS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(os.getenv("DATAFORGE_ROOT") or Path(__file__).resolve().parents[3])
    return (root / "llm_servings.yaml").resolve()


def _required_text(data: dict[str, Any], key: str, serving_id: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValueError(f"LLM Serving {serving_id} 缺少配置 {key}")
    return value


def _positive_float(data: dict[str, Any], key: str, serving_id: str) -> float:
    try:
        value = float(data.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LLM Serving {serving_id} 的 {key} 必须是正数") from exc
    if value <= 0:
        raise ValueError(f"LLM Serving {serving_id} 的 {key} 必须是正数")
    return value


def _non_negative_int(data: dict[str, Any], key: str, serving_id: str) -> int:
    raw = data.get(key)
    if isinstance(raw, bool):
        raise ValueError(f"LLM Serving {serving_id} 的 {key} 必须是非负整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"LLM Serving {serving_id} 的 {key} 必须是非负整数") from exc
    if value < 0:
        raise ValueError(f"LLM Serving {serving_id} 的 {key} 必须是非负整数")
    return value


def _parse_registry(path: Path) -> LLMServingRegistry:
    if not path.is_file():
        raise RuntimeError(f"DataForge 缺少 Model Serving 配置文件：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"无法读取 Model Serving 配置：{path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Model Serving 配置根节点必须是对象")
    default_serving = str(raw.get("default_serving") or "").strip()
    raw_servings = raw.get("servings")
    if not default_serving:
        raise ValueError("Model Serving 配置缺少 default_serving")
    if not isinstance(raw_servings, dict) or not raw_servings:
        raise ValueError("Model Serving 配置必须包含非空 servings")

    servings: dict[str, LLMServingConfig] = {}
    for raw_id, raw_config in raw_servings.items():
        serving_id = str(raw_id or "").strip()
        if not _SERVING_ID_PATTERN.fullmatch(serving_id):
            raise ValueError(f"LLM Serving ID 不合法：{serving_id or '<empty>'}")
        if not isinstance(raw_config, dict):
            raise ValueError(f"LLM Serving {serving_id} 配置必须是对象")
        serving_type = _required_text(raw_config, "type", serving_id)
        if serving_type != "openai-compatible-chat":
            raise ValueError(f"LLM Serving {serving_id} 暂不支持类型 {serving_type}")
        disable_thinking = raw_config.get("disable_thinking", True)
        if not isinstance(disable_thinking, bool):
            raise ValueError(f"LLM Serving {serving_id} 的 disable_thinking 必须是布尔值")
        model_name = _required_text(raw_config, "model_name", serving_id)
        base_url = _required_text(raw_config, "base_url", serving_id).rstrip("/")
        api_key_env = _required_text(raw_config, "api_key_env", serving_id)
        max_tokens = _non_negative_int(raw_config, "max_tokens", serving_id)
        if max_tokens == 0:
            raise ValueError(f"LLM Serving {serving_id} 的 max_tokens 必须是正整数")
        servings[serving_id] = LLMServingConfig(
            id=serving_id,
            type=serving_type,
            model_name=model_name,
            base_url=base_url,
            api_key_env=api_key_env,
            default_api_key=str(raw_config.get("default_api_key") or "EMPTY"),
            timeout_seconds=_positive_float(raw_config, "timeout_seconds", serving_id),
            max_retries=_non_negative_int(raw_config, "max_retries", serving_id),
            max_tokens=max_tokens,
            disable_thinking=disable_thinking,
        )
    if default_serving not in servings:
        raise ValueError(f"默认 LLM Serving 未配置：{default_serving}")
    return LLMServingRegistry(path, default_serving, servings)


@lru_cache(maxsize=8)
def _load_registry_cached(path: str) -> LLMServingRegistry:
    return _parse_registry(Path(path))


def configure_llm_serving_registry(sessions, encryption_key: str | None, *, client_factory=OpenAI):
    """Install the process-local DB registry used by Flow compilation and Runner calls."""
    global _database_registry
    _database_registry = DatabaseLLMServingRegistry(
        ServingManager(sessions, encryption_key, client_factory=client_factory)
    )
    return _database_registry


def get_llm_serving_registry(path: str | Path | None = None):
    """Load one registry by resolved path; connection clients stay process-local."""
    if path is not None or os.getenv("DATAFORGE_LLM_SERVINGS_PATH"):
        return _load_registry_cached(str(_config_path(path)))
    if path is None and _database_registry is not None:
        return _database_registry
    return _load_registry_cached(str(_config_path(path)))
