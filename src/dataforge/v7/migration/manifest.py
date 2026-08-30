"""Versioned manifest contract shared by export and local import."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit


FORMAT = "dataforge-migration"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
PACKAGE_KINDS = {"deployment_seed", "institution_release", "knowledge_update"}
SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "private_key"}


class ManifestError(ValueError):
    pass


def _contains_sensitive(value: Any, path: str = "") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            current = f"{path}.{key}" if path else str(key)
            if normalized in SENSITIVE_KEYS and child not in (None, "", False):
                return current
            if normalized in {"uri", "url", "milvus_url"} and isinstance(child, str):
                parsed = urlsplit(child)
                query_keys = {key.lower() for key in parse_qs(parsed.query)}
                if parsed.username or parsed.password or query_keys & SENSITIVE_KEYS:
                    return current
            found = _contains_sensitive(child, current)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _contains_sensitive(child, f"{path}[{index}]")
            if found:
                return found
    return None


def _validate_collections(value: dict[str, Any]) -> set[tuple[str, str]]:
    collections = value.get("collections")
    if not isinstance(collections, dict):
        raise ManifestError("collections 必须按 Collection 分组")
    planned: set[tuple[str, str]] = set()
    for collection_name, partitions in collections.items():
        if not collection_name or not isinstance(partitions, list):
            raise ManifestError("Collection/Partition 计划无效")
        for partition in partitions:
            if not isinstance(partition, dict) or not str(partition.get("partition_name", "")).startswith("kl_"):
                raise ManifestError("只允许 kl_* Partition")
            key = (collection_name, partition["partition_name"])
            if key in planned:
                raise ManifestError("Collection/Partition 计划重复")
            planned.add(key)
    return planned


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("format") != FORMAT:
        raise ManifestError("不是 DataForge Migration Package")
    schema_version = int(value.get("schema_version", 0))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestError("不支持的 migration schema_version")
    if value.get("package_kind") not in PACKAGE_KINDS:
        raise ManifestError("package_kind 无效")
    for key in ("package_id", "source_instance_id", "deployment", "scope", "collections"):
        if not value.get(key):
            raise ManifestError(f"manifest 缺少 {key}")
    deployment, scope = value["deployment"], value["scope"]
    if not isinstance(deployment, dict) or not deployment.get("id") or not deployment.get("code"):
        raise ManifestError("manifest deployment 无效")
    if int(scope.get("deployment_count", 0)) != 1:
        raise ManifestError("migration package 必须且只能包含一个 Deployment")
    libraries = scope.get("knowledge_library_ids")
    if not isinstance(libraries, list) or len(libraries) != len(set(libraries)):
        raise ManifestError("knowledge_library_ids 必须是无重复列表")
    _validate_collections(value)
    if schema_version == 1:
        project = value.get("project")
        if not isinstance(project, dict) or not project.get("id") or not project.get("code"):
            raise ManifestError("manifest project 无效")
        if value["package_kind"] == "institution_release":
            raise ManifestError("v1 不支持 institution_release")
        if value["package_kind"] == "deployment_seed" and not value.get("base_route_version"):
            raise ManifestError("deployment_seed 必须包含 base_route_version")
        return value

    if int(value.get("manifest_schema_version", 0)) != 2:
        raise ManifestError("v2 manifest_schema_version 必须为 2")
    if deployment.get("scope") != "institution" or not str(deployment.get("institution_code") or "").strip():
        raise ManifestError("v2 manifest 必须包含机构发布目标 institution_code")
    for key in ("minimum_dataforge_version", "maximum_dataforge_version", "source_instance_version",
                "required_features", "operator_versions", "storage_contract_versions",
                "asset_versions", "diff_summary", "tombstones"):
        if key not in value:
            raise ManifestError(f"v2 manifest 缺少 {key}")
    projects = value.get("projects")
    if value["package_kind"] in {"deployment_seed", "institution_release"}:
        if not isinstance(projects, list) or not projects:
            raise ManifestError("Seed/Institution Release 至少包含一个项目")
        seen_projects = set()
        for project in projects:
            deployment_id = str((project or {}).get("project_deployment_id") or "")
            if not deployment_id or deployment_id in seen_projects:
                raise ManifestError("每个 ProjectDeployment 在包中必须且只能出现一次")
            seen_projects.add(deployment_id)
            if not isinstance(project.get("route_snapshot"), dict) or not project.get("route_version"):
                raise ManifestError("项目缺少冻结 RouteVersion/Snapshot")
    elif projects not in (None, []):
        raise ManifestError("knowledge_update 不得携带可应用项目路由")
    assets = value.get("asset_versions")
    if not isinstance(assets, list) or len({str(item.get("id")) for item in assets}) != len(assets):
        raise ManifestError("asset_versions 必须是无重复列表")
    sensitive = _contains_sensitive(value)
    if sensitive:
        raise ManifestError(f"manifest 不得包含敏感字段值：{sensitive}")
    return value
