"""Test-only import of kg_for_consultation legacy Milvus collections.

The importer deliberately refuses every Milvus endpoint except the approved
test service.  Inventory and dry-run never mutate MySQL or Milvus.  Import
creates DataForge KnowledgeItems first and delegates vector materialisation to
the normal provisioning/vector-sync services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from pymilvus import MilvusClient

if TYPE_CHECKING:
    from .models import KnowledgeLibrary
    from .store import V7Store


KG_TEST_MILVUS_URL = os.environ.get("DATAFORGE_KG_TEST_MILVUS_URL") or "http://milvus-central-test:19531"


@dataclass(frozen=True)
class LegacyCollectionSpec:
    collection_name: str
    knowledge_type: str
    graph_mode: str | None
    library_code: str
    library_name: str
    output_fields: tuple[str, ...]
    migration_action: str = "migrate"


LEGACY_COLLECTIONS: dict[str, LegacyCollectionSpec] = {
    "department_kb_infectious_disease_v3": LegacyCollectionSpec(
        collection_name="department_kb_infectious_disease_v3",
        knowledge_type="text",
        graph_mode=None,
        library_code="legacy-kg-department-text-infectious-disease",
        library_name="传染科文本知识（legacy test import）",
        output_fields=(
            "chunk_id", "department_code", "document_id", "document_name", "document_type",
            "chapter", "section", "heading", "content_type", "page_start", "page_end",
            "line_start", "line_end", "raw_content", "cleaned_content", "content_hash", "source_path",
        ),
    ),
    "medical_symptoms_signs": LegacyCollectionSpec(
        collection_name="medical_symptoms_signs",
        knowledge_type="graph",
        graph_mode="triple",
        library_code="legacy-kg-medical-symptoms-signs",
        library_name="通用疾病症状体征图谱（legacy test import）",
        output_fields=("uid", "disease_name", "symptom_sign_name", "description", "excerpts"),
    ),
    "infectious_disease_symptoms_signs": LegacyCollectionSpec(
        collection_name="infectious_disease_symptoms_signs",
        knowledge_type="graph",
        graph_mode="triple",
        library_code="legacy-kg-infectious-disease-symptoms-signs",
        library_name="传染病疾病症状体征图谱（legacy test import）",
        output_fields=("uid", "disease_name", "symptom_sign_name", "description", "excerpts"),
    ),
}

PRESERVED_COLLECTIONS: dict[str, LegacyCollectionSpec] = {
    "llm_cache": LegacyCollectionSpec(
        collection_name="llm_cache",
        knowledge_type="cache",
        graph_mode=None,
        library_code="",
        library_name="",
        output_fields=("id", "complaint", "plan"),
        migration_action="preserve",
    ),
}


def _require_test_uri(uri: str) -> str:
    normalized = str(uri or "").strip().rstrip("/")
    if normalized != KG_TEST_MILVUS_URL:
        raise ValueError(f"kg legacy 第一阶段只允许 {KG_TEST_MILVUS_URL}，实际为 {normalized or '<empty>'}")
    return normalized


def _json_digest(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)):
        digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_rows(client: MilvusClient, spec: LegacyCollectionSpec, *, batch_size: int = 1000) -> list[dict[str, Any]]:
    iterator = client.query_iterator(
        collection_name=spec.collection_name, filter="", output_fields=list(spec.output_fields),
        batch_size=batch_size,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            rows.extend(dict(item) for item in batch)
    finally:
        iterator.close()
    return rows


def _schema_field(field: dict[str, Any]) -> dict[str, Any]:
    data_type = field.get("type") or field.get("data_type") or ""
    return {
        "name": str(field.get("name") or ""),
        "data_type": str(getattr(data_type, "name", data_type)),
        "is_primary": bool(field.get("is_primary")),
        "is_partition_key": bool(field.get("is_partition_key")),
        "params": dict(field.get("params") or {}),
    }


def inventory(client: MilvusClient, specs: Iterable[LegacyCollectionSpec]) -> list[dict[str, Any]]:
    existing = set(client.list_collections())
    reports: list[dict[str, Any]] = []
    for spec in specs:
        report: dict[str, Any] = {
            "collection_name": spec.collection_name,
            "knowledge_type": spec.knowledge_type,
            "graph_mode": spec.graph_mode,
            "migration_action": spec.migration_action,
            "exists": spec.collection_name in existing,
        }
        if report["exists"]:
            rows = _read_rows(client, spec)
            description = client.describe_collection(collection_name=spec.collection_name)
            schema = [_schema_field(field) for field in description.get("fields", [])]
            report.update({
                "row_count": len(rows),
                "content_sha256": _json_digest(rows),
                "primary_keys": [field["name"] for field in schema if field["is_primary"]],
                "source_schema": schema,
                "vector_dimensions": {
                    field["name"]: int(field["params"]["dim"])
                    for field in schema if field["params"].get("dim")
                },
            })
        reports.append(report)
    return reports


def _text_candidates(spec: LegacyCollectionSpec, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        chunk_id = str(row.get("chunk_id") or "").strip()
        content = str(row.get("cleaned_content") or "").strip()
        if not chunk_id or not content:
            errors.append({"row": index, "chunk_id": chunk_id, "reason": "chunk_id 或 cleaned_content 为空"})
            continue
        data = {key: row.get(key) for key in spec.output_fields if key not in {"cleaned_content"} and row.get(key) is not None}
        data.update({"legacy_collection": spec.collection_name, "legacy_import_kind": "text"})
        candidates.append({
            "source_knowledge_id": f"legacy:{spec.collection_name}:{chunk_id}",
            "canonical_content": content,
            "data_json": data,
        })
    return candidates, errors


def _graph_candidates(spec: LegacyCollectionSpec, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        disease = str(row.get("disease_name") or "").strip()
        symptom = str(row.get("symptom_sign_name") or "").strip()
        if not disease or not symptom:
            errors.append({"row": index, "uid": str(row.get("uid") or ""), "reason": "disease_name 或 symptom_sign_name 为空"})
            continue
        uid = str(row.get("uid") or "").strip()
        description = str(row.get("description") or "").strip()
        excerpts = str(row.get("excerpts") or "").strip()
        if not description and not excerpts:
            errors.append({"row": index, "uid": uid, "reason": "description 与 excerpts 均为空"})
            continue
        current = grouped.setdefault((disease, symptom), {
            "legacy_uids": [], "descriptions": [], "excerpts": [], "evidence": [],
        })
        if uid:
            current["legacy_uids"].append(uid)
        if description:
            current["descriptions"].append(description)
        if excerpts:
            current["excerpts"].append(excerpts)
        current["evidence"].append({"legacy_uid": uid, "description": description, "excerpts": excerpts})

    candidates: list[dict[str, Any]] = []
    for (disease, symptom), values in sorted(grouped.items()):
        identity = hashlib.sha256(f"{spec.collection_name}\n{disease}\n表现为\n{symptom}".encode("utf-8")).hexdigest()
        data = {
            "subject": disease,
            "predicate": "表现为",
            "object": symptom,
            "subject_type": "disease",
            "object_type": "symptom_or_sign",
            "description": "\n".join(sorted(set(values["descriptions"]))),
            "excerpts": "\n".join(sorted(set(values["excerpts"]))),
            "evidence": sorted(
                values["evidence"],
                key=lambda item: (item["legacy_uid"], item["description"], item["excerpts"]),
            ),
            "legacy_uids": sorted(set(values["legacy_uids"])),
            "legacy_collection": spec.collection_name,
            "legacy_import_kind": "graph:triple",
        }
        candidates.append({
            "source_knowledge_id": f"legacy:{spec.collection_name}:{identity}",
            "canonical_content": f"{disease} 表现为 {symptom}",
            "data_json": data,
        })
    return candidates, errors


def transform(spec: LegacyCollectionSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates, errors = _text_candidates(spec, rows) if spec.knowledge_type == "text" else _graph_candidates(spec, rows)
    return {
        "collection_name": spec.collection_name,
        "source_row_count": len(rows),
        "candidate_count": len(candidates),
        "duplicate_count": max(0, len(rows) - len(candidates) - len(errors)),
        "errors": errors,
        "candidate_sha256": _json_digest(candidates),
        "candidates": candidates,
    }


def _ensure_library(store: "V7Store", spec: LegacyCollectionSpec) -> "KnowledgeLibrary":
    from sqlalchemy import select

    from .models import KnowledgeLibrary
    with store.sessions() as session:
        existing = session.scalar(select(KnowledgeLibrary).where(KnowledgeLibrary.code == spec.library_code))
        if existing:
            library_id = existing.id
        else:
            library_id = ""
    if not library_id:
        payload = store.create_knowledge_library(
            spec.library_name, spec.knowledge_type,
            description=f"从测试 Collection {spec.collection_name} 确定性导入；源 Collection 保持只读",
            graph_mode=spec.graph_mode, code=spec.library_code,
        )
        library_id = payload["id"]
    with store.sessions.begin() as session:
        library = session.get(KnowledgeLibrary, library_id, with_for_update=True)
        if not library:
            raise ValueError("目标知识库创建失败")
        library.migration_status = "migrating"
    with store.sessions() as session:
        library = session.get(KnowledgeLibrary, library_id)
        assert library
        return library


def upsert_candidates(store: "V7Store", spec: LegacyCollectionSpec, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    from sqlalchemy import select

    from .models import KnowledgeItem, KnowledgeLibrary, KnowledgeTypeModeRevision, KnowledgeTypeRevision
    from .store import content_hash, new_id

    library = _ensure_library(store, spec)
    incoming = {candidate["source_knowledge_id"] for candidate in candidates}
    counts = {"added": 0, "updated": 0, "unchanged": 0, "inactivated": 0}
    with store.sessions.begin() as session:
        current_library = session.get(KnowledgeLibrary, library.id, with_for_update=True)
        if not current_library:
            raise ValueError("目标知识库不存在")
        current_library.migration_status = "migrating"
        revision = session.get(KnowledgeTypeRevision, current_library.knowledge_type_revision_id)
        if not revision or revision.status != "published":
            raise ValueError("目标知识库没有已发布 Knowledge Type Revision")
        mode_revision = None
        if spec.graph_mode:
            mode_revision = session.scalar(select(KnowledgeTypeModeRevision).where(
                KnowledgeTypeModeRevision.knowledge_type_revision_id == revision.id,
                KnowledgeTypeModeRevision.mode == spec.graph_mode,
                KnowledgeTypeModeRevision.status == "published",
            ).order_by(KnowledgeTypeModeRevision.revision_no.desc()))
        current = {item.source_knowledge_id: item for item in session.scalars(select(KnowledgeItem).where(
            KnowledgeItem.knowledge_library_id == current_library.id,
        ))}
        for candidate in candidates:
            store._validate_candidate_contract(candidate, revision, mode_revision)
            key = candidate["source_knowledge_id"]
            content = candidate["canonical_content"]
            data = candidate["data_json"]
            digest = content_hash(content, data)
            item = current.get(key)
            if not item:
                session.add(KnowledgeItem(
                    id=new_id("ki"), knowledge_library_id=current_library.id,
                    knowledge_type_revision_id=revision.id, source_knowledge_id=key,
                    canonical_content=content, data_json=data, content_hash=digest, status="active",
                ))
                counts["added"] += 1
            elif item.content_hash != digest or item.status != "active":
                item.canonical_content, item.data_json, item.content_hash = content, data, digest
                item.knowledge_type_revision_id, item.status = revision.id, "active"
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
        for key, item in current.items():
            if key not in incoming and item.status == "active":
                item.status = "inactive"
                counts["inactivated"] += 1
        library_id, partition_name = current_library.id, current_library.partition_name
    return {"library_id": library_id, "partition_name": partition_name, **counts}


def run_vector_sync(store: "V7Store", library_id: str, uri: str) -> list[dict[str, Any]]:
    from .provisioning import ManagedCollectionProvisioner
    from .vector import OpenAILikeEmbeddingProvider, V7Milvus, VectorSyncService

    library, profiles = store.index_profiles_for_library(library_id)
    milvus = V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN"))
    provisioner = ManagedCollectionProvisioner(store, milvus)
    for profile in profiles:
        provisioner.ensure_collection_for_profile(profile.revision_id)
    api_base = os.getenv("EMBEDDING_API_BASE", "").strip()
    if not api_base:
        raise RuntimeError("未配置 EMBEDDING_API_BASE，不能为旧知识重新生成 embedding")
    batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    if batch_size <= 0:
        raise ValueError("EMBEDDING_BATCH_SIZE 必须为正整数")
    service = VectorSyncService(
        store,
        milvus=milvus,
        embeddings=OpenAILikeEmbeddingProvider(
            api_base, os.getenv("EMBEDDING_API_KEY", "fake"), batch_size,
        ),
    )
    results = [service.run(job["id"]) for job in store.create_vector_sync_jobs(library.id)]
    if not results or any(result.get("status") != "ready" for result in results):
        raise RuntimeError(f"Vector Sync 未全部 ready: {results}")
    return results


def _normalized_candidate(source_id: Any, content: Any, data: Any) -> dict[str, Any]:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass
    return {
        "source_knowledge_id": str(source_id or ""),
        "canonical_content": str(content or ""),
        "data_json": data or {},
    }


def _read_partition_candidates(client: MilvusClient, collection_name: str,
                               partition_name: str) -> list[dict[str, Any]]:
    iterator = client.query_iterator(
        collection_name=collection_name, partition_names=[partition_name], filter="",
        output_fields=["knowledge_library_id", "source_knowledge_id", "content", "data"],
        batch_size=1000,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            rows.extend(dict(item) for item in batch)
    finally:
        iterator.close()
    return rows


def verify_library(store: "V7Store", client: MilvusClient, spec: LegacyCollectionSpec,
                   expected: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select

    from .models import KnowledgeItem, KnowledgeLibrary

    with store.sessions() as session:
        library = session.scalar(select(KnowledgeLibrary).where(KnowledgeLibrary.code == spec.library_code))
        if not library:
            raise ValueError(f"{spec.collection_name} 尚未创建目标知识库")
        active_items = list(session.scalars(select(KnowledgeItem).where(
            KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active",
        )))
        active = len(active_items)
        library_id, collection_name, partition_name = library.id, (
            "dataforge_text_knowledge" if spec.knowledge_type == "text" else "dataforge_graph_triple_knowledge"
        ), library.partition_name
    partition = client.get_partition_stats(collection_name=collection_name, partition_name=partition_name)
    partition_count = int(partition.get("row_count") or 0)
    partition_rows = _read_partition_candidates(client, collection_name, partition_name)
    mysql_candidates = [_normalized_candidate(item.source_knowledge_id, item.canonical_content, item.data_json)
                        for item in active_items]
    partition_candidates = [_normalized_candidate(row.get("source_knowledge_id"), row.get("content"), row.get("data"))
                            for row in partition_rows if str(row.get("knowledge_library_id") or "") == library_id]
    expected_candidates = [_normalized_candidate(item.get("source_knowledge_id"), item.get("canonical_content"), item.get("data_json"))
                           for item in expected["candidates"]]
    expected_ids = {item["source_knowledge_id"] for item in expected_candidates}
    mysql_ids = {item["source_knowledge_id"] for item in mysql_candidates}
    partition_ids = {item["source_knowledge_id"] for item in partition_candidates}
    expected_hash = _json_digest(expected_candidates)
    mysql_hash = _json_digest(mysql_candidates)
    partition_hash = _json_digest(partition_candidates)
    vector_status = store.vector_status(library_id)
    ready = bool(
        vector_status["ready"]
        and active == len(expected_candidates) == partition_count == len(partition_candidates)
        and expected_ids == mysql_ids == partition_ids
        and expected_hash == mysql_hash == partition_hash
    )
    with store.sessions.begin() as session:
        current = session.get(KnowledgeLibrary, library_id, with_for_update=True)
        current.migration_status = "ready" if ready else "migrating"
    return {
        "collection_name": spec.collection_name, "library_id": library_id,
        "target_collection": collection_name, "partition_name": partition_name,
        "active_knowledge_count": active, "partition_row_count": partition_count,
        "source_id_match": expected_ids == mysql_ids == partition_ids,
        "expected_sha256": expected_hash, "mysql_sha256": mysql_hash,
        "partition_sha256": partition_hash,
        "vector_ready": vector_status["ready"], "ready": ready,
    }


def _selected_specs(names: list[str]) -> list[LegacyCollectionSpec]:
    selected = names or list(LEGACY_COLLECTIONS)
    unknown = sorted(set(selected) - set(LEGACY_COLLECTIONS))
    if unknown:
        raise ValueError(f"不支持的 legacy Collection: {unknown}")
    return [LEGACY_COLLECTIONS[name] for name in selected]


def _inventory_specs(names: list[str]) -> list[LegacyCollectionSpec]:
    supported = {**LEGACY_COLLECTIONS, **PRESERVED_COLLECTIONS}
    selected = names or list(supported)
    unknown = sorted(set(selected) - set(supported))
    if unknown:
        raise ValueError(f"不支持盘点的 Collection: {unknown}")
    return [supported[name] for name in selected]


def main() -> None:
    parser = argparse.ArgumentParser(description="kg_for_consultation legacy Collection 测试迁移")
    parser.add_argument("action", choices=("inventory", "dry-run", "import", "verify"))
    parser.add_argument("--collection", action="append", default=[])
    parser.add_argument("--milvus-uri", default=os.getenv("DATAFORGE_MILVUS_URI", KG_TEST_MILVUS_URL))
    parser.add_argument("--database-url", default="")
    args = parser.parse_args()
    uri = _require_test_uri(args.milvus_uri)
    client = MilvusClient(uri=uri, token=os.getenv("DATAFORGE_MILVUS_TOKEN") or None)
    if args.action == "inventory":
        print(json.dumps(inventory(client, _inventory_specs(args.collection)), ensure_ascii=False, indent=2))
        return
    specs = _selected_specs(args.collection)

    existing = set(client.list_collections())
    reports: list[dict[str, Any]] = []
    transforms: list[tuple[LegacyCollectionSpec, dict[str, Any]]] = []
    for spec in specs:
        if spec.collection_name not in existing:
            reports.append({"collection_name": spec.collection_name, "exists": False,
                            "error": "测试 Milvus 中不存在；未访问其它环境"})
            continue
        result = transform(spec, _read_rows(client, spec))
        reports.append({key: value for key, value in result.items() if key != "candidates"})
        transforms.append((spec, result))
    if args.action == "dry-run":
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return
    if any(report.get("errors") for report in reports):
        raise SystemExit(json.dumps({"status": "blocked", "reports": reports}, ensure_ascii=False, indent=2))

    from dataforge.config import Settings
    from .store import V7Store

    database_url = args.database_url or Settings.load().database_url
    store = V7Store(database_url)
    store.assert_schema_current()
    if args.action == "import":
        for spec, result in transforms:
            imported = upsert_candidates(store, spec, result["candidates"])
            imported["vector_jobs"] = run_vector_sync(store, imported["library_id"], uri)
            reports.append({"collection_name": spec.collection_name, "import": imported})
    verified = [verify_library(store, client, spec, result) for spec, result in transforms]
    print(json.dumps({"reports": reports, "verified": verified}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
