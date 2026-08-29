from __future__ import annotations

import base64
import importlib.util
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from dataforge.config import Settings
from dataforge.v7.instance import InstanceContext
from dataforge.v7.local_config import LocalMilvusConfigurationService
from dataforge.v7.migration.exporter import MigrationExporter
from dataforge.v7.migration.importer import MigrationImporter
from dataforge.v7.migration.package import MigrationPackageBuilder, inspect_package
from dataforge.v7.migration.manifest import validate_manifest
from dataforge.v7.migration.planner import InstitutionReleasePlanner, MigrationPlanner
from dataforge.v7.migration.verifier import ActivationPreflightVerifier
from dataforge.v7.migrations import upgrade
from dataforge.v7.models import ImportedRouteCandidate, KnowledgeAssetVersion, KnowledgeAssetItem, KnowledgeItemSource, SourceReviewSnapshot, SourceVersion
from dataforge.v7.storage import LocalObjectStore
from dataforge.v7.store import V7Store
from dataforge.v7.vector import V7Milvus
from dataforge.v7 import web as v7_web
from dataforge.v7.web import create_app


def seeded_qa_project(store: V7Store) -> dict:
    return next(item for item in store.list_projects() if item["code"] == "qa-agent")


def record_and_approve_source(store: V7Store, source: dict, content: str = "迁移测试来源") -> None:
    preparation = store.claim_source_preparation_job(f"migration-{source['version']['id']}")
    assert preparation and preparation.source_version_id == source["version"]["id"]
    run = store.start_source_preparation_flow_run(preparation.id)
    store.record_source_chunks(run["id"], [{
        "source_version_id": source["version"]["id"],
        "source_chunk_id": f"chunk-{source['version']['id']}",
        "chunk_index": 0,
        "content": content,
        "anchor": {"chunk_index": 0},
    }])
    store.finish_flow_run(run["id"], status="completed")
    store.finish_source_preparation(preparation.id)
    store.approve_source_version(source["version"]["id"])


def test_mysql_deployment_migration_backfills_text_without_server_default(monkeypatch):
    migration_path = (
        Path(__file__).parents[1]
        / "src"
        / "dataforge"
        / "v7"
        / "alembic"
        / "versions"
        / "20260817_01_deployment_migration.py"
    )
    spec = importlib.util.spec_from_file_location("v7_deployment_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Inspector:
        def get_columns(self, table):
            if table == "project_tasks":
                return [{"name": "id"}, {"name": "knowledge_type"}]
            return [
                {"name": "flow_run_id", "nullable": True},
                {"name": "origin_flow_run_id", "nullable": True},
            ]

    added, altered, executed = [], [], []

    class Batch:
        def __init__(self, table):
            self.table = table

        def add_column(self, column):
            added.append((self.table, column))

        def create_index(self, *_args, **_kwargs):
            pass

        def alter_column(self, column, **kwargs):
            altered.append((self.table, column, kwargs))

    @contextmanager
    def batch_alter_table(table):
        yield Batch(table)

    monkeypatch.setattr(migration.op, "batch_alter_table", batch_alter_table)
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))

    migration._add_task_and_chunk_columns(Inspector())

    assert len(added) == 1
    table, column = added[0]
    assert table == "project_tasks"
    assert column.name == "description"
    assert column.nullable is True
    assert column.server_default is None
    assert executed == ["UPDATE project_tasks SET description='' WHERE description IS NULL"]
    assert len(altered) == 1
    table, column_name, kwargs = altered[0]
    assert (table, column_name) == ("project_tasks", "description")
    assert isinstance(kwargs["existing_type"], migration.sa.Text)
    assert kwargs["existing_nullable"] is True
    assert kwargs["nullable"] is False


def keys():
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def manifest():
    return {"format": "dataforge-migration", "schema_version": 1, "package_kind": "deployment_seed",
            "package_id": "pkg-one", "source_instance_id": "central-one",
            "project": {"id": "project-one", "code": "qa-agent"},
            "deployment": {"id": "deploy-a", "code": "hospital-a"},
            "scope": {"deployment_count": 1, "knowledge_library_ids": ["library-a"]},
            "collections": {"dataforge_qa_full": [{"knowledge_library_id": "library-a",
                "partition_name": "kl_library-a", "content_revision": "a" * 64}]},
            "base_route_version": 1}


def test_dfm_ed25519_checksum_and_path_guards(tmp_path: Path):
    private, public = keys(); package = tmp_path / "seed.dfm"
    builder = MigrationPackageBuilder(package, key_id="central-key", private_key=private)
    builder.add_bytes("control/deployment.json", b"{}")
    builder.build(manifest())
    inspected = inspect_package(package, {"central-key": public})
    assert inspected["manifest"]["deployment"]["id"] == "deploy-a"

    tampered = tmp_path / "tampered.dfm"
    with zipfile.ZipFile(package, "r") as source, zipfile.ZipFile(tampered, "w", allowZip64=True) as target:
        for info in source.infolist():
            payload = b'{"changed":true}' if info.filename == "control/deployment.json" else source.read(info.filename)
            target.writestr(info.filename, payload)
    with pytest.raises(ValueError, match="checksum"):
        inspect_package(tampered, {"central-key": public})

    with zipfile.ZipFile(package, "a", allowZip64=True) as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(ValueError, match="entry|路径|根目录"):
        inspect_package(package, {"central-key": public})

    with pytest.raises(ValueError):
        MigrationPackageBuilder(tmp_path / "bad.dfm", key_id="central-key", private_key=private).add_bytes(
            "/absolute", b"bad")


class FakeMilvusClient:
    def __init__(self):
        self.partitions = {"A": [{"id": "2", "vector": [0.2, 0.3], "library": "A"},
                                 {"id": "1", "vector": [0.1, 0.2], "library": "A"}],
                           "B": [{"id": "3", "vector": [0.3, 0.4], "library": "B"}]}
    def describe_collection(self, **_): return {"fields": [{"name": "id", "is_primary": True}]}
    def has_partition(self, partition_name, **_): return partition_name.removeprefix("kl_") in self.partitions
    def query(self, partition_names, limit, offset, **_):
        rows = self.partitions[partition_names[0].removeprefix("kl_")]
        return rows[offset:offset + limit]
    def release_partitions(self, **_): pass
    def drop_partition(self, partition_name, **_): self.partitions.pop(partition_name.removeprefix("kl_"), None)
    def create_partition(self, partition_name, **_): self.partitions[partition_name.removeprefix("kl_")] = []
    def upsert(self, partition_name, data, **_): self.partitions[partition_name.removeprefix("kl_")].extend(data)


def test_parquet_round_trip_is_partition_scoped(tmp_path: Path):
    milvus = V7Milvus("unused"); client = FakeMilvusClient(); milvus._client = client
    output = tmp_path / "a.parquet"
    exported = milvus.export_partition("shared", "kl_A", output, batch_size=1)
    assert exported["count"] == 2 and client.partitions["B"] == [{"id": "3", "vector": [0.3, 0.4], "library": "B"}]
    milvus.reset_partition("shared", "kl_A")
    imported = milvus.import_partition("shared", "kl_A", output, batch_size=1)
    assert imported == milvus.verify_partition("shared", "kl_A")
    assert [row["id"] for row in client.partitions["A"]] == ["1", "2"]
    assert [row["id"] for row in client.partitions["B"]] == ["3"]


def test_local_instance_hides_other_deployment_and_rejects_second_seed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "local")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "hospital-a")
    url = f"sqlite:///{tmp_path / 'local.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    project = seeded_qa_project(store)
    bound_target = store.create_milvus_target("institution-a", "http://institution-a:19530")
    bound = store.create_deployment(
        project["id"], "hospital-a", "医院 A", "local", bound_target["id"],
        institution_name="医院 A", institution_code="KM001",
    )
    target = store.create_milvus_target("other", "http://unreachable:19530")
    other = store.create_deployment(project["id"], "hospital-b", "医院 B", "local", target["id"],
                                    institution_name="医院 B", institution_code="KM002")
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=url,
                        instance_mode="local", instance_code="hospital-a")
    client = TestClient(create_app(settings, check_schema=True))
    assert client.post("/api/projects", json={"name": "不允许的本地项目"}).status_code == 403
    context = client.app.state.instance.bind_seed(store, bound["deployment_id"], "central-one")
    monkeypatch.setattr(
        v7_web, "V7Milvus",
        lambda uri, token=None: SimpleNamespace(
            uri=uri, token=token, list_collections=lambda: [],
        ),
    )
    routing_check = client.post(
        f"/api/project-deployments/{bound['id']}/routing/validate?release_stage=test",
    ).json()
    assert routing_check["target_validation"] == {
        "mode": "live", "attempted": True, "reachable": True, "reason": None,
    }
    with pytest.raises(ValueError, match="第二次 Seed"):
        context.bind_seed(store, bound["deployment_id"], "central-one")
    assert client.post("/api/projects", json={"name": "仍不允许的本地项目"}).status_code == 403
    assert client.get(f'/api/project-deployments/{other["id"]}/tasks').status_code == 404
    projects = client.get("/api/projects").json()
    assert [item["id"] for item in projects[0]["deployments"]] == [bound["id"]]


def test_central_deployment_routing_validation_uses_live_milvus_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "dataforge-central")
    url = f"sqlite:///{tmp_path / 'central-routing-mode.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    project = seeded_qa_project(store)
    deployment = next(item for item in project["deployments"] if item["scope"] == "central")
    calls = []
    monkeypatch.setattr(
        v7_web, "V7Milvus",
        lambda uri, token=None: calls.append(uri) or SimpleNamespace(
            uri=uri, token=token, list_collections=lambda: [],
        ),
    )
    client = TestClient(create_app(Settings(
        project_root=tmp_path, state_dir=tmp_path / "state", database_url=url,
        instance_mode="central", instance_code="dataforge-central",
    ), check_schema=True))
    result = client.post(
        f"/api/project-deployments/{deployment['id']}/routing/validate?release_stage=test",
    ).json()
    assert result["target_validation"] == {
        "mode": "live", "attempted": True, "reachable": True, "reason": None,
    }
    production = client.post(
        f"/api/project-deployments/{deployment['id']}/routing/validate?release_stage=production",
    ).json()
    assert production["snapshot"]["release_stage"] == "production"
    assert calls == [
        deployment["stage_targets"]["test"]["milvus_url"],
        deployment["stage_targets"]["production"]["milvus_url"],
    ]


def test_migration_planner_rejects_dataforge_central_as_target(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'central-target.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    project = seeded_qa_project(store)
    central = next(item for item in project["deployments"] if item["scope"] == "central")
    with pytest.raises(ValueError, match="目标机构.*DataForge 中心环境"):
        MigrationPlanner(store).plan(central["id"])


def test_routing_snapshot_is_generated_from_deployment_authorization(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    url = f"sqlite:///{tmp_path / 'central.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    library = store.create_knowledge_library("医院 FAQ", "qa")
    project = seeded_qa_project(store)
    task = next(item for item in project["tasks"] if item["code"] == "knowledge_qa")
    deployment = store.list_deployments(project["id"])[0]
    deployment = store.patch_deployment(
        deployment["id"], institution_name="医院 A", institution_code="KM001",
    )
    deployment_task = store.list_deployment_tasks(deployment["id"])[0]
    store.put_deployment_route(deployment_task["id"], "KM001", "医院 A", [library["id"]])
    snapshot = store.routing_snapshot(deployment["id"], "test")
    assert snapshot["schema_version"] == 3
    assert snapshot["deployment"]["id"] == deployment["deployment_id"]
    assert snapshot["project_deployment"]["id"] == deployment["id"]
    assert snapshot["milvus_target"]["id"] == deployment["milvus_target_id"]
    assert snapshot["tasks"][0]["org_routes"][0]["knowledge_library_ids"] == [library["id"]]
    first = store.create_route_version(deployment["id"], snapshot)
    target = store.create_milvus_target("hospital-b", "http://hospital-b:19530")
    other = store.create_deployment(project["id"], "hospital-b", "医院 B", "local", target["id"],
                                    institution_name="医院 B", institution_code="KM002")
    other_version = store.create_route_version(other["id"], store.routing_snapshot(other["id"], "test"))
    assert first.version_no == other_version.version_no == 1


def test_institution_freeze_locks_org_code_and_does_not_create_package(tmp_path: Path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'freeze.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    docs = store.create_document_library("冻结资料")
    source = store.create_source(
        library_id=docs["id"], name="FAQ", filename="faq.txt", blob_uri=f"blob://{'f' * 64}",
        sha256="f" * 64, size_bytes=10, media_type="text/plain",
    )
    record_and_approve_source(store, source, "冻结 FAQ 来源")
    library = store.create_knowledge_library("冻结 FAQ", "qa")
    job = store.create_knowledge_job(
        [source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa",
    )
    store.apply_knowledge_output(job["id"], "qa", [{
        "source_knowledge_id": "freeze-faq", "canonical_content": "冻结答案",
        "data_json": {"question": "冻结？", "answer": "是"},
        "source_version_ids": [source["version"]["id"]],
    }])
    item = store.list_knowledge_items(library["id"])[0]
    for sync in store.create_vector_sync_jobs(library["id"]):
        store.finish_vector_sync(sync["id"], [{
            "knowledge_item_id": item["id"], "vector_id": f"vec-{sync['index_profile_id']}",
            "content_hash": item["content_hash"],
        }], asset_count=1, asset_digest="f" * 64)
    project = seeded_qa_project(store)
    task = next(item for item in project["tasks"] if item["code"] == "knowledge_qa")
    deployment = store.create_deployment(
        project["id"], "hospital-freeze", "冻结医院", "local", "",
        institution_name="冻结医院", institution_code="FREEZE001",
    )
    profile = next(item for item in store.list_index_profiles() if item["code"] == "qa-question")
    deployment_task = store.create_deployment_task(deployment["id"], task["id"], profile["id"])
    store.put_deployment_route(deployment_task["id"], "FREEZE001", "冻结医院", [library["id"]])
    frozen = store.freeze_route_version(deployment["id"], "test")
    assert frozen["status"] == "frozen"
    assert store.route_version_detail(deployment["id"], frozen["version_no"])["assets"]
    assert store.list_migration_jobs() == []
    production_frozen = store.freeze_route_version(deployment["id"], "production")
    production_draft = store.create_institution_release_draft(
        deployment["deployment_id"], "institution_release", release_stage="production",
        route_version_ids=[production_frozen["id"]],
    )
    production_plan = InstitutionReleasePlanner(store).plan(production_draft["id"])
    assert production_plan["deployment"]["release_stage"] == "production"
    assert "milvus_preset" not in production_plan["deployment"]
    with pytest.raises(ValueError, match="不能混合测试环境和生产环境"):
        store.create_institution_release_draft(
            deployment["deployment_id"], "institution_release",
            route_version_ids=[frozen["id"], production_frozen["id"]],
        )
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "dataforge-central")
    monkeypatch.setattr(
        v7_web, "V7Milvus",
        lambda *_args, **_kwargs: pytest.fail("中心 institution Routing 校验不得创建 Milvus 客户端"),
    )
    client = TestClient(create_app(Settings(
        project_root=tmp_path, state_dir=tmp_path / "state", database_url=url,
        instance_mode="central", instance_code="dataforge-central",
    ), check_schema=True))
    validation = client.post(
        f"/api/project-deployments/{deployment['id']}/routing/validate?release_stage=test",
    ).json()
    assert validation["valid"] is True
    assert validation["target_validation"] == {
        "mode": "deferred_to_local", "attempted": False, "reachable": None,
        "reason": "中心不连接机构现场 Milvus，实体检查延后到机构本地 Prepare/Activation Preflight",
    }
    second_project = store.create_project("共享资产项目")
    second_task = store.create_project_task(
        second_project["id"], "knowledge_qa_shared", "共享问答", "qa",
    )
    second_binding = store.bind_project_deployment(deployment["deployment_id"], second_project["id"])
    second_deployment_task = store.create_deployment_task(
        second_binding["id"], second_task["id"], profile["id"],
    )
    store.put_deployment_route(
        second_deployment_task["id"], "FREEZE001", "冻结医院", [library["id"]],
    )
    second_frozen = store.freeze_route_version(second_binding["id"], "test")
    draft = store.create_institution_release_draft(
        deployment["deployment_id"], "deployment_seed",
        route_version_ids=[frozen["id"], second_frozen["id"]],
    )
    required_asset_id = store.route_version_detail(
        deployment["id"], frozen["version_no"],
    )["assets"][0]["knowledge_asset_version_id"]
    store.update_institution_release_draft(
        draft["id"], extra_asset_version_ids=[required_asset_id, required_asset_id],
    )
    plan = InstitutionReleasePlanner(store).plan(draft["id"])
    assert plan["counts"]["projects"] == 2 and plan["counts"]["partitions"] == 1
    assert plan["projects"][0]["route_version"] == frozen["version_no"]
    assert plan["selection_summary"] == {
        "project_required_refs": 2, "manual_refs": 1, "raw_refs": 3,
        "duplicates_removed": 2, "resolved_assets": 1,
    }
    assert plan["preflight"]["blocked"] == 0
    assert plan["asset_versions"][0]["locked"] is True
    assert plan["asset_versions"][0]["selected_manually"] is True
    options = InstitutionReleasePlanner(store).asset_options(draft["id"])
    required_option = next(asset for group in options["collections"] for asset in group["assets"]
                           if asset["asset_version_id"] == required_asset_id)
    assert required_option["required"] is True
    assert {item["project_name"] for item in required_option["required_by_projects"]} == {
        project["name"], second_project["name"],
    }

    other_asset_id = next(item["id"] for item in store.vector_status(library["id"])["asset_versions"]
                          if item["id"] != required_asset_id and item["status"] == "ready")
    with store.sessions.begin() as session:
        required_asset = session.get(KnowledgeAssetVersion, required_asset_id)
        session.get(KnowledgeAssetVersion, other_asset_id).collection_name = required_asset.collection_name
    conflict_draft = store.create_institution_release_draft(
        deployment["deployment_id"], "institution_release",
        route_version_ids=[frozen["id"], second_frozen["id"]],
        extra_asset_version_ids=[other_asset_id],
    )
    conflict_plan = InstitutionReleasePlanner(store).plan(conflict_draft["id"])
    assert "RELEASE.LIBRARY.ASSET_VERSION_CONFLICT" in {
        item["code"] for item in conflict_plan["preflight"]["checks"] if item["status"] == "blocked"
    }
    assert "RELEASE.COLLECTION.CONTRACT_CONFLICT" in {
        item["code"] for item in conflict_plan["preflight"]["checks"] if item["status"] == "blocked"
    }
    release = store.freeze_institution_release_snapshot(draft["id"], plan)
    assert release["status"] == "frozen" and release["snapshot"]["asset_versions"]
    with pytest.raises(ValueError, match="机构代码已.*锁定"):
        store.patch_deployment(deployment["id"], institution_code="FREEZE002")
    private, _public = keys()
    settings = Settings(
        project_root=tmp_path, state_dir=tmp_path / "freeze-state", database_url=url,
        instance_mode="central", instance_code="central-freeze",
        migration_signing_private_key=private,
    )
    client = TestClient(create_app(settings, check_schema=True))
    identity = client.get("/api/instance?instance_mode=local").json()
    assert identity["instance_mode"] == "central"
    assert identity["deployment_flavor"] == "central_control_plane"
    asset_options_response = client.get(
        f"/api/institution-deployments/drafts/{conflict_draft['id']}/asset-options",
    )
    assert asset_options_response.status_code == 200
    assert asset_options_response.json()["collections"]
    blocked = client.post(f"/api/institution-deployments/drafts/{conflict_draft['id']}/freeze")
    assert blocked.status_code == 422
    built = client.post(f"/api/institution-deployments/releases/{release['id']}/build")
    assert built.status_code == 202
    assert built.json()["release_snapshot_id"] == release["id"]


def test_manifest_v2_requires_project_routes_and_rejects_secrets():
    value = {
        "format": "dataforge-migration", "schema_version": 2, "manifest_schema_version": 2,
        "package_kind": "deployment_seed", "package_id": "pkg-v2", "source_instance_id": "central",
        "minimum_dataforge_version": "7.0.0", "maximum_dataforge_version": "7.0.0",
        "source_instance_version": "7.0.0", "required_features": ["immutable_asset_versions"],
        "operator_versions": [], "storage_contract_versions": [],
        "base_release_id": None, "base_manifest_digest": None,
        "deployment": {"id": "dep-1", "code": "hospital-a"},
        "projects": [{"project_deployment_id": "pd-1", "route_version": 1,
                      "route_snapshot": {"schema_version": 3}}],
        "scope": {"deployment_count": 1, "knowledge_library_ids": ["kl-1"]},
        "asset_versions": [{"id": "asset-1", "knowledge_library_id": "kl-1"}],
        "collections": {"dataforge_text": [{"knowledge_library_id": "kl-1",
                                                "partition_name": "kl_1__v1"}]},
        "diff_summary": {}, "tombstones": [],
    }
    assert validate_manifest(value)["schema_version"] == 2
    with pytest.raises(ValueError, match="敏感字段"):
        validate_manifest({**value, "milvus_preset": {"token": "leak"}})


def test_local_milvus_credentials_are_encrypted_and_changes_clear_verification(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "local")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "local-config-test")
    url = f"sqlite:///{tmp_path / 'local-config.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    settings = Settings(
        project_root=tmp_path, state_dir=tmp_path / "state", database_url=url,
        instance_mode="local", instance_code="local-config-test",
    )
    instance = InstanceContext.load(store, settings)
    service = LocalMilvusConfigurationService(
        store, base64.b64encode(b"k" * 32).decode("ascii"),
    )
    created = service.put(
        instance.id, "candidate_target", uri="http://milvus.local:19531",
        username="operator", secret="private-token",
    )
    assert created["secret_configured"] is True and "secret" not in created
    assert service.resolve(instance.id, "candidate_target").token == "operator:private-token"

    class Client:
        @staticmethod
        def list_collections(): return []

    class Milvus:
        def client(self): return Client()

    verified = service.verify(instance.id, "candidate_target", factory=lambda _uri, _token: Milvus())
    assert verified["status"] == "verified" and verified["verified_fingerprint"]
    changed = service.put(
        instance.id, "candidate_target", uri="http://milvus-new.local:19531",
        username="operator",
    )
    assert changed["status"] == "pending_verification"
    assert changed["verified_fingerprint"] is None and changed["secret_configured"] is True
    service.verify(instance.id, "candidate_target", factory=lambda _uri, _token: Milvus())
    promoted = service.promote_candidate(instance.id)
    assert promoted["status"] == "verified" and promoted["secret_configured"] is True
    assert service.resolve(instance.id, "current_target").token == "operator:private-token"


def test_v2_seed_waits_for_milvus_then_imports_template_closure_and_candidate_route(
        tmp_path: Path, monkeypatch):
    private, public = keys()
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "central-v2")
    central_url = f"sqlite:///{tmp_path / 'central-v2.sqlite3'}"; upgrade(central_url)
    central = V7Store(central_url); central.seed()
    central_objects = LocalObjectStore(tmp_path / "central-v2-objects")
    document = central.create_document_library("完整模板资料")
    stored = central_objects.put_blob(b"template closure", "text/plain")
    source = central.create_source(
        library_id=document["id"], name="闭包", filename="closure.txt", blob_uri=stored.blob_uri,
        sha256=stored.sha256, size_bytes=stored.size_bytes, media_type="text/plain",
    )
    record_and_approve_source(central, source, "完整模板来源")
    binding = central.bind_document_library_template(document["id"], "flow_standard-qa")
    library_id = binding["outputs"][0]["knowledge_library"]["id"]
    processing_job = central.process_document_library(document["id"])[0]
    central.apply_knowledge_output(processing_job["id"], "qa", [{
        "source_knowledge_id": "closure-faq", "canonical_content": "问题：闭包？答案：完整。",
        "data_json": {"question": "闭包？", "answer": "完整。"},
        "source_version_ids": [source["version"]["id"]],
    }])
    central.complete_job(processing_job["id"])
    item = central.list_knowledge_items(library_id)[0]
    for sync in central.create_vector_sync_jobs(library_id):
        central.finish_vector_sync(sync["id"], [{
            "knowledge_item_id": item["id"], "vector_id": f"vec-{sync['index_profile_id']}",
            "content_hash": item["content_hash"],
        }], asset_count=1, asset_digest="c" * 64)

    project = seeded_qa_project(central)
    task = next(value for value in project["tasks"] if value["code"] == "knowledge_qa")
    deployment = central.create_deployment(
        project["id"], "hospital-v2", "医院 V2", "local", "",
        institution_name="医院 V2", institution_code="V2001",
    )
    profile = next(value for value in central.list_index_profiles() if value["code"] == "qa-question")
    deployment_task = central.create_deployment_task(deployment["id"], task["id"], profile["id"],
        final_top_k=3, reranker_serving_code="bge_reranker_large")
    central.put_deployment_route(deployment_task["id"], "V2001", "医院 V2", [library_id])
    frozen = central.freeze_route_version(deployment["id"], "test")
    draft = central.create_institution_release_draft(
        deployment["deployment_id"], "deployment_seed", route_version_ids=[frozen["id"]],
    )
    release_plan = InstitutionReleasePlanner(central).plan(draft["id"])
    release = central.freeze_institution_release_snapshot(draft["id"], release_plan)
    selected = release_plan["libraries"][0]
    central_row = {
        "id": "vec-v2", "vector": [0.1, 0.2], "knowledge_library_id": library_id,
        "knowledge_item_id": item["id"], "source_knowledge_id": "closure-faq",
    }
    central_milvus = FakeOfflineMilvus({
        selected["collection_name"]: {selected["partition_name"]: [central_row]},
    })
    export_job = central.create_migration_job(
        direction="export", package_kind="deployment_seed", target_deployment_id=deployment["deployment_id"],
        release_snapshot_id=release["id"], status="queued", stage="planned",
        items=[{"knowledge_library_id": library_id, "collection_name": selected["collection_name"],
                "partition_name": selected["partition_name"]}],
    )
    central_settings = Settings(
        project_root=tmp_path, state_dir=tmp_path / "central-v2-state", database_url=central_url,
        instance_mode="central", instance_code="central-v2",
    )
    exported = MigrationExporter(
        central, central_objects, central_milvus, migration_dir=tmp_path / "v2-packages",
        private_key=private, key_id="central-key", instance=InstanceContext.load(central, central_settings),
    ).run(export_job["id"])
    with zipfile.ZipFile(exported["package_path"], "r") as archive:
        manifest_v2 = json.loads(archive.read("manifest.json"))
        assert manifest_v2["schema_version"] == 2
        assert archive.read("metadata/flow_execution_snapshots.jsonl").strip()
        assert archive.read("metadata/operator_versions.jsonl").strip()
        assert archive.read("metadata/document_library_processing_baselines.jsonl").strip()
        exported_asset_items = [json.loads(line) for line in archive.read("metadata/knowledge_asset_items.jsonl").splitlines()]
        assert exported_asset_items and exported_asset_items[0]["evidence_json"]
        assert "asset_item_snapshots" in manifest_v2["required_features"]

    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "local")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "hospital-v2")
    local_url = f"sqlite:///{tmp_path / 'local-v2.sqlite3'}"; upgrade(local_url)
    local = V7Store(local_url); local.seed()
    local_objects = LocalObjectStore(tmp_path / "local-v2-objects")
    local_settings = Settings(
        project_root=tmp_path, state_dir=tmp_path / "local-v2-state", database_url=local_url,
        instance_mode="local", instance_code="hospital-v2",
    )
    local_instance = InstanceContext.load(local, local_settings)
    local_config = LocalMilvusConfigurationService(
        local, base64.b64encode(b"v" * 32).decode("ascii"),
    )
    import_job = local.create_migration_job(
        direction="import", package_kind="deployment_seed", package_id=manifest_v2["package_id"],
        package_path=exported["package_path"], status="queued", stage="uploaded",
        items=[{"knowledge_library_id": part["knowledge_library_id"], "collection_name": collection,
                "partition_name": part["partition_name"]}
               for collection, partitions in manifest_v2["collections"].items() for part in partitions],
    )
    waiting = MigrationImporter(
        local, local_objects, None, migration_dir=tmp_path / "local-v2-packages",
        trusted_public_keys=json.dumps({"central-key": public}), instance=local_instance,
        routing_dir=local_settings.routing_dir, local_config=local_config,
    ).run(import_job["id"])
    assert waiting["status"] == "waiting"
    assert waiting["stage"] == "waiting_for_milvus_configuration"
    with local.sessions() as session:
        restored_item = session.scalar(select(KnowledgeAssetItem))
        assert restored_item and restored_item.canonical_content == exported_asset_items[0]["canonical_content"]
        assert restored_item.evidence_json == exported_asset_items[0]["evidence_json"]
    restored_task = local.list_deployment_tasks(deployment["id"])[0]
    assert restored_task["final_top_k"] == 3 and restored_task["reranker_serving_code"] == "bge_reranker_large"
    assert local.list_document_library_template_bindings(document["id"])[0]["pending_file_count"] == 0

    local_config.put(local_instance.id, "candidate_target", uri="http://milvus.local:19531")
    local_milvus = FakeOfflineMilvus()

    class HealthClient:
        @staticmethod
        def list_collections(): return []

    class HealthMilvus:
        def client(self): return HealthClient()

    local_config.verify(
        local_instance.id, "candidate_target", factory=lambda _uri, _token: HealthMilvus(),
    )
    local.resume_migration_job(import_job["id"], selected_import_target="candidate_target")
    import dataforge.v7.migration.importer as importer_module
    import dataforge.v7.migration.verifier as verifier_module
    monkeypatch.setattr(importer_module, "V7Milvus", lambda _uri, _token=None: local_milvus)
    monkeypatch.setattr(verifier_module, "V7Milvus", lambda _uri, _token=None: local_milvus)
    completed = MigrationImporter(
        local, local_objects, None, migration_dir=tmp_path / "local-v2-packages",
        trusted_public_keys=json.dumps({"central-key": public}), instance=local_instance,
        routing_dir=local_settings.routing_dir, local_config=local_config,
    ).run(import_job["id"])
    assert completed["status"] == "completed"
    candidates = local.list_imported_route_candidates(import_job["id"])
    assert len(candidates) == 1 and candidates[0]["status"] == "ready"
    assert candidates[0]["snapshot"]["milvus_target"]["milvus_url"] == "http://milvus.local:19531"
    verifier = ActivationPreflightVerifier(
        local, local_config, InstanceContext.load(local, local_settings),
        milvus_factory=lambda _uri, _token=None: local_milvus,
    )
    verified_preflight = verifier.run(import_job["id"])
    assert verified_preflight["ready"] is True
    verified_partition = verified_preflight["partitions"][0]
    collection_name, partition_name = verified_partition["collection_name"], verified_partition["partition_name"]
    original_rows = list(local_milvus.rows[collection_name][partition_name])

    local_milvus.rows[collection_name][partition_name] = [{**original_rows[0], "source_knowledge_id": "changed"}]
    digest_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.PARTITION.DIGEST_MISMATCH" in {item["code"] for item in digest_blocked["checks"]}
    local_milvus.rows[collection_name][partition_name] = []
    count_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.PARTITION.COUNT_MISMATCH" in {item["code"] for item in count_blocked["checks"]}
    local_milvus.rows[collection_name].pop(partition_name)
    missing_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.PARTITION.MISSING" in {item["code"] for item in missing_blocked["checks"]}
    local_milvus.rows[collection_name][partition_name] = original_rows

    with local.sessions.begin() as session:
        session.get(ImportedRouteCandidate, candidates[0]["id"]).status = "waiting_assets"
    candidate_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.CANDIDATE.READY" in {
        item["code"] for item in candidate_blocked["checks"] if item["status"] == "blocked"
    }
    with local.sessions.begin() as session:
        session.get(ImportedRouteCandidate, candidates[0]["id"]).status = "ready"
        asset_id = next(iter(local._asset_ids_in_json(candidates[0]["snapshot"])))
        session.get(KnowledgeAssetVersion, asset_id).status = "building"
    asset_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.ASSET.READY" in {
        item["code"] for item in asset_blocked["checks"] if item["status"] == "blocked"
    }
    with local.sessions.begin() as session:
        session.get(KnowledgeAssetVersion, asset_id).status = "ready"

    local_config.put(local_instance.id, "candidate_target", uri="http://changed.local:19531")
    target_blocked = verifier.run(import_job["id"])
    assert "ACTIVATION.MILVUS.TARGET_UNCHANGED" in {
        item["code"] for item in target_blocked["checks"] if item["status"] == "blocked"
    }
    local_config.put(local_instance.id, "candidate_target", uri="http://milvus.local:19531")
    local_config.verify(local_instance.id, "candidate_target", factory=lambda _uri, _token: HealthMilvus())
    assert verifier.run(import_job["id"])["ready"] is True
    local_milvus.write_calls = 0
    client = TestClient(create_app(local_settings, check_schema=True))
    api_preflight = client.post(f"/api/migrations/{import_job['id']}/activation-preflight")
    assert api_preflight.status_code == 200 and api_preflight.json()["ready"] is True
    activated = client.post("/api/imported-route-candidates/activate-ready", json={
        "candidate_ids": [candidates[0]["id"], "missing-candidate"],
    })
    assert activated.status_code == 200
    assert activated.json()["atomic"] is False
    assert [item["ok"] for item in activated.json()["results"]] == [True, False]
    assert local_milvus.write_calls == 0
    activated_candidate = local.list_imported_route_candidates(import_job["id"])[0]
    assert activated_candidate["status"] == "activated"
    job_batch = client.post(f"/api/migrations/{import_job['id']}/activate-ready")
    assert job_batch.status_code == 200
    assert job_batch.json()["atomic"] is False
    assert job_batch.json()["results"][0]["ok"] is True
    assert local_milvus.write_calls == 0

    # A Knowledge Update imports newer immutable assets but must not create a
    # route candidate or change the already published project route.
    for sync in central.create_vector_sync_jobs(library_id):
        central.finish_vector_sync(sync["id"], [{
            "knowledge_item_id": item["id"], "vector_id": f"vec-update-{sync['index_profile_id']}",
            "content_hash": item["content_hash"],
        }], asset_count=1, asset_digest="d" * 64)
    update_draft = central.create_institution_release_draft(
        deployment["deployment_id"], "knowledge_update", knowledge_library_ids=[library_id],
    )
    update_plan = InstitutionReleasePlanner(central).plan(update_draft["id"])
    update_release = central.freeze_institution_release_snapshot(update_draft["id"], update_plan)
    update_milvus = FakeOfflineMilvus({
        collection: {asset["partition_name"]: [{**central_row, "id": f"update-{asset['asset_version_id']}"}]}
        for collection, assets in update_plan["collections"].items() for asset in assets
    })
    update_export_job = central.create_migration_job(
        direction="export", package_kind="knowledge_update",
        target_deployment_id=deployment["deployment_id"], release_snapshot_id=update_release["id"],
        status="queued", stage="planned",
        items=[{"knowledge_library_id": asset["knowledge_library_id"], "collection_name": collection,
                "partition_name": asset["partition_name"]}
               for collection, assets in update_plan["collections"].items() for asset in assets],
    )
    update_exported = MigrationExporter(
        central, central_objects, update_milvus, migration_dir=tmp_path / "v2-packages",
        private_key=private, key_id="central-key", instance=InstanceContext.load(central, central_settings),
    ).run(update_export_job["id"])
    with zipfile.ZipFile(update_exported["package_path"], "r") as archive:
        update_manifest = json.loads(archive.read("manifest.json"))
    update_import_job = local.create_migration_job(
        direction="import", package_kind="knowledge_update", package_id=update_manifest["package_id"],
        package_path=update_exported["package_path"], status="queued", stage="uploaded",
        items=[{"knowledge_library_id": part["knowledge_library_id"], "collection_name": collection,
                "partition_name": part["partition_name"]}
               for collection, partitions in update_manifest["collections"].items() for part in partitions],
    )
    updated = MigrationImporter(
        local, local_objects, None, migration_dir=tmp_path / "local-v2-packages",
        trusted_public_keys=json.dumps({"central-key": public}),
        instance=InstanceContext.load(local, local_settings), routing_dir=local_settings.routing_dir,
        local_config=local_config,
    ).run(update_import_job["id"])
    assert updated["status"] == "completed"
    assert local.list_imported_route_candidates(update_import_job["id"]) == []
    after_update = local.list_imported_route_candidates(import_job["id"])[0]
    assert after_update["activated_route_version_id"] == activated_candidate["activated_route_version_id"]
    assert after_update["snapshot"] == activated_candidate["snapshot"]
    old_asset = next(value for value in manifest_v2["asset_versions"]
                     if value["index_profile_revision_id"] == selected["index_profile_revision_id"])
    new_asset = next(value for value in update_manifest["asset_versions"]
                     if value["index_profile_revision_id"] == selected["index_profile_revision_id"])
    failed_snapshot_text = json.dumps(after_update["snapshot"])
    failed_snapshot = json.loads(failed_snapshot_text
                                 .replace(old_asset["id"], new_asset["id"])
                                 .replace(old_asset["partition_name"], new_asset["partition_name"]))
    failed_activation_job = local.create_migration_job(
        direction="import", package_kind="institution_release", status="completed", stage="completed",
    )
    failed_candidate = local.create_imported_route_candidates(failed_activation_job["id"], [{
        "project_deployment_id": after_update["project_deployment_id"],
        "route_version": after_update["source_route_version"] + 1,
        "route_snapshot": failed_snapshot,
    }])[0]
    published_before = [value["id"] for value in local.list_route_versions(
        after_update["project_deployment_id"], "test",
    ) if value["status"] == "published"]
    started = local.start_route_candidate_activation(failed_candidate["id"])
    failed = local.finish_route_candidate_activation(
        failed_candidate["id"], started["route_version_id"], None, None, "simulated atomic file failure",
    )
    published_after = [value["id"] for value in local.list_route_versions(
        after_update["project_deployment_id"], "test",
    ) if value["status"] == "published"]
    assert failed["status"] == "failed"
    assert published_after == published_before == [activated_candidate["activated_route_version_id"]]


class FakeOfflineMilvus:
    def __init__(self, rows=None):
        self.rows, self.write_calls, self.export_calls = rows or {}, 0, []
    @staticmethod
    def _digest(rows): return V7Milvus._row_digest(rows, "id")
    def ensure_managed_collection(self, collection_name, _schema, _dimension, _metric, _index, description):
        self.write_calls += 1
        self.rows.setdefault(collection_name, {}); return description
    def ensure_partition(self, collection_name, partition_name):
        self.write_calls += 1; self.rows.setdefault(collection_name, {}).setdefault(partition_name, [])
    def client(self): return self
    def list_collections(self): return list(self.rows)
    def partition_exists(self, collection_name, partition_name):
        return partition_name in self.rows.get(collection_name, {})
    def verify_partition(self, collection_name, partition_name):
        rows = self.rows[collection_name][partition_name]
        return {"count": len(rows), "digest": self._digest(rows), "primary_field": "id"}
    def export_partition(self, collection_name, partition_name, output_path, batch_size=1000):
        import pyarrow as pa
        import pyarrow.parquet as pq
        rows = sorted(self.rows[collection_name][partition_name], key=lambda item: item["id"])
        table = pa.Table.from_pylist(rows).replace_schema_metadata({
            b"dataforge_primary_field": b"id", b"dataforge_json_fields": b"[]"})
        output_path.parent.mkdir(parents=True, exist_ok=True); pq.write_table(table, output_path)
        self.export_calls.append((collection_name, partition_name))
        return self.verify_partition(collection_name, partition_name)
    def reset_partition(self, collection_name, partition_name):
        self.write_calls += 1; self.rows.setdefault(collection_name, {})[partition_name] = []
    def load_partition(self, collection_name, partition_name):
        assert partition_name in self.rows.setdefault(collection_name, {})
    def import_partition(self, collection_name, partition_name, input_path, batch_size=1000):
        import pyarrow.parquet as pq
        self.write_calls += 1
        self.rows.setdefault(collection_name, {})[partition_name] = pq.read_table(input_path).to_pylist()
        return self.verify_partition(collection_name, partition_name)


def test_exporter_uses_each_final_partition_exactly_once(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "export-inventory")
    url = f"sqlite:///{tmp_path / 'export-inventory.sqlite3'}"; upgrade(url)
    store = V7Store(url); store.seed()
    private, _public = keys()
    collection = "dataforge_text_knowledge"
    rows = {collection: {f"kl_library_{index}__v1": [{
        "id": f"vector-{index}", "knowledge_library_id": f"library-{index}",
    }] for index in range(15)}}
    milvus = FakeOfflineMilvus(rows)
    items = [{"knowledge_library_id": f"library-{index}", "collection_name": collection,
              "partition_name": f"kl_library_{index}__v1"} for index in range(15)]
    job = store.create_migration_job(
        direction="export", package_kind="institution_release", status="queued",
        stage="planned", items=items,
    )
    plan = {"collections": {collection: [{
        **item, "asset_version_id": f"asset-{index}",
    } for index, item in enumerate(items)]}}
    settings = Settings(
        project_root=tmp_path, state_dir=tmp_path / "export-state", database_url=url,
        instance_mode="central", instance_code="export-inventory",
    )
    exporter = MigrationExporter(
        store, LocalObjectStore(tmp_path / "objects"), milvus,
        migration_dir=tmp_path / "packages", private_key=private, key_id="central-key",
        instance=InstanceContext.load(store, settings),
    )
    builder = MigrationPackageBuilder(tmp_path / "inventory.dfm", key_id="central-key", private_key=private)
    exporter._add_vectors(builder, plan, tmp_path / "work", job["id"])
    assert len(milvus.export_calls) == 15
    assert len(set(milvus.export_calls)) == 15

    duplicate = {"collections": {collection: [plan["collections"][collection][0]] * 2}}
    duplicate_builder = MigrationPackageBuilder(
        tmp_path / "duplicate.dfm", key_id="central-key", private_key=private,
    )
    with pytest.raises(ValueError, match="重复 Partition"):
        exporter._add_vectors(duplicate_builder, duplicate, tmp_path / "duplicate", job["id"])


def test_signed_seed_export_import_round_trip(tmp_path: Path, monkeypatch):
    private, public = keys(); monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "central")
    central_url = f"sqlite:///{tmp_path / 'central-roundtrip.sqlite3'}"; upgrade(central_url)
    central = V7Store(central_url); central.seed(); central_objects = LocalObjectStore(tmp_path / "central-objects")
    docs = central.create_document_library("资料")
    stored = central_objects.put_blob(b"hospital faq", "text/plain")
    source = central.create_source(library_id=docs["id"], name="FAQ", filename="faq.txt", blob_uri=stored.blob_uri,
        sha256=stored.sha256, size_bytes=stored.size_bytes, media_type="text/plain")
    record_and_approve_source(central, source, "医院 FAQ 来源")
    library = central.create_knowledge_library("医院 FAQ", "qa")
    knowledge_job = central.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    central.apply_knowledge_output(knowledge_job["id"], "qa", [{"source_knowledge_id": "faq-1",
        "canonical_content": "问题：挂号？答案：窗口。", "data_json": {"question": "挂号？", "answer": "窗口。"},
        "source_version_ids": [source["version"]["id"]]}])
    item = central.list_knowledge_items(library["id"])[0]
    for sync in central.create_vector_sync_jobs(library["id"]):
        central.finish_vector_sync(sync["id"], [{"knowledge_item_id": item["id"],
            "vector_id": f'vec-{sync["index_profile_id"]}', "content_hash": item["content_hash"]}])
    project = seeded_qa_project(central)
    task = next(item for item in project["tasks"] if item["code"] == "knowledge_qa")
    deployment = central.create_deployment(
        project["id"], "hospital-a", "医院 A", "central", "",
        institution_name="医院 A", institution_code="KM001",
    )
    qa_question_profile = next(item for item in central.list_index_profiles() if item["code"] == "qa-question")
    deployment_task = central.create_deployment_task(deployment["id"], task["id"], qa_question_profile["id"])
    central.put_deployment_route(deployment_task["id"], "KM001", "医院 A", [library["id"]])
    central.create_route_version(deployment["id"], central.routing_snapshot(deployment["id"], "test"), status="published")
    other_target = central.create_milvus_target("hospital-b", "http://hospital-b-secret:19530")
    other_deployment = central.create_deployment(project["id"], "hospital-b-secret", "医院 B 私有部署", "local", other_target["id"],
                                                 institution_name="医院 B", institution_code="KM002")
    other_task = central.create_deployment_task(other_deployment["id"], task["id"], qa_question_profile["id"])
    central.put_deployment_route(other_task["id"], "KM002", "医院 B", [library["id"]])
    plan = MigrationPlanner(central).plan(deployment["id"], [library["id"]])
    selected = plan["libraries"][0]; central_milvus = FakeOfflineMilvus({
        selected["collection_name"]: {selected["partition_name"]: [{"id": "vec-1", "vector": [0.1, 0.2],
            "knowledge_library_id": library["id"], "knowledge_item_id": item["id"], "source_knowledge_id": "faq-1"}]}})
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "central-state", database_url=central_url,
        instance_mode="central", instance_code="central-one")
    central_instance = InstanceContext.load(central, settings)
    export_job = central.create_migration_job(direction="export", package_kind="deployment_seed",
        project_id=project["id"], project_deployment_id=deployment["id"], status="queued", stage="planned",
        checkpoint={"options": {"knowledge_library_ids": [library["id"]], "include_full_document_library": False}},
        items=[{"knowledge_library_id": library["id"], "collection_name": selected["collection_name"],
                "partition_name": selected["partition_name"]}])
    exported = MigrationExporter(central, central_objects, central_milvus, migration_dir=tmp_path / "packages",
        private_key=private, key_id="central-key", instance=central_instance).run(export_job["id"])
    with zipfile.ZipFile(exported["package_path"], "r") as archive:
        package_text = b"\n".join(archive.read(name) for name in archive.namelist()
                                  if not name.endswith(".parquet") and not name.startswith("objects/")).decode("utf-8")
    assert "hospital-b-secret" not in package_text and "KM002" not in package_text

    monkeypatch.setenv("DATAFORGE_INSTANCE_MODE", "local"); monkeypatch.setenv("DATAFORGE_INSTANCE_CODE", "hospital-a")
    local_url = f"sqlite:///{tmp_path / 'local-roundtrip.sqlite3'}"; upgrade(local_url)
    local = V7Store(local_url); local.seed(); local_objects = LocalObjectStore(tmp_path / "local-objects")
    local_settings = Settings(project_root=tmp_path, state_dir=tmp_path / "local-state", database_url=local_url,
        instance_mode="local", instance_code="hospital-a")
    local_instance = InstanceContext.load(local, local_settings)
    inspected = inspect_package(Path(exported["package_path"]), {"central-key": public}); manifest_value = inspected["manifest"]
    import_items = [{"knowledge_library_id": part["knowledge_library_id"], "collection_name": collection,
                     "partition_name": part["partition_name"]}
                    for collection, parts in manifest_value["collections"].items() for part in parts]
    imported_job = local.create_migration_job(direction="import", package_kind="deployment_seed",
        package_id=manifest_value["package_id"], project_id=project["id"], project_deployment_id=deployment["id"],
        package_path=exported["package_path"], package_sha256=exported["package_sha256"],
        status="inspected", stage="verified", items=import_items)
    local.queue_migration_import(imported_job["id"])
    local_milvus = FakeOfflineMilvus()
    result = MigrationImporter(local, local_objects, local_milvus, migration_dir=tmp_path / "local-packages",
        trusted_public_keys=json.dumps({"central-key": public}), instance=local_instance,
        routing_dir=tmp_path / "local-routing").run(imported_job["id"])
    assert result["status"] == "completed"
    assert InstanceContext.load(local, local_settings).bound_deployment_id == deployment["deployment_id"]
    restored = local.get_knowledge_library(library["id"])
    assert restored.origin_type == "central_import" and restored.migration_status == "ready"
    assert local_milvus.verify_partition(selected["collection_name"], selected["partition_name"])["count"] == 1
    with local.sessions() as session:
        imported_version = session.scalar(select(SourceVersion))
        imported_evidence = session.scalar(select(KnowledgeItemSource))
        assert imported_version and imported_version.review_status == "approved"
        assert session.get(SourceReviewSnapshot, imported_version.current_review_snapshot_id)
        assert imported_evidence and imported_evidence.source_chunk_revision_id and imported_evidence.source_review_snapshot_id
