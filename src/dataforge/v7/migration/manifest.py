"""Versioned manifest contract shared by export and local import."""
from __future__ import annotations

from typing import Any


FORMAT = "dataforge-migration"
SCHEMA_VERSION = 1
PACKAGE_KINDS = {"deployment_seed", "knowledge_update"}


class ManifestError(ValueError):
    pass


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("format") != FORMAT:
        raise ManifestError("不是 DataForge Migration Package")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("不支持的 migration schema_version")
    if value.get("package_kind") not in PACKAGE_KINDS:
        raise ManifestError("package_kind 无效")
    for key in ("package_id", "source_instance_id", "project", "deployment", "scope", "collections"):
        if not value.get(key):
            raise ManifestError(f"manifest 缺少 {key}")
    project, deployment, scope = value["project"], value["deployment"], value["scope"]
    if not isinstance(project, dict) or not project.get("id") or not project.get("code"):
        raise ManifestError("manifest project 无效")
    if not isinstance(deployment, dict) or not deployment.get("id") or not deployment.get("code"):
        raise ManifestError("manifest deployment 无效")
    if int(scope.get("deployment_count", 0)) != 1:
        raise ManifestError("migration package 必须且只能包含一个 Deployment")
    libraries = scope.get("knowledge_library_ids")
    if not isinstance(libraries, list) or len(libraries) != len(set(libraries)):
        raise ManifestError("knowledge_library_ids 必须是无重复列表")
    collections = value.get("collections")
    if not isinstance(collections, dict):
        raise ManifestError("collections 必须按 Collection 分组")
    planned = set()
    for collection_name, partitions in collections.items():
        if not collection_name or not isinstance(partitions, list):
            raise ManifestError("Collection/Partition 计划无效")
        for partition in partitions:
            if not isinstance(partition, dict) or not str(partition.get("partition_name", "")).startswith("kl_"):
                raise ManifestError("只允许 kl_* Partition")
            key = (collection_name, partition["partition_name"])
            if key in planned: raise ManifestError("Collection/Partition 计划重复")
            planned.add(key)
    if value["package_kind"] == "deployment_seed" and not value.get("base_route_version"):
        raise ManifestError("deployment_seed 必须包含 base_route_version")
    return value
