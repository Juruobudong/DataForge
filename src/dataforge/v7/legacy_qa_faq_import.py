"""Test-only migration of qa_agent's legacy FAQ Collection through V7 documents."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import time
from collections import OrderedDict
from typing import Any, Iterable

from pymilvus import MilvusClient
from sqlalchemy import select

from dataforge.config import Settings

from .faq import (
    FAQ_COLLECTION_NAME,
    FAQ_PROFILE_CODE,
    FAQ_SCHEMA,
    FAQ_TEMPLATE_CODE,
    FAQ_TYPE_CODE,
    clean_faq_text,
    faq_rows_digest,
    faq_template_definition,
)
from .models import (
    DocumentLibrary,
    DocumentLibraryTemplateBinding,
    DocumentLibraryTemplateOutput,
    EmbeddingProfile,
    KnowledgeFlowTemplate,
    KnowledgeIndexProfile,
    KnowledgeItem,
    KnowledgeLibrary,
    KnowledgeType,
    KnowledgeTypeIndexBinding,
    KnowledgeTypeRevision,
    Source,
    SourceVersion,
)
from .provisioning import ManagedCollectionProvisioner
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store
from .vector import V7Milvus


FAQ_TEST_MILVUS_URI = os.environ.get("DATAFORGE_FAQ_TEST_MILVUS_URI") or "http://milvus-test:19531"
FAQ_INTERNAL_MILVUS_URI = "http://dataforge-milvus:19530"
FAQ_SOURCE_COLLECTION = "faq"
EXPECTED_PARTITIONS: "OrderedDict[str, int]" = OrderedDict((
    ("426600660", 2649),
    ("48809971X", 22),
    ("XMSZYY", 130),
    ("LZSZYYY_1", 51),
    ("1234000006911257XP", 6),
    ("12340000327998482T", 5),
    ("FJSFCYY", 38),
    ("928900209", 1),
    ("FZRYT", 5),
    ("FJFY", 5298),
    ("123500004880031160", 37),
    ("12530100431361510G", 39),
))
EXPECTED_TOTAL = sum(EXPECTED_PARTITIONS.values())
SOURCE_FIELDS = ("id", "doc_id", "text", "org_code", "question_name", "answer_desc", "aq_id", "document_id", "ref_doc_id")
OPTIONAL_REFERENCE_FIELDS = ("doc_id", "document_id", "ref_doc_id")
EXPECTED_SCHEMA_FIELDS = {
    "id": ("VARCHAR", True, None),
    "doc_id": ("VARCHAR", False, None),
    "text": ("VARCHAR", False, None),
    "embedding": ("FLOAT_VECTOR", False, 768),
    "sparse_embedding": ("SPARSE_FLOAT_VECTOR", False, None),
}
EXPECTED_INDEXES = {
    "embedding": ("embedding", "FLAT", "IP"),
    "sparse_embedding": ("sparse_embedding", "SPARSE_INVERTED_INDEX", "BM25"),
}


def require_test_uri(uri: str) -> str:
    normalized = str(uri or "").strip().rstrip("/")
    if normalized != FAQ_TEST_MILVUS_URI:
        raise ValueError(f"qa_agent FAQ 迁移只允许 {FAQ_TEST_MILVUS_URI}，实际为 {normalized or '<empty>'}")
    return normalized


def require_connect_uri(uri: str) -> str:
    normalized = str(uri or "").strip().rstrip("/")
    if normalized not in {FAQ_TEST_MILVUS_URI, FAQ_INTERNAL_MILVUS_URI}:
        raise ValueError(f"FAQ 实际连接只允许测试 Milvus 或其容器别名，实际为 {normalized or '<empty>'}")
    return normalized


def _read_partition_rows(client: MilvusClient, partition_name: str) -> list[dict[str, Any]]:
    iterator = client.query_iterator(
        collection_name=FAQ_SOURCE_COLLECTION,
        partition_names=[partition_name],
        filter="",
        output_fields=list(SOURCE_FIELDS),
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


def _normalize_source_rows(partition_name: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        item = {key: clean_faq_text(row.get(key)) for key in SOURCE_FIELDS}
        for key in OPTIONAL_REFERENCE_FIELDS:
            if item[key].casefold() in {"none", "null"}:
                item[key] = ""
        missing = [key for key in ("id", "aq_id", "org_code", "question_name", "answer_desc", "text") if not item[key]]
        if missing:
            raise ValueError(f"{partition_name} 第 {index + 1} 行缺少字段：{', '.join(missing)}")
        if item["id"] != item["aq_id"]:
            raise ValueError(f"{partition_name} 第 {index + 1} 行 id 与 aq_id 不一致")
        if item["org_code"] != partition_name:
            raise ValueError(f"{partition_name} 第 {index + 1} 行 org_code={item['org_code']} 与 Partition 不一致")
        if item["aq_id"] in seen:
            raise ValueError(f"{partition_name} aq_id 重复：{item['aq_id']}")
        seen.add(item["aq_id"])
        normalized.append({
            "aq_id": item["aq_id"], "org_code": partition_name,
            "question_name": item["question_name"], "answer_desc": item["answer_desc"],
            "full_text": f"{item['question_name']}: {item['answer_desc']}", "doc_id": item["doc_id"],
            "document_id": item["document_id"], "ref_doc_id": item["ref_doc_id"],
        })
    return normalized


def _schema_summary(client: MilvusClient) -> dict[str, Any]:
    description = client.describe_collection(collection_name=FAQ_SOURCE_COLLECTION)
    fields = []
    for field in description.get("fields", []):
        params = {key: value for key, value in dict(field.get("params") or {}).items() if key != "analyzer_params"}
        fields.append({
            "name": str(field.get("name") or ""),
            "type": str(getattr(field.get("type"), "name", field.get("type"))),
            "params": params,
            "primary": bool(field.get("is_primary")),
        })
    indexes = []
    for name in client.list_indexes(collection_name=FAQ_SOURCE_COLLECTION):
        value = client.describe_index(collection_name=FAQ_SOURCE_COLLECTION, index_name=name)
        indexes.append({key: value.get(key) for key in ("index_name", "field_name", "index_type", "metric_type", "state")})
    return {
        "description": str(description.get("description") or ""),
        "enable_dynamic_field": bool(description.get("enable_dynamic_field")),
        "fields": fields,
        "indexes": indexes,
    }


def _validate_schema_summary(summary: dict[str, Any]) -> None:
    fields = {item["name"]: item for item in summary["fields"]}
    if set(fields) != set(EXPECTED_SCHEMA_FIELDS):
        raise ValueError(f"FAQ schema 字段漂移：actual={sorted(fields)}")
    for name, (data_type, primary, dimension) in EXPECTED_SCHEMA_FIELDS.items():
        field = fields[name]
        actual_dimension = int((field.get("params") or {}).get("dim") or 0) or None
        if (field["type"], field["primary"], actual_dimension) != (data_type, primary, dimension):
            raise ValueError(f"FAQ schema 字段漂移：{name}={field}")
    indexes = {item["index_name"]: item for item in summary["indexes"]}
    if set(indexes) != set(EXPECTED_INDEXES):
        raise ValueError(f"FAQ index 清单漂移：actual={sorted(indexes)}")
    for name, expected in EXPECTED_INDEXES.items():
        item = indexes[name]
        actual = (item["field_name"], item["index_type"], item["metric_type"])
        if actual != expected:
            raise ValueError(f"FAQ index 漂移：{name}={item}")


def inventory(client: MilvusClient, *, include_rows: bool = False) -> dict[str, Any]:
    collections = set(client.list_collections())
    if FAQ_SOURCE_COLLECTION not in collections:
        raise ValueError("测试 Milvus 中不存在固定源 Collection faq")
    actual_partitions = client.list_partitions(collection_name=FAQ_SOURCE_COLLECTION)
    expected_names = ["_default", *EXPECTED_PARTITIONS]
    if actual_partitions != expected_names and set(actual_partitions) != set(expected_names):
        raise ValueError(f"FAQ Partition 清单漂移：expected={expected_names}, actual={actual_partitions}")
    default_count = int(client.get_partition_stats(collection_name=FAQ_SOURCE_COLLECTION, partition_name="_default").get("row_count") or 0)
    if default_count != 0:
        raise ValueError(f"FAQ _default 必须为 0 行，实际为 {default_count}")
    reports: list[dict[str, Any]] = []
    normalized_by_org: dict[str, list[dict[str, str]]] = {}
    for org_code, expected_count in EXPECTED_PARTITIONS.items():
        rows = _read_partition_rows(client, org_code)
        normalized = _normalize_source_rows(org_code, rows)
        if len(normalized) != expected_count:
            raise ValueError(f"FAQ {org_code} 行数漂移：expected={expected_count}, actual={len(normalized)}")
        normalized_by_org[org_code] = normalized
        reports.append({"org_code": org_code, "row_count": len(normalized), "content_sha256": faq_rows_digest(normalized)})
    combined = [row for org_code in EXPECTED_PARTITIONS for row in normalized_by_org[org_code]]
    if len(combined) != EXPECTED_TOTAL:
        raise ValueError(f"FAQ 总行数漂移：expected={EXPECTED_TOTAL}, actual={len(combined)}")
    schema = _schema_summary(client)
    _validate_schema_summary(schema)
    report: dict[str, Any] = {
        "milvus_uri": FAQ_TEST_MILVUS_URI,
        "collection_name": FAQ_SOURCE_COLLECTION,
        "default_partition_count": default_count,
        "row_count": len(combined),
        "content_sha256": faq_rows_digest(combined),
        "schema": schema,
        "partitions": reports,
    }
    if include_rows:
        report["rows"] = normalized_by_org
    report["confirmation"] = confirmation_value(report)
    return report


def confirmation_value(report: dict[str, Any]) -> str:
    material = {
        "milvus_uri": report["milvus_uri"],
        "collection_name": report["collection_name"],
        "row_count": report["row_count"],
        "content_sha256": report["content_sha256"],
        "schema": report["schema"],
        "partitions": [(item["org_code"], item["row_count"], item["content_sha256"]) for item in report["partitions"]],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20].upper()
    return f"MIGRATE-QA-FAQ-{digest}"


def canonical_csv(rows: Iterable[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    columns = ["aq_id", "question_name", "answer_desc", "doc_id", "document_id", "ref_doc_id"]
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue().encode("utf-8-sig")


def _objects(settings: Settings):
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStore(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_bucket)
    return LocalObjectStore(settings.state_dir / "v7-objects")


def _provisioner(store: V7Store, connect_uri: str) -> ManagedCollectionProvisioner:
    return ManagedCollectionProvisioner(store, V7Milvus(connect_uri, os.getenv("DATAFORGE_MILVUS_TOKEN")))


def ensure_faq_type(store: V7Store, connect_uri: str) -> dict[str, Any]:
    with store.sessions() as session:
        existing = session.scalar(select(KnowledgeType).where(KnowledgeType.code == FAQ_TYPE_CODE))
    if not existing:
        created = store.create_knowledge_type(
            FAQ_TYPE_CODE, "qa_agent FAQ", "问", FAQ_SCHEMA, "full_text", ["aq_id"], "single",
            "qualityrev_default", [], managed_collection_name=FAQ_COLLECTION_NAME,
        )
        type_id = created["id"]
    else:
        type_id = existing.id
    with store.sessions() as session:
        current = session.get(KnowledgeType, type_id)
        revision = session.scalar(select(KnowledgeTypeRevision).where(
            KnowledgeTypeRevision.knowledge_type_id == type_id,
        ).order_by(KnowledgeTypeRevision.revision_no.desc())) if current else None
        bindings = [] if not revision else list(session.scalars(select(KnowledgeTypeIndexBinding).where(
            KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id,
        )))
        profile = session.get(KnowledgeIndexProfile, bindings[0].index_profile_id) if len(bindings) == 1 else None
        embedding = session.get(EmbeddingProfile, profile.embedding_profile_id) if profile else None
        mismatches = []
        if not revision:
            mismatches.append("revision")
        else:
            if revision.schema_json != FAQ_SCHEMA: mismatches.append("schema")
            if revision.canonical_field != "full_text": mismatches.append("canonical_field")
            if list(revision.identity_fields or []) != ["aq_id"]: mismatches.append("identity_fields")
            if revision.source_policy != "single": mismatches.append("source_policy")
        if not profile:
            mismatches.append("profile")
        else:
            if profile.code != FAQ_PROFILE_CODE: mismatches.append("profile_code")
            if profile.knowledge_type != FAQ_TYPE_CODE: mismatches.append("profile_type")
            if profile.collection_name != FAQ_COLLECTION_NAME: mismatches.append("collection")
        if not embedding:
            mismatches.append("embedding")
        else:
            if embedding.model != "bce-embedding-base": mismatches.append("embedding_model")
            if embedding.dimension != 768: mismatches.append("embedding_dimension")
        if mismatches:
            raise ValueError(
                "已存在 qa-agent-faq Type/Profile 的业务合同与固定迁移规格不一致："
                + ", ".join(mismatches)
            )
    requirements = store.knowledge_type_publication_requirements(type_id)
    if (
        len(requirements) != 1
        or requirements[0]["collection_name"] != FAQ_COLLECTION_NAME
        or requirements[0]["dimension"] != 768
    ):
        raise ValueError("qa-agent-faq Type/Profile/Collection 合同与固定迁移规格不一致")
    _provisioner(store, connect_uri).ensure_collection_for_profile(requirements[0]["profile_revision_id"])
    with store.sessions() as session:
        current = session.get(KnowledgeType, type_id)
        revision = session.get(KnowledgeTypeRevision, current.current_revision_id) if current and current.current_revision_id else None
        published = bool(current and current.status == "active" and revision and revision.status == "published")
    if not published:
        store.publish_knowledge_type(type_id)
    return store.validate_knowledge_type(type_id)


def ensure_faq_template(store: V7Store) -> dict[str, Any]:
    expected = faq_template_definition()
    with store.sessions() as session:
        template = session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == FAQ_TEMPLATE_CODE))
    if not template:
        created = store.create_flow_template(FAQ_TEMPLATE_CODE, "qa_agent FAQ 结构化文件", [FAQ_TYPE_CODE], expected)
        template_id = created["id"]
        return store.publish_flow_template(template_id)
    current = next(item for item in store.list_flow_templates() if item["id"] == template.id)
    if current["definition"] != expected:
        raise ValueError("qa-agent-faq-structured 模板已存在但定义不一致")
    if current["status"] != "active" or current["revision_status"] != "published":
        return store.publish_flow_template(template.id)
    return {"id": template.id, "status": "published", "revision": current["revision"]}


def prepare(store: V7Store, connect_uri: str) -> dict[str, Any]:
    return {"type": ensure_faq_type(store, connect_uri), "template": ensure_faq_template(store)}


def _document_library_code(org_code: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", org_code).strip("_")
    suffix = hashlib.sha256(org_code.encode()).hexdigest()[:8]
    return f"legacy-qa-faq-{safe}-{suffix}"[:120]


def prepare_empty_document_libraries(store: V7Store) -> list[dict[str, Any]]:
    """Idempotently create the 12 manual-upload targets without creating Sources."""
    with store.sessions() as session:
        template = session.scalar(select(KnowledgeFlowTemplate).where(
            KnowledgeFlowTemplate.code == FAQ_TEMPLATE_CODE,
        ))
        if not template or template.status != "active":
            raise ValueError("qa-agent-faq-structured 模板未发布")
        template_id = template.id

    results: list[dict[str, Any]] = []
    for org_code in EXPECTED_PARTITIONS:
        code = _document_library_code(org_code)
        name = f"qa_agent FAQ（{org_code}）"
        with store.sessions() as session:
            library = session.scalar(select(DocumentLibrary).where(DocumentLibrary.code == code))
        if not library:
            created = store.create_document_library(
                name,
                description=f"qa_agent FAQ 手工上传目标；文件名固定 faq-{org_code}.csv|xlsx",
                code=code,
            )
            library_id = created["id"]
        else:
            if library.name != name or library.status != "active":
                raise ValueError(f"FAQ 文档库合同不一致：{org_code}")
            library_id = library.id
        with store.sessions() as session:
            active_sources = list(session.scalars(select(Source).where(
                Source.document_library_id == library_id,
                Source.status != "deleted",
            )))
        if active_sources:
            raise ValueError(f"FAQ 文档库已存在活动 Source，拒绝预建：{org_code}")
        binding = store.bind_document_library_template(library_id, template_id)
        output = next(item for item in binding["outputs"] if item["output_key"] == FAQ_TYPE_CODE)
        result_library_id = output["knowledge_library"]["id"]
        with store.sessions.begin() as session:
            result_library = session.get(KnowledgeLibrary, result_library_id)
            result_library.migration_status = "migrating"
        results.append({
            "org_code": org_code,
            "filename": f"faq-{org_code}.csv",
            "document_library_id": library_id,
            "document_library_code": code,
            "knowledge_library_id": result_library_id,
            "partition_name": output["knowledge_library"]["partition_name"],
        })
    return results


def _ensure_document_source(store: V7Store, objects, org_code: str, payload: bytes) -> tuple[dict[str, Any], bool]:
    code = _document_library_code(org_code)
    filename = f"faq-{org_code}.csv"
    with store.sessions() as session:
        library = session.scalar(select(DocumentLibrary).where(DocumentLibrary.code == code))
    if not library:
        library_payload = store.create_document_library(
            f"qa_agent FAQ（{org_code}）",
            description=f"从测试 Milvus faq/{org_code} 迁入；每机构一个权威文件",
            code=code,
        )
        library_id = library_payload["id"]
    else:
        library_id = library.id
    with store.sessions() as session:
        sources = list(session.scalars(select(Source).where(Source.document_library_id == library_id, Source.status != "deleted")))
        if len(sources) > 1:
            raise ValueError(f"{org_code} FAQ 文档库存在多个活动 Source")
        source = sources[0] if sources else None
        current = session.get(SourceVersion, source.current_version_id) if source and source.current_version_id else None
    sha256 = hashlib.sha256(payload).hexdigest()
    if current and current.sha256 == sha256:
        source_payload = {"id": source.id, "current_version_id": current.id, "sha256": current.sha256}
        changed = False
    else:
        key = f"legacy-import/qa-agent-faq/{org_code}/{sha256}.csv"
        stored = objects.put_bytes(key, payload, "text/csv; charset=utf-8")
        if source:
            source_payload = store.replace_source(
                source_id=source.id, filename=filename, object_key=stored.key,
                sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="text/csv",
            )
        else:
            source_payload = store.create_source(
                library_id=library_id, name=f"FAQ {org_code}", filename=filename,
                object_key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes,
                mime_type="text/csv", metadata={"org_code": org_code, "legacy_collection": FAQ_SOURCE_COLLECTION},
                relative_path=filename,
            )
        changed = True
    with store.sessions() as session:
        template = session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == FAQ_TEMPLATE_CODE))
        if not template or template.status != "active":
            raise ValueError("qa-agent-faq-structured 模板尚未发布")
    binding = store.bind_document_library_template(library_id, template.id)
    output = next(item for item in binding["outputs"] if item["output_key"] == FAQ_TYPE_CODE)
    with store.sessions.begin() as session:
        knowledge_library = session.get(KnowledgeLibrary, output["knowledge_library"]["id"])
        if changed or knowledge_library.migration_status != "ready":
            knowledge_library.migration_status = "migrating"
    return {
        "document_library_id": library_id,
        "knowledge_library_id": output["knowledge_library"]["id"],
        "partition_name": output["knowledge_library"]["partition_name"],
        "source": source_payload,
    }, changed


def _wait_knowledge_job(store: V7Store, job_id: str, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job.status == "completed":
            return {"job_id": job.id, "status": job.status}
        if job.status in {"failed", "cancelled"}:
            raise RuntimeError(f"FAQ 知识任务失败：job={job.id}, status={job.status}, error={job.error}")
        time.sleep(2)
    raise TimeoutError(f"FAQ 知识任务等待超时：{job_id}")


def _wait_vector_ready(store: V7Store, library_id: str, deadline: float) -> list[dict[str, Any]]:
    requeued = False
    while time.monotonic() < deadline:
        status = store.vector_status(library_id)
        if status["ready"]:
            return list(status["jobs"])
        active = [item for item in status["jobs"] if item["status"] in {"queued", "running"}]
        if not active and not requeued:
            store.create_vector_sync_jobs(library_id)
            requeued = True
        time.sleep(2)
    raise TimeoutError(f"FAQ Vector Sync 等待超时：{library_id}")


def import_documents(store: V7Store, objects, connect_uri: str, rows_by_org: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    from .servings import EmbeddingServingRegistry, ServingManager
    EmbeddingServingRegistry(ServingManager(
        store.sessions, Settings.load().config_encryption_key,
    )).require(healthy=True)
    timeout_seconds = max(60, int(os.getenv("DATAFORGE_QA_FAQ_IMPORT_TIMEOUT_SECONDS", "3600")))
    reports: list[dict[str, Any]] = []
    for org_code in EXPECTED_PARTITIONS:
        asset, changed = _ensure_document_source(store, objects, org_code, canonical_csv(rows_by_org[org_code]))
        jobs = store.process_document_library(asset["document_library_id"])
        deadline = time.monotonic() + timeout_seconds
        executed = [_wait_knowledge_job(store, job["id"], deadline) for job in jobs]
        vector_jobs = _wait_vector_ready(store, asset["knowledge_library_id"], deadline)
        reports.append({
            "org_code": org_code,
            **asset,
            "source_changed": changed,
            "jobs": executed,
            "vector_jobs": vector_jobs,
        })
    return reports


def _target_rows(client: MilvusClient, partition_name: str) -> list[dict[str, Any]]:
    iterator = client.query_iterator(
        collection_name=FAQ_COLLECTION_NAME, partition_names=[partition_name], filter="",
        output_fields=["source_knowledge_id", "content", "data"], batch_size=1000,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            for value in batch:
                data = value.get("data") or {}
                if isinstance(data, str):
                    data = json.loads(data)
                rows.append({
                    **dict(data),
                    "_source_knowledge_id": clean_faq_text(value.get("source_knowledge_id")),
                    "_canonical_content": clean_faq_text(value.get("content")),
                })
    finally:
        iterator.close()
    return rows


def verify(store: V7Store, client: MilvusClient, rows_by_org: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for org_code, expected_rows in rows_by_org.items():
        code = _document_library_code(org_code)
        with store.sessions() as session:
            document_library = session.scalar(select(DocumentLibrary).where(DocumentLibrary.code == code))
            if not document_library:
                raise ValueError(f"FAQ {org_code} 文档库不存在")
            output = session.scalar(
                select(DocumentLibraryTemplateOutput)
                .join(DocumentLibraryTemplateBinding,
                      DocumentLibraryTemplateOutput.document_library_template_binding_id == DocumentLibraryTemplateBinding.id)
                .where(
                    DocumentLibraryTemplateBinding.document_library_id == document_library.id,
                    DocumentLibraryTemplateOutput.knowledge_type == FAQ_TYPE_CODE,
                )
            )
            if not output:
                raise ValueError(f"FAQ {org_code} 自动结果知识库不存在")
            library = session.get(KnowledgeLibrary, output.knowledge_library_id)
            mysql_items = list(session.scalars(select(KnowledgeItem).where(
                KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active",
            )))
            mysql_rows = [{
                **dict(item.data_json or {}),
                "_source_knowledge_id": item.source_knowledge_id,
                "_canonical_content": item.canonical_content,
            } for item in mysql_items]
            partition_name = library.partition_name
        target_rows = _target_rows(client, partition_name)
        expected_hash, mysql_hash, target_hash = (faq_rows_digest(value) for value in (expected_rows, mysql_rows, target_rows))
        expected_ids = {f"faq:{row['aq_id']}" for row in expected_rows}
        expected_content = {f"faq:{row['aq_id']}": row["full_text"] for row in expected_rows}
        mysql_ids = {row["_source_knowledge_id"] for row in mysql_rows}
        target_ids = {row["_source_knowledge_id"] for row in target_rows}
        mysql_content = {row["_source_knowledge_id"]: row["_canonical_content"] for row in mysql_rows}
        target_content = {row["_source_knowledge_id"]: row["_canonical_content"] for row in target_rows}
        ready = (
            len(expected_rows) == len(mysql_rows) == len(target_rows)
            and expected_hash == mysql_hash == target_hash
            and expected_ids == mysql_ids == target_ids
            and expected_content == mysql_content == target_content
            and store.vector_status(library.id)["ready"]
        )
        if not ready:
            raise ValueError(f"FAQ {org_code} 四层验证失败")
        with store.sessions.begin() as session:
            current = session.get(KnowledgeLibrary, library.id)
            current.migration_status = "ready"
        results.append({
            "org_code": org_code, "document_library_id": document_library.id,
            "knowledge_library_id": library.id, "partition_name": partition_name,
            "row_count": len(expected_rows), "content_sha256": expected_hash, "ready": True,
        })
    return results


def _load_store_and_objects(database_url: str):
    settings = Settings.load()
    store = V7Store(database_url or settings.platform_database_url)
    store.assert_schema_current()
    return store, _objects(settings)


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "rows"}


def main() -> None:
    parser = argparse.ArgumentParser(description="qa_agent FAQ 固定测试迁移")
    parser.add_argument("action", choices=("inventory", "dry-run", "prepare", "import", "verify"))
    parser.add_argument("--milvus-uri", default=FAQ_TEST_MILVUS_URI)
    parser.add_argument("--connect-uri", default=os.getenv("DATAFORGE_MILVUS_URI", FAQ_TEST_MILVUS_URI))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    require_test_uri(args.milvus_uri)
    connect_uri = require_connect_uri(args.connect_uri)
    client = MilvusClient(uri=connect_uri, token=os.getenv("DATAFORGE_MILVUS_TOKEN") or None)
    source = inventory(client, include_rows=args.action in {"import", "verify"})
    if args.action in {"inventory", "dry-run"}:
        print(json.dumps(_public_report(source), ensure_ascii=False, indent=2, default=str))
        return
    if args.confirm != source["confirmation"]:
        raise SystemExit(f"确认值不匹配；先运行 dry-run，当前需要：{source['confirmation']}")
    store, objects = _load_store_and_objects(args.database_url)
    if args.action == "prepare":
        prepared = prepare(store, connect_uri)
        print(json.dumps({"prepared": prepared, "confirmation": source["confirmation"]}, ensure_ascii=False, indent=2))
        return
    imported: list[dict[str, Any]] = []
    if args.action == "import":
        prepared = prepare(store, connect_uri)
        imported = import_documents(store, objects, connect_uri, source["rows"])
    else:
        prepared = {}
    verified = verify(store, client, source["rows"])
    print(json.dumps({
        "prepared": prepared, "imported": imported, "verified": verified,
        "source": _public_report(source), "routing_published": False,
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
