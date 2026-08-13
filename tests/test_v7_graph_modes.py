from __future__ import annotations

from pathlib import Path

import pytest

from dataforge.v7.flow import FlowCompiler, FlowValidationError
from dataforge.v7.migrations import upgrade
from dataforge.v7.provisioning import ManagedCollectionProvisioner
from dataforge.v7.store import DEFAULT_INDEX_FIELD_MAPPING, STORAGE_CONTRACT_SEEDS, V7Store


def store(tmp_path: Path) -> V7Store:
    value = V7Store(f"sqlite:///{tmp_path / 'graph-modes.sqlite3'}")
    upgrade(str(value.engine.url))
    value.seed()
    return value


def test_graph_modes_seed_dedicated_managed_collections_and_keep_legacy(tmp_path: Path):
    value = store(tmp_path)
    profiles = {item["code"]: item for item in value.list_index_profiles()}
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


def test_new_graph_libraries_freeze_mode_specific_profiles(tmp_path: Path):
    value = store(tmp_path)
    triple = value.create_knowledge_library("三元组", "graph", graph_mode="triple")
    semantic = value.create_knowledge_library("语义", "graph", graph_mode="semantic")
    assert triple["graph_mode"] == "triple"
    assert semantic["graph_mode"] == "semantic"
    assert [item.code for item in value.index_profiles_for_library(triple["id"])[1]] == ["graph-triple"]
    assert [item.code for item in value.index_profiles_for_library(semantic["id"])[1]] == ["graph-semantic"]
    with pytest.raises(ValueError, match="graph_mode"):
        value.create_knowledge_library("错误", "text", graph_mode="semantic")

    # A migrated pre-mode library is backfilled as triple but must retain its
    # explicitly frozen legacy graph profile and Collection.
    from sqlalchemy import select
    from dataforge.v7.models import KnowledgeIndexProfile, KnowledgeLibrary, KnowledgeType, KnowledgeTypeRevision
    with value.sessions.begin() as session:
        legacy_profile = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == "graph"))
        graph_type = session.scalar(select(KnowledgeType).where(KnowledgeType.code == "graph"))
        legacy_type_revision = session.scalar(select(KnowledgeTypeRevision).where(
            KnowledgeTypeRevision.knowledge_type_id == graph_type.id,
            KnowledgeTypeRevision.revision_no == 1,
        ))
        persisted = session.get(KnowledgeLibrary, triple["id"])
        persisted.knowledge_type_revision_id = legacy_type_revision.id
        persisted.index_profile_id = legacy_profile.id
    assert [item.code for item in value.index_profiles_for_library(triple["id"])[1]] == ["graph"]


def test_managed_profile_reuses_only_an_identical_storage_contract(tmp_path: Path):
    value = store(tmp_path)
    compatible = value.create_index_profile(
        "text-compatible", "text", "", "bce_base_768_v1", "bce-embedding-base", 768, "COSINE", None,
        DEFAULT_INDEX_FIELD_MAPPING, collection_policy="managed",
        storage_schema=STORAGE_CONTRACT_SEEDS["text"]["schema"],
    )
    profiles = {item["code"]: item for item in value.list_index_profiles()}
    assert profiles["text-compatible"]["revisions"][0]["collection_name"] == "dataforge_text_knowledge"
    assert len(value.list_managed_collections()) == 5

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
