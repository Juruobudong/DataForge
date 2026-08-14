"""Deployment-only document parser adapters used by the V7 Runner."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


MINERU_VERSION = "3.4.4"


@dataclass(frozen=True)
class MinerUParseResult:
    markdown: str
    middle_json: dict[str, Any]
    content_list: list[dict[str, Any]]
    version: str
    backend: str = "pipeline"
    parse_method: str = "auto"


def _json_value(value: Any, *, label: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MinerU 返回的{label}不是合法 JSON") from exc
    return value


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("detail") or payload.get("message")
        if detail:
            return str(detail)
    return response.text.strip()[:500] or f"HTTP {response.status_code}"


def parse_with_mineru(*, filename: str, payload: bytes, parse_method: str = "auto") -> MinerUParseResult:
    """Synchronously parse one PDF through the pinned self-hosted MinerU API."""
    base_url = os.getenv("DATAFORGE_MINERU_URL", "http://mineru-api:8000").rstrip("/")
    if not base_url:
        raise ValueError("DATAFORGE_MINERU_URL 未配置")
    try:
        timeout = float(os.getenv("DATAFORGE_MINERU_TIMEOUT_SECONDS", "1800"))
    except ValueError as exc:
        raise ValueError("DATAFORGE_MINERU_TIMEOUT_SECONDS 必须是数字") from exc
    if timeout <= 0:
        raise ValueError("DATAFORGE_MINERU_TIMEOUT_SECONDS 必须大于 0")

    form = {
        "backend": "pipeline",
        "parse_method": parse_method,
        "lang_list": "ch",
        "formula_enable": "true",
        "table_enable": "true",
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "false",
        "response_format_zip": "false",
        "return_original_file": "false",
    }
    files = {"files": (filename, payload, "application/pdf")}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base_url}/file_parse", files=files, data=form)
    except httpx.TimeoutException as exc:
        raise ValueError(f"MinerU PDF 解析超时（{timeout:g} 秒）") from exc
    except httpx.RequestError as exc:
        raise ValueError(f"MinerU PDF 解析服务不可达：{exc}") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"MinerU PDF 解析失败（HTTP {response.status_code}）：{_response_error(response)}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ValueError("MinerU 返回的响应不是合法 JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("MinerU 返回的响应结构无效")
    version = str(body.get("version") or "")
    if version != MINERU_VERSION:
        raise ValueError(f"MinerU 版本必须为 {MINERU_VERSION}，当前为 {version or '未知'}")
    if body.get("backend") != "pipeline":
        raise ValueError("MinerU 未使用 pipeline 后端")
    results = body.get("results")
    if not isinstance(results, dict) or len(results) != 1:
        raise ValueError("MinerU 必须返回且只能返回一个文件结果")
    result = next(iter(results.values()))
    if not isinstance(result, dict):
        raise ValueError("MinerU 文件结果结构无效")
    markdown = result.get("md_content")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("MinerU 未返回可用的 Markdown 文本")
    middle_json = _json_value(result.get("middle_json"), label=" Middle JSON")
    if not isinstance(middle_json, dict):
        raise ValueError("MinerU 返回的 Middle JSON 结构无效")
    content_list = _json_value(result.get("content_list", []), label=" Content List")
    if not isinstance(content_list, list) or not all(isinstance(item, dict) for item in content_list):
        raise ValueError("MinerU 返回的 Content List 结构无效")
    return MinerUParseResult(
        markdown=markdown,
        middle_json=middle_json,
        content_list=content_list,
        version=version,
    )


def content_list_pages(content_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group MinerU content-list text by its zero-based PDF page index."""
    grouped: dict[int, list[str]] = {}
    for item in content_list:
        page_index = item.get("page_idx")
        if not isinstance(page_index, int) or page_index < 0:
            continue
        value = next((item.get(key) for key in ("text", "table_body", "equation") if item.get(key)), None)
        if isinstance(value, list):
            text = "\n".join(str(part).strip() for part in value if str(part).strip())
        else:
            text = str(value or "").strip()
        if text:
            grouped.setdefault(page_index, []).append(text)
    return [
        {"page_index": page_index, "page": page_index + 1, "text": "\n\n".join(parts)}
        for page_index, parts in sorted(grouped.items())
        if parts
    ]
