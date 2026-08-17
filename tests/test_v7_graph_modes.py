from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataforge.config import Settings
from dataforge.v7.flow import FlowCompiler, FlowValidationError
from dataforge.v7.migrations import upgrade
from dataforge.v7.provisioning import ManagedCollectionDeletionService, ManagedCollectionProvisioner
from dataforge.v7.models import KnowledgeIndexProfile, KnowledgeIndexProfileRevision, KnowledgeType, KnowledgeTypeIndexBinding, KnowledgeTypeModeRevision, KnowledgeTypeRevision, ManagedCollection, ProjectOrgRoute
from dataforge.v7.store import DEFAULT_INDEX_FIELD_MAPPING, STORAGE_CONTRACT_SEEDS, V7Store
from dataforge.v7.vector import V7Milvus, VectorSyncService
from dataforge.v7.web import create_app


def store(tmp_path: Path) -> V7Store:
    value = V7Store(f"sqlite:///{tmp_path / 'graph-modes.sqlite3'}")
    upgrade(str(value.engine.url))
    value.seed()
    return value


def mark_managed_collection_ready(value: V7Store, collection_id: str) -> None:
    with value.sessions.begin() as session:
        collection = session.get(ManagedCollection, collection_id)
        collection.status = "ready"
        collection.observed_spec_hash = collection.desired_spec_hash


def test_graph_modes_keep_legacy_profile_but_skip_its_capacity_check(tmp_path: Path):
    value = store(tmp_path)
    profiles = {item["code"]: item for item in value.list_index_profiles()}
    assert set(profiles) == {"text", "qa-question", "qa-full", "graph", "graph-triple", "graph-semantic"}
    assert profiles["graph"]["collection_name"] == "dataforge_graph_knowledge"
    assert profiles["graph"]["revisions"][0]["collection_policy"] == "external"
    assert profiles["graph-triple"]["collection_name"] == "dataforge_graph_triple_knowledge"
    assert profiles["graph-semantic"]["collection_name"] == "dataforge_graph_semantic_knowledge"
    collections = {item["collection_name"]: item for item in value.list_managed_collections()}
    assert set(collections) == {
        "dataforge_text_knowledge", "dataforge_qa_question", "dataforge_qa_full",
        "dataforge_graph_triple_knowledge", "dataforge_graph_semantic_knowledge",
    }
    assert collections["dataforge_graph_triple_knowledge"]["desired_spec_hash"] != collections["dataforge_graph_semantic_knowledge"]["desired_spec_hash"]
    assert all(item["status"] == "planned" for item in collections.values())

    class FakeMilvus:
        def __init__(self):
            self.names = []

        def capacity(self, collection_name):
            self.names.append(collection_name)
            return type("Capacity", (), {
                "collection_name": collection_name, "entity_count": 0, "capacity_limit": None,
                "threshold": 0.8, "alert": False,
            })()

    fake = FakeMilvus()
    report = VectorSyncService(value, milvus=fake).capacity_report()
    assert {item["collection_name"] for item in report} == {item["collection_name"] for item in profiles.values()}
    assert "dataforge_graph_knowledge" not in fake.names
    skipped = next(item for item in report if item["collection_name"] == "dataforge_graph_knowledge")
    assert skipped == {
        "collection_name": "dataforge_graph_knowledge",
        "available": False,
        "reason": "旧外部 Profile，不参与容量监控",
    }

    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(value.engine.url))
    response = TestClient(create_app(settings)).get("/api/developer/vector-indexes")
    assert response.status_code == 200
    assert {item["code"] for item in response.json()["profiles"]} == set(profiles)
    capacity = {item["collection_name"]: item for item in response.json()["capacity"]}
    assert capacity["dataforge_graph_knowledge"]["reason"] == "旧外部 Profile，不参与容量监控"

    custom = value.create_index_profile(
        "external-custom", "text", "external_custom_collection", "bce_base_768_v1",
        "bce-embedding-base", 768, "COSINE", None, DEFAULT_INDEX_FIELD_MAPPING,
        collection_policy="external",
    )
    with value.sessions.begin() as session:
        profile = session.get(KnowledgeIndexProfile, custom["id"])
        profile.status = "active"
        profile.current_revision_id = custom["revision_id"]
        session.get(KnowledgeIndexProfileRevision, custom["revision_id"]).status = "published"
    fake.names.clear()
    VectorSyncService(value, milvus=fake).capacity_report()
    assert "external_custom_collection" in fake.names


def test_new_graph_libraries_freeze_mode_specific_profiles(tmp_path: Path):
    value = store(tmp_path)
    from sqlalchemy import select

    with value.sessions() as session:
        graph_type = session.scalar(select(KnowledgeType).where(KnowledgeType.code == "graph"))
        graph_revision = session.get(KnowledgeTypeRevision, graph_type.current_revision_id)
        bindings = session.scalars(select(KnowledgeTypeIndexBinding).where(
            KnowledgeTypeIndexBinding.knowledge_type_revision_id == graph_revision.id,
        )).all()
        profile_codes = {
            session.get(KnowledgeIndexProfile, item.index_profile_id).code
            for item in bindings
        }
        modes = set(session.scalars(select(KnowledgeTypeModeRevision.mode).where(
            KnowledgeTypeModeRevision.knowledge_type_revision_id == graph_revision.id,
        )))
    assert graph_revision.revision_no == 2
    assert profile_codes == {"graph-triple", "graph-semantic"}
    assert modes == {"triple", "semantic"}

    triple = value.create_knowledge_library("三元组", "graph", graph_mode="triple")
    semantic = value.create_knowledge_library("语义", "graph", graph_mode="semantic")
    assert triple["graph_mode"] == "triple"
    assert semantic["graph_mode"] == "semantic"
    assert [item.code for item in value.index_profiles_for_library(triple["id"])[1]] == ["graph-triple"]
    assert [item.code for item in value.index_profiles_for_library(semantic["id"])[1]] == ["graph-semantic"]
    with pytest.raises(ValueError, match="graph_mode"):
        value.create_knowledge_library("错误", "text", graph_mode="semantic")

    # A migrated pre-mode library is backfilled as triple but retains its
    # explicitly frozen legacy graph Profile and external Collection.
    from dataforge.v7.models import KnowledgeLibrary
    with value.sessions.begin() as session:
        legacy_profile = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == "graph"))
        legacy_type_revision = session.scalar(select(KnowledgeTypeRevision).where(
            KnowledgeTypeRevision.knowledge_type_id == graph_type.id,
            KnowledgeTypeRevision.revision_no == 1,
        ))
        persisted = session.get(KnowledgeLibrary, triple["id"])
        persisted.knowledge_type_revision_id = legacy_type_revision.id
        persisted.index_profile_id = legacy_profile.id
    assert [item.code for item in value.index_profiles_for_library(triple["id"])[1]] == ["graph"]


def test_managed_profile_defaults_to_an_independent_collection_and_can_explicitly_reuse(tmp_path: Path):
    value = store(tmp_path)
    compatible = value.create_index_profile(
        "text-compatible", "text", "", "bce_base_768_v1", "bce-embedding-base", 768, "COSINE", None,
        DEFAULT_INDEX_FIELD_MAPPING, collection_policy="managed",
        storage_schema=STORAGE_CONTRACT_SEEDS["text"]["schema"],
    )
    profiles = {item["code"]: item for item in value.list_index_profiles()}
    assert profiles["text-compatible"]["revisions"][0]["collection_name"] == "dataforge_text_compatible_knowledge"
    assert len(value.list_managed_collections()) == 6

    from dataforge.v7.models import ManagedCollection
    from sqlalchemy import select
    with value.sessions.begin() as session:
        text_collection = session.scalar(select(ManagedCollection).where(
            ManagedCollection.collection_name == "dataforge_text_knowledge",
        ))
        text_collection.status = "ready"
        text_collection.observed_spec_hash = text_collection.desired_spec_hash
        reusable_id = text_collection.id
    reused = value.create_index_profile(
        "text-reused", "text", "", "bce_base_768_v1", "bce-embedding-base", 768, "COSINE", None,
        DEFAULT_INDEX_FIELD_MAPPING, collection_policy="managed",
        storage_schema=STORAGE_CONTRACT_SEEDS["text"]["schema"],
        reuse_managed_collection_id=reusable_id,
    )
    assert reused["collection_name"] == "dataforge_text_knowledge"
    assert len(value.list_managed_collections()) == 6

    changed = {"fields": [*STORAGE_CONTRACT_SEEDS["text"]["schema"]["fields"],
                           {"name": "section", "type": "VARCHAR", "max_length": 512, "nullable": True}]}
    value.create_index_profile(
        "text-section", "text", "", "bce_base_768_v1", "bce-embedding-base", 768, "COSINE", None,
        DEFAULT_INDEX_FIELD_MAPPING, collection_policy="managed", storage_schema=changed,
    )
    assert {item["collection_name"] for item in value.list_managed_collections()} >= {
        "dataforge_text_knowledge", "dataforge_text_section_knowledge",
    }
    assert compatible["status"] == "draft"


def test_manual_create_is_managed_while_attach_only_validates_external_collection(tmp_path: Path):
    value = store(tmp_path)
    managed = value.create_index_profile(
        "manual-managed", "text", "manual_managed_collection", "bce_base_768_v1",
        "bce-embedding-base", 768, "COSINE", None, DEFAULT_INDEX_FIELD_MAPPING,
        collection_mode="create", storage_schema=STORAGE_CONTRACT_SEEDS["text"]["schema"],
    )
    external = value.create_index_profile(
        "manual-external", "text", "customer_existing_collection", "bce_base_768_v1",
        "bce-embedding-base", 768, "COSINE", None, DEFAULT_INDEX_FIELD_MAPPING,
        collection_mode="attach",
    )
    assert managed["collection_policy"] == "managed" and managed["managed_collection_id"]
    assert external["collection_policy"] == "external" and external["managed_collection_id"] is None
    calls: list[str] = []
    value.publish_index_profile(external["id"], lambda name, fields, dimension: calls.append(name))
    mark_managed_collection_ready(value, managed["managed_collection_id"])
    value.publish_index_profile(managed["id"], lambda *_: calls.append("unexpected"))
    assert calls == ["customer_existing_collection"]


def test_type_revision_rejects_two_profiles_pointing_to_the_same_collection(tmp_path: Path):
    value = store(tmp_path)
    manual = value.create_index_profile(
        "duplicate-manual", "duplicate-type", "dataforge_duplicate_type_knowledge", "bce_base_768_v1",
        "bce-embedding-base", 768, "COSINE", None, DEFAULT_INDEX_FIELD_MAPPING,
        collection_mode="attach",
    )
    value.publish_index_profile(manual["id"], lambda *_: None)
    with pytest.raises(ValueError, match="同一 Type Revision"):
        value.create_knowledge_type(
            "duplicate-type", "重复集合类型", "重", {"type": "object", "required": ["title"]},
            "title", ["title"], "single", "qualityrev_default", [manual["id"]],
        )


def test_managed_collection_delete_requires_archived_profile_and_owned_collection(tmp_path: Path):
    value = store(tmp_path)
    profile = value.create_index_profile(
        "temporary-manual", "text", "temporary_manual_collection", "bce_base_768_v1",
        "bce-embedding-base", 768, "COSINE", None, DEFAULT_INDEX_FIELD_MAPPING,
        collection_mode="create", storage_schema=STORAGE_CONTRACT_SEEDS["text"]["schema"],
    )
    mark_managed_collection_ready(value, profile["managed_collection_id"])

    class FakeMilvus:
        dropped: list[str] = []

        def inspect_managed_collection(self, collection_name, expected_description):
            return {"exists": True, "description": expected_description, "ownership_valid": True,
                    "partitions": ["_default"], "entity_count": 0}

        def drop_managed_collection(self, collection_name, expected_description):
            self.dropped.append(collection_name)
            return True

    service = ManagedCollectionDeletionService(value, FakeMilvus())
    assert {item["code"] for item in service.preflight(profile["managed_collection_id"])["blockers"]} == {"profile_ids"}
    value.archive_index_profile(profile["id"])
    assert service.preflight(profile["managed_collection_id"])["deletable"]
    requested = service.request_delete(profile["managed_collection_id"])
    job = value.claim_managed_collection_deletion_job("test-worker")
    assert job.id == requested["id"]
    assert service.run(job.id)["status"] == "completed"
    assert service.milvus.dropped == ["temporary_manual_collection"]
    collection = next(item for item in value.list_managed_collections() if item["id"] == profile["managed_collection_id"])
    assert collection["status"] == "deleted"


def test_managed_collection_preflight_reports_all_active_database_references(tmp_path: Path):
    value = store(tmp_path)
    target = next(item for item in value.list_managed_collections() if item["collection_name"] == "dataforge_text_knowledge")
    document_library = value.create_document_library("受管删除引用")
    binding = value.bind_document_library_template(document_library["id"], "flow_standard-text")
    library_id = binding["outputs"][0]["knowledge_library"]["id"]
    project = value.create_project("受管删除项目")
    task = value.create_project_task(project["id"], "route", "受管删除路由")
    route = value.put_route(task["id"], "general", [library_id])
    with value.sessions.begin() as session:
        session.get(ProjectOrgRoute, route["id"]).status = "published"
    value.create_vector_sync_jobs(library_id)

    class FakeMilvus:
        def inspect_managed_collection(self, collection_name, expected_description):
            return {"exists": True, "description": expected_description, "ownership_valid": True,
                    "partitions": ["_default", f"kl_{library_id}"], "entity_count": 7}

    check = ManagedCollectionDeletionService(value, FakeMilvus()).preflight(target["id"])
    assert {item["code"] for item in check["blockers"]} >= {
        "profile_ids", "type_ids", "knowledge_libraries", "template_binding_count",
        "route_count", "running_vector_job_count",
    }
    assert check["references"]["profiles"] and check["references"]["type_revisions"]
    assert check["references"]["template_bindings"] and check["references"]["routes"]
    assert check["references"]["vector_jobs"]
    assert check["observed"]["entity_count"] == 7


def test_milvus_and_http_have_no_external_whole_collection_delete_path(tmp_path: Path):
    class Client:
        description = "foreign"
        partitions = ["_default"]
        dropped = False

        def has_collection(self, **kwargs): return True
        def describe_collection(self, **kwargs): return {"description": self.description, "fields": []}
        def list_partitions(self, **kwargs): return self.partitions
        def get_collection_stats(self, **kwargs): return {"row_count": 0}
        def drop_collection(self, **kwargs): self.dropped = True

    milvus = V7Milvus("unused")
    milvus._client = Client()
    with pytest.raises(ValueError, match="ownership marker"):
        milvus.drop_managed_collection("customer", "dataforge-managed:expected")
    milvus._client.description = "dataforge-managed:expected"
    milvus._client.partitions = ["_default", "customer_partition"]
    with pytest.raises(ValueError, match="非 DataForge Partition"):
        milvus.drop_managed_collection("customer", "dataforge-managed:expected")
    assert not milvus._client.dropped

    value = store(tmp_path)
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(value.engine.url))
    client = TestClient(create_app(settings))
    response = client.post("/api/developer/index-profiles", json={
        "code": "api-external", "knowledge_type": "text", "collection_mode": "attach",
        "collection_name": "customer_api_collection", "embedding_code": "bce_base_768_v1",
        "embedding_model": "bce-embedding-base", "dimension": 768, "metric_type": "COSINE",
        "fields": DEFAULT_INDEX_FIELD_MAPPING,
    })
    assert response.status_code == 201 and response.json()["collection_policy"] == "external"
    assert client.delete("/api/developer/index-profiles/api-external/collection").status_code == 404


def test_provisioner_is_idempotent_and_rejects_foreign_same_name(tmp_path: Path):
    value = store(tmp_path)

    class FakeMilvus:
        descriptions: dict[str, str] = {}

        def ensure_managed_collection(self, collection_name, schema_spec, dimension, metric_type, index_spec, description):
            return self.descriptions.setdefault(collection_name, description)

    fake = FakeMilvus()
    service = ManagedCollectionProvisioner(value, fake)
    assert {item["status"] for item in service.reconcile()} == {"ready"}
    assert {item["status"] for item in service.reconcile()} == {"ready"}
    target = value.list_managed_collections()[0]
    fake.descriptions[target["collection_name"]] = "foreign-collection"
    assert service.reconcile_one(target["id"])["status"] == "incompatible"


def test_flow_v3_supports_typed_many_merge_and_rejects_isolated_nodes():
    catalog = {
        "root": {"code": "root", "input": "source_file", "output": "candidate:text", "adapter_code": "root", "exposure": "canvas", "enabled": True},
        "merge": {"code": "merge", "input": "candidate:*", "output": "candidate:*", "adapter_code": "merge", "exposure": "canvas", "enabled": True,
                  "input_ports": {"input": {"artifact_type": "candidate:*", "cardinality": "many"}},
                  "output_ports": {"output": {"artifact_type": "candidate:*", "cardinality": "many"}}},
    }
    definition = {"schema_version": 3, "nodes": [
        {"id": "a", "kind": "operator", "ref": "root"}, {"id": "b", "kind": "operator", "ref": "root"},
        {"id": "merge", "kind": "operator", "ref": "merge"},
        {"id": "sink", "kind": "knowledge_sink", "knowledge_type": "text", "output_key": "text"},
    ], "edges": [
        {"source": "a", "source_port": "output", "target": "merge", "target_port": "input"},
        {"source": "b", "source_port": "output", "target": "merge", "target_port": "input"},
        {"source": "merge", "source_port": "output", "target": "sink", "target_port": "input"},
    ]}
    compiled = FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "r1"}}).compile(definition)
    assert compiled["compiled_definition"]["schema_version"] == 3
    assert compiled["compiled_definition"]["sink_types"] == {"sink": "text"}
    bad = {"schema_version": 3, "nodes": definition["nodes"] + [{"id": "alone", "kind": "operator", "ref": "root"}], "edges": definition["edges"]}
    with pytest.raises(FlowValidationError, match="孤立节点"):
        FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "r1"}}).compile(bad)

    wrong_port = {**definition, "edges": [{**definition["edges"][0], "target_port": "missing"}, *definition["edges"][1:]]}
    with pytest.raises(FlowValidationError, match="不存在输入端口"):
        FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "r1"}}).compile(wrong_port)

    non_sink_terminal = {**definition, "nodes": [*definition["nodes"], {"id": "tail", "kind": "operator", "ref": "merge"}],
                         "edges": [*definition["edges"], {"source": "merge", "target": "tail"}]}
    with pytest.raises(FlowValidationError, match="所有终点"):
        FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "r1"}}).compile(non_sink_terminal)

    duplicate_sink = {**definition, "nodes": [*definition["nodes"], {"id": "sink2", "kind": "knowledge_sink", "knowledge_type": "text", "output_key": "text"}],
                      "edges": [*definition["edges"], {"source": "merge", "target": "sink2"}]}
    with pytest.raises(FlowValidationError, match="必须且只能"):
        FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "r1"}}).compile(duplicate_sink)
