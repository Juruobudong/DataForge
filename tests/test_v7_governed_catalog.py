from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from dataforge.v7.catalog import builtin_flow_definition, catalog_by_code
from dataforge.v7.flow import FlowCompiler, FlowValidationError
from dataforge.v7.runner import execute_job, preview_template_definition, select_parser_adapter, select_runtime_mode
from dataforge.v7 import runner
from dataforge.v7 import parser_runtime
from dataforge.v7 import worker
from dataforge.v7.documents import DocumentDeletionService
from dataforge.v7.models import Artifact
from dataforge.v7.storage import LocalObjectStore
from dataforge.v7.store import DEFAULT_INDEX_FIELD_MAPPING, V7Store
from dataforge.v7.web import create_app
from dataforge.config import Settings
from fastapi.testclient import TestClient


def _store(tmp_path) -> V7Store:
    from dataforge.v7.migrations import upgrade

    store = V7Store(f"sqlite:///{tmp_path / 'governed.sqlite3'}")
    upgrade(str(store.engine.url)); store.seed()
    return store


def _source(store: V7Store, objects: LocalObjectStore, code: str, text: str) -> dict:
    library = store.create_document_library(f"资料 {code}")
    stored = objects.put_bytes(f"sources/{code}.txt", text.encode("utf-8"), "text/plain")
    return store.create_source(library_id=library["id"], name=code, filename=f"{code}.txt", object_key=stored.key,
                               sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="text/plain")


def test_catalog_is_logical_allowlist_and_disabled_operator_is_rejected(tmp_path):
    store = _store(tmp_path)
    listed = store.list_operator_catalog()
    codes = {item["code"] for item in listed}
    assert {"document-parser", "text-cleaner", "semantic-chunker", "source-chunk-builder", "knowledge-diff"} <= codes
    assert not {"mineru-pipeline-gpu-adapter", "mineru-api-adapter", "mineru-local-adapter", "mineru-flash-adapter"} & codes
    seeds = {item["code"] for item in __import__("dataforge.v7.catalog", fromlist=["CATALOG_SEEDS"]).CATALOG_SEEDS}
    assert "mineru-pipeline-gpu-adapter" in seeds
    assert not {"mineru-api-adapter", "mineru-local-adapter", "mineru-flash-adapter"} & seeds
    assert all(item["description"].strip() and item["input_example"] and item["output_example"] for item in listed)
    semantic = next(item for item in listed if item["code"] == "semantic-chunker")
    assert semantic["description"] == "按语义边界将文档切分为文本块"
    assert semantic["input_example"]["input"] and semantic["output_example"]["output"]
    with store.sessions.begin() as session:
        from dataforge.v7.models import OperatorDefinition, OperatorVersion
        definition = session.get(OperatorDefinition, semantic["id"])
        version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == semantic["id"]))
        definition.description = "旧说明"; version.input_example = {}; version.output_example = {}
    store.seed()
    refreshed = next(item for item in store.list_operator_catalog() if item["code"] == "semantic-chunker")
    assert refreshed["description"] == semantic["description"]
    assert refreshed["input_example"] == semantic["input_example"] and refreshed["output_example"] == semantic["output_example"]
    catalog = catalog_by_code()
    compiler = FlowCompiler(catalog=catalog, type_revisions={"text": {"id": "typerev_text_1"}})
    with pytest.raises(FlowValidationError, match="allowlist"):
        compiler.compile({"schema_version": 2, "nodes": [
            {"id": "bad", "kind": "operator", "ref": "kcenter-greedy", "params": {"knowledge_type": "text"}},
            {"id": "sink", "kind": "knowledge_sink", "knowledge_type": "text"},
        ], "edges": [["bad", "sink"]]})
    assert select_parser_adapter("report.pdf", "auto") == "mineru-pipeline-gpu"
    assert select_parser_adapter("report.docx", "auto") == "dataforge-word-parser"
    assert select_parser_adapter("table.csv", "auto") == "dataforge-structured-table-parser"
    assert select_runtime_mode(4, {"DATAFORGE_BATCH_THRESHOLD": "4"}) == "batch"
    assert select_runtime_mode(3, {"DATAFORGE_BATCH_THRESHOLD": "4"}) == "single"


def test_controlled_preview_exposes_top_level_subflows_sinks_and_isolates_branch_failures(monkeypatch):
    definition = builtin_flow_definition(["text", "qa"])
    compiled = FlowCompiler(
        catalog=catalog_by_code(),
        subflows={item["code"]: item["definition"] for item in __import__("dataforge.v7.catalog", fromlist=["subflow_seeds"]).subflow_seeds()},
        type_revisions={"text": {"id": "text"}, "qa": {"id": "qa"}},
    ).compile(definition)["compiled_definition"]
    calls = []
    monkeypatch.setattr(runner, "_initialize_global_llm", lambda: calls.append("llm"))
    monkeypatch.setattr(runner, "parse_with_mineru", lambda **_: calls.append("mineru"))

    preview = preview_template_definition(definition, "guideline-md", compiled_definition=compiled)

    assert preview["preview_mode"] == "controlled_in_memory"
    assert preview["persisted"] is False and preview["status"] == "completed"
    assert calls == []
    assert set(definition_node["id"] for definition_node in definition["nodes"]) <= set(preview["node_runs"])
    assert preview["node_runs"]["parse"]["internal_trace"]
    assert preview["node_runs"]["sink-text"]["inputs"]["input"]["total"] >= 1
    assert preview["node_runs"]["sink-text"]["outputs"] == {}

    invalid_compiled = json.loads(json.dumps(compiled))
    chunk_node = next(item for item in invalid_compiled["nodes"] if item["id"] == "chunk::chunk")
    chunk_node["params"] = {"chunk_size": 50}
    failed = preview_template_definition(definition, "guideline-md", compiled_definition=invalid_compiled)
    assert failed["status"] == "completed_with_errors"
    assert any(item["status"] in {"failed", "skipped"} for item in failed["expanded_node_runs"].values())
    assert failed["expanded_node_runs"]["generate-text"]["status"] == "skipped"


def test_preview_truncates_records_and_long_strings():
    payload = runner._preview_port([{"content": "x" * 600}, {"content": "b"}, {"content": "c"}, {"content": "d"}])
    assert payload["total"] == 4 and payload["truncated"] is True
    assert len(payload["items"]) == 3 and len(payload["items"][0]["content"]) == 500


class FakeMinerUClient:
    response: httpx.Response | None = None
    error: Exception | None = None
    seen: dict = {}

    def __init__(self, *, timeout): self.timeout = timeout
    def __enter__(self): return self
    def __exit__(self, *_): return None

    def post(self, url, *, files, data):
        type(self).seen = {"url": url, "files": files, "data": data, "timeout": self.timeout}
        if type(self).error:
            raise type(self).error
        assert type(self).response is not None
        return type(self).response


def _mineru_response(status: int, payload) -> httpx.Response:
    request = httpx.Request("POST", "http://mineru-api:8000/file_parse")
    return httpx.Response(status, request=request, json=payload)


def _valid_mineru_payload(**result_overrides):
    result = {"md_content": "# 标题\n正文", "middle_json": json.dumps({"pdf_info": [{"page_idx": 0}]}),
              "content_list": json.dumps([{"page_idx": 0, "text": "正文"}])}
    result.update(result_overrides)
    return {"version": "3.4.4", "backend": "pipeline", "results": {"scan": result}}


@pytest.fixture
def fake_mineru_client(monkeypatch):
    FakeMinerUClient.response = None; FakeMinerUClient.error = None; FakeMinerUClient.seen = {}
    monkeypatch.setattr(parser_runtime.httpx, "Client", FakeMinerUClient)
    monkeypatch.setenv("DATAFORGE_MINERU_URL", "http://mineru-api:8000/")
    monkeypatch.setenv("DATAFORGE_MINERU_TIMEOUT_SECONDS", "1800")


def test_mineru_adapter_sends_pinned_pipeline_contract(fake_mineru_client):
    FakeMinerUClient.response = _mineru_response(200, _valid_mineru_payload())
    parsed = parser_runtime.parse_with_mineru(filename="scan.pdf", payload=b"pdf")
    assert parsed.version == "3.4.4" and parsed.markdown.startswith("# 标题")
    assert FakeMinerUClient.seen == {
        "url": "http://mineru-api:8000/file_parse",
        "files": {"files": ("scan.pdf", b"pdf", "application/pdf")},
        "data": {
            "backend": "pipeline", "parse_method": "auto", "lang_list": "ch", "formula_enable": "true",
            "table_enable": "true", "return_md": "true", "return_middle_json": "true",
            "return_model_output": "false", "return_content_list": "true", "return_images": "false",
            "response_format_zip": "false", "return_original_file": "false",
        },
        "timeout": 1800,
    }


@pytest.mark.parametrize(("payload", "message"), [
    (_valid_mineru_payload(md_content="  "), "Markdown"),
    (_valid_mineru_payload(middle_json="{"), "Middle JSON"),
    ({"version": "3.4.5", "backend": "pipeline", "results": {"scan": {}}}, "版本必须为 3.4.4"),
    ({"version": "3.4.4", "backend": "vlm-engine", "results": {"scan": {}}}, "pipeline"),
])
def test_mineru_adapter_rejects_invalid_contract(fake_mineru_client, payload, message):
    FakeMinerUClient.response = _mineru_response(200, payload)
    with pytest.raises(ValueError, match=message):
        parser_runtime.parse_with_mineru(filename="scan.pdf", payload=b"pdf")


def test_mineru_adapter_reports_http_and_transport_failures(fake_mineru_client):
    FakeMinerUClient.response = _mineru_response(409, {"error": "pipeline failed"})
    with pytest.raises(ValueError, match="pipeline failed"):
        parser_runtime.parse_with_mineru(filename="scan.pdf", payload=b"pdf")
    FakeMinerUClient.error = httpx.ConnectError("refused", request=httpx.Request("POST", "http://mineru-api:8000"))
    with pytest.raises(ValueError, match="服务不可达"):
        parser_runtime.parse_with_mineru(filename="scan.pdf", payload=b"pdf")
    FakeMinerUClient.error = httpx.ReadTimeout("slow", request=httpx.Request("POST", "http://mineru-api:8000"))
    with pytest.raises(ValueError, match="1800 秒"):
        parser_runtime.parse_with_mineru(filename="scan.pdf", payload=b"pdf")


def test_mineru_content_list_is_grouped_by_one_based_page_anchor():
    pages = parser_runtime.content_list_pages([
        {"page_idx": 1, "text": "第二页 A"}, {"page_idx": 0, "text": "第一页"},
        {"page_idx": 1, "text": "第二页 B"}, {"text": "无页码"},
    ])
    assert pages == [
        {"page_index": 0, "page": 1, "text": "第一页"},
        {"page_index": 1, "page": 2, "text": "第二页 A\n\n第二页 B"},
    ]


def test_worker_preserves_runner_persisted_ocr_failure(monkeypatch):
    failed = []; job = SimpleNamespace(id="job-ocr", status="running")

    class FakeStore:
        def __init__(self, _): pass
        def assert_schema_current(self): pass
        def claim_job(self, _): return job
        def get_job(self, _): return SimpleNamespace(status="failed", error="MinerU OCR 服务不可达")
        def mark_job_failed(self, job_id, error): failed.append((job_id, error))

    monkeypatch.setattr(worker, "V7Store", FakeStore)
    monkeypatch.setattr(worker.httpx, "post", lambda *_, **__: (_ for _ in ()).throw(
        worker.httpx.HTTPStatusError("422", request=worker.httpx.Request("POST", "http://runner/internal/jobs"),
                                    response=worker.httpx.Response(422))))
    settings = SimpleNamespace(platform_database_url="sqlite:///worker.sqlite3", runner_url="http://runner",
                               runner_service_token="token", runner_timeout_seconds=1860)
    assert worker.run_once(settings) == 0 and failed == []


def test_mineru_pipeline_gpu_deployment_contract():
    root = Path(__file__).parents[1]
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (root / "deploy" / "mineru" / "Dockerfile").read_text(encoding="utf-8")
    assert "image: ${DATAFORGE_MINERU_IMAGE:-dataforge-mineru:3.4.4}" in compose
    assert '127.0.0.1:${DATAFORGE_MINERU_HOST_PORT:-18000}:8000' in compose
    assert 'MINERU_API_MAX_CONCURRENT_REQUESTS: "1"' in compose
    assert 'MINERU_PROCESSING_WINDOW_SIZE: "16"' in compose
    assert 'device_ids: ["${DATAFORGE_MINERU_GPU_DEVICE:-0}"]' in compose
    assert "DATAFORGE_MINERU_URL: http://mineru-api:8000" in compose
    assert "DATAFORGE_RUNNER_TIMEOUT_SECONDS: ${DATAFORGE_RUNNER_TIMEOUT_SECONDS:-1860}" in compose
    assert "networks: [private, parser]" in compose and "parser:\n    internal: true" in compose
    assert "mineru[pipeline]==3.4.4" in dockerfile and "pytorch:2.6.0-cuda12.4-cudnn9-runtime" in dockerfile
    assert "libglib2.0-0" in dockerfile and "import cv2" in dockerfile
    assert "ARG MINERU_MODEL_SOURCE=modelscope" in dockerfile
    assert 'mineru-models-download -s "${MINERU_MODEL_SOURCE}" -m pipeline' in dockerfile
    assert "find_spec('vllm') is None" in dockerfile and "find_spec('sglang') is None" in dockerfile
    assert all(term not in compose.lower() for term in ("vllm", "flash", "mineru-router", "vlm"))


def test_parser_artifact_is_deleted_and_listed_for_v7_rebuild(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    documents = store.create_document_library("Artifact 生命周期")
    stored = objects.put_bytes("sources/lifecycle.txt", b"lifecycle", "text/plain")
    source = store.create_source(library_id=documents["id"], name="生命周期", filename="lifecycle.txt",
                                 object_key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes,
                                 mime_type="text/plain")
    knowledge = store.create_knowledge_library("artifact-text", "产物文本", "text")
    job = store.create_knowledge_job([source["version"]["id"]], {"text": knowledge["id"]}, "flow_standard-text")
    run = store.start_flow_run(job["id"])
    middle = objects.put_bytes("parser-artifacts/middle.json", b"{}", "application/json")
    with store.sessions.begin() as session:
        session.add(Artifact(id="artifact_middle", flow_run_id=run["id"], source_version_id=source["version"]["id"],
                             type_code="parser.middle-json", uri="object:///parser-artifacts/middle.json",
                             data_json={"object_key": middle.key}, checksum=middle.sha256))
    store.finish_flow_run(run["id"]); store.mark_job_failed(job["id"], "lifecycle fixture")
    assert middle.key in store.v7_rebuild_manifest()["object_keys"]
    store.request_document_deletion(source_ids=[source["id"]]); claimed = store.claim_document_deletion_job("test")
    assert middle.key in claimed.object_keys
    assert DocumentDeletionService(store, objects).run(claimed)["status"] == "completed"
    assert not (tmp_path / "objects" / middle.key).exists()
    with store.sessions() as session:
        assert session.get(Artifact, "artifact_middle") is None


@pytest.mark.parametrize("pdf_kind", ["text", "scan"])
def test_pdf_runner_uses_mineru_and_persists_page_anchors_and_middle_json(tmp_path, monkeypatch, pdf_kind):
    from dataforge.v7.models import Artifact
    from dataforge.v7.parser_runtime import MinerUParseResult

    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    documents = store.create_document_library("扫描资料")
    filename = f"{pdf_kind}.pdf"
    stored = objects.put_bytes(f"sources/{filename}", f"%PDF-1.7 fake {pdf_kind} content".encode(), "application/pdf")
    source = store.create_source(library_id=documents["id"], name=pdf_kind, filename=filename, object_key=stored.key,
                                 sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="application/pdf")
    library = store.create_knowledge_library("ocr-text", "OCR 文本", "text")
    job = store.create_knowledge_job([source["version"]["id"]], {"text": library["id"]}, "flow_standard-text")
    monkeypatch.setattr(runner, "parse_with_mineru", lambda **_: MinerUParseResult(
        markdown="# 扫描标题\n第一页内容\n\n第二页内容",
        middle_json={"pdf_info": [{"page_idx": 0}, {"page_idx": 1}]},
        content_list=[{"page_idx": 0, "type": "text", "text": "第一页内容"},
                      {"page_idx": 1, "type": "text", "text": "第二页内容"}],
        version="3.4.4",
    ))

    result = execute_job(store, objects, job["id"])

    assert result["status"] == "completed"
    detail = store.source_detail(source["id"])
    assert detail["document_ir"]["parser_adapter"] == "mineru-pipeline-gpu"
    assert detail["document_ir"]["parser_profile"] == "pipeline:auto"
    assert [chunk["anchor"]["page"] for chunk in detail["source_chunks"]] == [1, 2]
    assert len(detail["parser_artifacts"]) == 1
    artifact = detail["parser_artifacts"][0]
    assert artifact["type"] == "parser.middle-json" and artifact["metadata"]["mineru_version"] == "3.4.4"
    with store.sessions() as session:
        persisted = session.get(Artifact, artifact["id"])
        assert persisted.source_version_id == source["version"]["id"]
    key = artifact["metadata"]["object_key"]
    assert b'"pdf_info"' in objects.get_bytes(key)
    assert store.list_knowledge_items(library["id"])


def test_pdf_middle_object_is_compensated_when_artifact_persistence_fails(tmp_path, monkeypatch):
    from dataforge.v7.parser_runtime import MinerUParseResult

    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    documents = store.create_document_library("补偿资料")
    stored = objects.put_bytes("sources/compensate.pdf", b"%PDF-1.7", "application/pdf")
    source = store.create_source(library_id=documents["id"], name="补偿", filename="compensate.pdf", object_key=stored.key,
                                 sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="application/pdf")
    library = store.create_knowledge_library("ocr-compensate", "OCR 补偿", "text")
    job = store.create_knowledge_job([source["version"]["id"]], {"text": library["id"]}, "flow_standard-text")
    monkeypatch.setattr(runner, "parse_with_mineru", lambda **_: MinerUParseResult(
        markdown="补偿测试", middle_json={"pdf_info": [{"page_idx": 0}]},
        content_list=[{"page_idx": 0, "text": "补偿测试"}], version="3.4.4",
    ))
    original = store.record_flow_node

    def fail_parser_artifact(flow_run_id, node_id, input_artifact_ids, outputs):
        if any(item.get("_parser_artifacts") for item in outputs):
            raise RuntimeError("artifact database failure")
        return original(flow_run_id, node_id, input_artifact_ids, outputs)

    monkeypatch.setattr(store, "record_flow_node", fail_parser_artifact)
    with pytest.raises(RuntimeError, match="artifact database failure"):
        execute_job(store, objects, job["id"])
    assert not list((tmp_path / "objects" / "parser-artifacts").rglob("mineru-middle.json"))


def test_pdf_parser_failure_marks_job_and_source_once(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    documents = store.create_document_library("失败扫描资料")
    stored = objects.put_bytes("sources/fail.pdf", b"%PDF-1.7 broken", "application/pdf")
    source = store.create_source(library_id=documents["id"], name="失败扫描件", filename="fail.pdf", object_key=stored.key,
                                 sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="application/pdf")
    library = store.create_knowledge_library("ocr-fail", "OCR 失败", "text")
    job = store.create_knowledge_job([source["version"]["id"]], {"text": library["id"]}, "flow_standard-text")
    monkeypatch.setattr(runner, "parse_with_mineru", lambda **_: (_ for _ in ()).throw(ValueError("MinerU OCR 服务不可达")))

    with pytest.raises(ValueError, match="MinerU OCR 服务不可达"):
        execute_job(store, objects, job["id"])

    assert store.get_job(job["id"]).status == "failed"
    assert store.source_versions(source["id"])[0]["extraction_status"] == "failed"
    assert [item["action"] for item in store.job_logs(job["id"])].count("knowledge_job.failed") == 1
    assert store.list_knowledge_items(library["id"]) == []


def test_published_flow_snapshot_executes_expanded_dag_and_records_lineage(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "guide", "新上传资料应被作为正式来源保留。")
    library = store.create_knowledge_library("catalog-text", "受控文本", "text")
    job = store.create_knowledge_job([source["version"]["id"]], {"text": library["id"]}, "flow_standard-text")
    assert job["execution_snapshot_id"]
    result = execute_job(store, objects, job["id"])
    assert result["status"] == "completed"
    run = store.flow_run_detail(result["flow_run_id"])
    assert any(node["node_id"].endswith("source-chunks") for node in run["nodes"])
    assert run["artifacts"]


def test_unpublished_prompt_is_rejected_and_only_three_builtin_types_are_seeded(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    definition = builtin_flow_definition(["text"])
    next(node for node in definition["nodes"] if node["id"] == "generate-text")["params"]["prompt_template_revision_id"] = "not-published"
    with pytest.raises(ValueError, match="Prompt Generator"):
        store.create_flow_template("bad-prompt", "错误提示", ["text"], definition)
    assert {item["code"] for item in store.list_knowledge_type_definitions()} == {"text", "qa", "graph"}
    assert "medical_fact" not in {item["code"] for item in store.list_knowledge_type_definitions()}
    assert all(item["fields"] == DEFAULT_INDEX_FIELD_MAPPING for item in store.list_index_profiles())


def test_custom_type_is_draft_until_governance_dependencies_are_published(tmp_path):
    store = _store(tmp_path)
    quality = store.list_quality_profiles()[0]["revisions"][0]["id"]
    index = store.list_index_profiles()[0]["id"]
    created = store.create_knowledge_type("policy", "政策条款", "策", {"type": "object", "required": ["title"]},
                                          "title", ["title"], "single", quality, [index])
    assert created["status"] == "draft"
    assert store.publish_knowledge_type(created["id"])["status"] == "published"


def test_library_project_codes_are_automatic_and_http_rejects_client_codes(tmp_path):
    store = _store(tmp_path)
    library = store.create_knowledge_library("自动编码", "text")
    project = store.create_project("自动项目")
    assert library["code"].startswith("KL-") and project["code"].startswith("PRJ-")
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(store.engine.url))
    client = TestClient(create_app(settings))
    assert client.post("/api/knowledge-libraries", json={"code": "bad", "name": "拒绝", "knowledge_type": "text"}).status_code == 422
    assert client.post("/api/projects", json={"code": "bad", "name": "拒绝"}).status_code == 422


def test_document_template_binding_creates_stable_result_library_and_tracks_incremental_work(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    document = store.create_document_library("自动处理资料")
    stored = objects.put_bytes("sources/auto.txt", "绑定后自动处理".encode("utf-8"), "text/plain")
    store.create_source(library_id=document["id"], name="自动", filename="auto.txt", object_key=stored.key,
                        sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="text/plain")
    binding = store.bind_document_library_template(document["id"], "flow_standard-text")
    assert binding["outputs"][0]["knowledge_library"]["code"].startswith("KL-")
    assert binding["pending_file_count"] == 1
    jobs = store.process_document_library(document["id"])
    assert len(jobs) == 1 and jobs[0]["document_library_template_binding_id"] == binding["id"]
    assert store.process_document_library(document["id"]) == []


def test_selected_document_sources_only_queue_pending_current_versions_for_each_binding(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    document = store.create_document_library("定向处理资料")

    def add_source(code: str):
        stored = objects.put_bytes(f"sources/{code}.txt", code.encode("utf-8"), "text/plain")
        return store.create_source(library_id=document["id"], name=code, filename=f"{code}.txt", object_key=stored.key,
                                   sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type="text/plain")

    first, second, deleted = add_source("first"), add_source("second"), add_source("deleted")
    text_binding = store.bind_document_library_template(document["id"], "flow_standard-text")
    qa_binding = store.bind_document_library_template(document["id"], "flow_standard-qa")
    jobs = store.process_selected_document_sources(document["id"], [first["id"], second["id"]])
    assert {job["document_library_template_binding_id"] for job in jobs} == {text_binding["id"], qa_binding["id"]}
    assert all(set(job["source_version_ids"]) == {first["version"]["id"], second["version"]["id"]} for job in jobs)
    assert store.process_selected_document_sources(document["id"], [first["id"], second["id"]]) == []

    for job in jobs:
        store.complete_job(job["id"])
    with store.sessions.begin() as session:
        session.get(__import__("dataforge.v7.models", fromlist=["Source"]).Source, deleted["id"]).status = "deleted"
    assert store.process_selected_document_sources(document["id"], [first["id"], deleted["id"]]) == []

    other = _source(store, objects, "other-library", "其他文档库文件")
    with pytest.raises(ValueError, match="不属于当前文档库"):
        store.process_selected_document_sources(document["id"], [other["id"]])

    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(store.engine.url))
    client = TestClient(create_app(settings))
    pending = add_source("pending")
    response = client.post(f"/api/document-libraries/{document['id']}/process-selected", json={"source_ids": [pending["id"]]})
    assert response.status_code == 202
    assert {job["document_library_template_binding_id"] for job in response.json()["jobs"]} == {text_binding["id"], qa_binding["id"]}
    assert client.post(f"/api/document-libraries/{document['id']}/process-selected", json={"source_ids": [pending["id"]]}).json() == {"jobs": []}
    assert client.post(f"/api/document-libraries/{document['id']}/process-selected", json={"source_ids": ["missing"]}).status_code == 422
    assert client.post(f"/api/document-libraries/{document['id']}/process-selected", json={"source_ids": [other["id"]]}).status_code == 422
    assert client.post(f"/api/document-libraries/{document['id']}/process-selected", json={"source_ids": []}).status_code == 422


def test_dynamic_routing_snapshot_uses_published_profile_mapping_and_multi_source_delete_keeps_graph(tmp_path):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    quality = store.list_quality_profiles()[0]["revisions"][0]["id"]
    index = store.list_index_profiles()[0]["id"]
    extension = store.create_knowledge_type("multi-note", "多来源备注", "多", {"type": "object", "required": ["title"]},
                                            "title", ["title"], "multiple", quality, [index])
    store.publish_knowledge_type(extension["id"])
    library = store.create_knowledge_library("动态路由", "multi-note")
    project = store.create_project("动态路由项目")
    task = store.create_project_task(project["id"], "route", "路由")
    store.put_route(task["id"], "general", [library["id"]])
    route = store.routing_snapshot(project["id"])["routes"][0]["libraries"][0]
    assert route["partition_name"].startswith("kl_") and route["indexes"][0]["fields"]["vector"] == "vector"
    first = _source(store, objects, "multi1", "第一份来源")
    second = _source(store, objects, "multi2", "第二份来源")
    template = store.create_flow_template("multi-note-template", "多来源模板", ["multi-note"], builtin_flow_definition(["multi-note"]))
    store.publish_flow_template(template["id"])
    job = store.create_knowledge_job([first["version"]["id"], second["version"]["id"]], {"multi-note": library["id"]}, template["id"])
    store.apply_knowledge_output(job["id"], "multi-note", [{"source_knowledge_id": "same", "canonical_content": "备注", "data_json": {"title": "备注"},
        "source_version_ids": [first["version"]["id"], second["version"]["id"]]}])
    store.complete_job(job["id"])
    check = store.document_deletion_preflight(source_ids=[first["id"]])
    store.request_document_deletion(source_ids=[first["id"]])
    assert store.list_knowledge_items(library["id"])[0]["status"] == "active"


def test_structured_generator_repairs_once_then_refuses_invalid_output(monkeypatch, tmp_path):
    source = type("Source", (), {"id": "source", "original_filename": "sample.txt"})()
    version = type("Version", (), {"id": "version"})()
    contract = {"schema": {"type": "object", "required": ["title"]}, "prompt": "生成", "canonical_field": "title", "identity_fields": ["title"]}
    chunk = {"content": "内容", "chunk_index": 0, "source_chunk_id": "chunk-0"}
    replies = iter([{"items": [{"wrong": "x"}]}, {"items": [{"title": "fixed"}]}])
    monkeypatch.setattr(runner, "_llm_json", lambda _: next(replies))
    assert runner._structured_candidates(source, version, "custom", chunk, contract)[0]["canonical_content"] == "fixed"
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"wrong": "x"}]})
    with pytest.raises(ValueError, match="一次修复"):
        runner._structured_candidates(source, version, "custom", chunk, contract)


def test_qwen_generation_is_initialized_once_and_returns_all_valid_items(monkeypatch, tmp_path):
    calls = []

    class SharedGlobalLlm:
        @staticmethod
        def get_app_config():
            if not calls:
                raise RuntimeError("not initialized")
            return {"ready": True}

        @staticmethod
        def init_app(path):
            calls.append(("init", Path(path)))

        @staticmethod
        def chat(messages, logical_model, org_code, **kwargs):
            calls.append(("chat", messages, logical_model, org_code, kwargs))
            return '{"items": []}'

    monkeypatch.setitem(__import__("sys").modules, "global_llm", SharedGlobalLlm)
    config = tmp_path / "llm_local.yaml"
    config.write_text("app_id: dataforge\n", encoding="utf-8")
    monkeypatch.setenv("DATAFORGE_LLM_CONFIG_PATH", str(config))
    monkeypatch.setenv("DATAFORGE_LLM_ORG_CODE", "org-1")
    assert runner._llm_json("test") == {"items": []}
    assert calls[0] == ("init", config)
    _, _, model, org_code, kwargs = calls[1]
    assert model == "qwen3_32b" and org_code == "org-1"
    assert kwargs["response_format"] == {"type": "json_object"}

    source = type("Source", (), {"id": "source", "original_filename": "sample.txt"})()
    version = type("Version", (), {"id": "version"})()
    contract = {"schema": {"type": "object", "required": ["question", "answer"]},
                "canonical_field": "answer", "identity_fields": ["question"]}
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [
        {"question": "问题 1", "answer": "答案 1"}, {"question": "问题 2", "answer": "答案 2"},
        {"question": "问题 3", "answer": "答案 3"},
    ]})
    values = runner._structured_candidates(source, version, "qa", {
        "content": "当前分块", "chunk_index": 2, "source_chunk_id": "chunk-2",
    }, contract)
    assert [item["data_json"]["question"] for item in values] == ["问题 1", "问题 2", "问题 3"]
    assert "question 和 answer" in runner._chunk_prompt("qa", {**contract, "prompt": "自定义提示"}, "当前分块")


def test_runner_app_initializes_global_llm_on_startup(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "_initialize_global_llm", lambda: calls.append("initialized"))
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=f"sqlite:///{tmp_path / 'runner.sqlite3'}")
    app = runner.create_app(settings, check_schema=False)
    assert app.title == "DataForge V7 Runner" and calls == ["initialized"]


def test_chunk_failures_are_retryable_without_replacing_successful_chunks(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "chunk-retry", "A" * 796 + " PASS" + "FAIL" + "B" * 795)
    library = store.create_knowledge_library("分块问答", "qa")
    job = store.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")

    def first_attempt(prompt):
        if "FAIL" in prompt:
            raise ValueError("temporary qwen failure")
        return {"items": [{"question": "第一块", "answer": "第一块答案"}]}

    monkeypatch.setattr(runner, "_llm_json", first_attempt)
    result = execute_job(store, objects, job["id"])
    assert result["status"] == "completed_with_warnings"
    assert store.list_knowledge_jobs()[0]["failed_chunk_count"] == 1
    assert len([item for item in store.list_knowledge_items(library["id"]) if item["status"] == "active"]) == 1

    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(store.engine.url))
    client = TestClient(create_app(settings))
    listed = next(item for item in client.get("/api/knowledge-jobs").json() if item["id"] == job["id"])
    assert listed["status"] == "completed_with_warnings" and listed["warning_count"] == listed["failed_chunk_count"] == 1
    retried = client.post("/api/knowledge-jobs/batch-actions", json={"job_ids": [job["id"]], "action": "retry"})
    assert retried.status_code == 200 and retried.json()[0]["status"] == "queued"
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"question": "第二块", "answer": "第二块答案"}]})
    result = execute_job(store, objects, job["id"])
    assert result["status"] == "completed"
    assert store.job_generation_results(job["id"], failed_only=True) == []
    assert {item["data"]["question"] for item in store.list_knowledge_items(library["id"]) if item["status"] == "active"} == {"第一块", "第二块"}


def test_all_failed_qwen_chunks_leave_no_knowledge_and_api_exposes_details(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "all-failed", "仅用于失败验证的资料")
    library = store.create_knowledge_library("失败问答", "qa")
    job = store.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: (_ for _ in ()).throw(ValueError("qwen unavailable")))
    assert execute_job(store, objects, job["id"])["status"] == "failed"
    assert store.list_knowledge_items(library["id"]) == []
    settings = Settings(project_root=tmp_path, state_dir=tmp_path / "state", database_url=str(store.engine.url))
    response = TestClient(create_app(settings)).get(f"/api/knowledge-jobs/{job['id']}/chunk-generations?failed_only=true")
    assert response.status_code == 200 and response.json()[0]["error"] == "qwen unavailable"


def test_successful_empty_chunk_withdraws_its_previous_knowledge(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "empty-result", "可先生成后撤销的资料")
    library = store.create_knowledge_library("空结果问答", "qa")
    first_job = store.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"question": "旧问题", "answer": "旧答案"}]})
    assert execute_job(store, objects, first_job["id"])["status"] == "completed"
    assert [item["status"] for item in store.list_knowledge_items(library["id"])] == ["active"]

    replacement_payload = objects.put_bytes("sources/empty-result-v2.txt", "可先生成后撤销的资料".encode("utf-8"), "text/plain")
    replacement = store.replace_source(source_id=source["id"], filename="empty-result.txt", object_key=replacement_payload.key,
                                       sha256=replacement_payload.sha256, size_bytes=replacement_payload.size_bytes, mime_type="text/plain")
    second_job = store.create_knowledge_job([replacement["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": []})
    assert execute_job(store, objects, second_job["id"])["status"] == "completed"
    assert [item["status"] for item in store.list_knowledge_items(library["id"])] == ["inactive"]


def test_successful_chunk_replaces_old_knowledge_when_output_identity_changes(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "identity-change", "同一分块更新资料")
    library = store.create_knowledge_library("身份变更问答", "qa")
    first_job = store.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"question": "旧问题", "answer": "旧答案"}]})
    assert execute_job(store, objects, first_job["id"])["status"] == "completed"

    replacement_payload = objects.put_bytes("sources/identity-change-v2.txt", "同一分块更新资料".encode("utf-8"), "text/plain")
    replacement = store.replace_source(source_id=source["id"], filename="identity-change.txt", object_key=replacement_payload.key,
                                       sha256=replacement_payload.sha256, size_bytes=replacement_payload.size_bytes, mime_type="text/plain")
    second_job = store.create_knowledge_job([replacement["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"question": "新问题", "answer": "新答案"}]})
    assert execute_job(store, objects, second_job["id"])["status"] == "completed"
    assert {item["data"]["question"] for item in store.list_knowledge_items(library["id"]) if item["status"] == "active"} == {"新问题"}


def test_version_chunk_reduction_cleans_absent_chunks_only_after_all_success(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "reduced", "A" * 800 + "B" * 800)
    library = store.create_knowledge_library("缩减问答", "qa")
    first_job = store.create_knowledge_job([source["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda prompt: {"items": [{
        "question": "第一块" if "A" * 20 in prompt else "第二块", "answer": "答案",
    }]})
    assert execute_job(store, objects, first_job["id"])["status"] == "completed"
    assert len([item for item in store.list_knowledge_items(library["id"]) if item["status"] == "active"]) == 2

    reduced = objects.put_bytes("sources/reduced-v2.txt", ("A" * 800).encode("utf-8"), "text/plain")
    replacement = store.replace_source(source_id=source["id"], filename="reduced.txt", object_key=reduced.key,
                                       sha256=reduced.sha256, size_bytes=reduced.size_bytes, mime_type="text/plain")
    second_job = store.create_knowledge_job([replacement["version"]["id"]], {"qa": library["id"]}, "flow_standard-qa")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"question": "第一块", "answer": "新答案"}]})
    assert execute_job(store, objects, second_job["id"])["status"] == "completed"
    active = [item for item in store.list_knowledge_items(library["id"]) if item["status"] == "active"]
    assert len(active) == 1 and active[0]["data"]["answer"] == "新答案"


def test_multitype_failure_skips_absent_chunk_cleanup_for_the_source_version(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "multi-reduced", "A" * 800 + "B" * 800)
    text = store.create_knowledge_library("多类型缩减文本", "text")
    qa = store.create_knowledge_library("多类型缩减问答", "qa")
    graph = store.create_knowledge_library("多类型缩减图谱", "graph")
    outputs = {"text": text["id"], "qa": qa["id"], "graph": graph["id"]}
    first_job = store.create_knowledge_job([source["version"]["id"]], outputs, "flow_standard-multi")
    monkeypatch.setattr(runner, "_llm_json", lambda prompt: {"items": [
        {"question": "问题 A" if "A" * 20 in prompt else "问题 B", "answer": "答案"}
        if "question 和 answer" in prompt else
        {"subject": "实体 A" if "A" * 20 in prompt else "实体 B", "predicate": "关联", "object": "目标"}
    ]})
    assert execute_job(store, objects, first_job["id"])["status"] == "completed"
    assert len([item for item in store.list_knowledge_items(qa["id"]) if item["status"] == "active"]) == 2

    reduced = objects.put_bytes("sources/multi-reduced-v2.txt", ("A" * 800).encode("utf-8"), "text/plain")
    replacement = store.replace_source(source_id=source["id"], filename="multi-reduced.txt", object_key=reduced.key,
                                       sha256=reduced.sha256, size_bytes=reduced.size_bytes, mime_type="text/plain")
    second_job = store.create_knowledge_job([replacement["version"]["id"]], outputs, "flow_standard-multi")

    def partial_failure(prompt):
        if "subject、predicate、object" in prompt:
            raise ValueError("graph qwen failure")
        return {"items": [{"question": "问题 A", "answer": "新答案"}]}

    monkeypatch.setattr(runner, "_llm_json", partial_failure)
    assert execute_job(store, objects, second_job["id"])["status"] == "completed_with_warnings"
    qa_active = [item for item in store.list_knowledge_items(qa["id"]) if item["status"] == "active"]
    assert {item["data"]["question"] for item in qa_active} == {"问题 A", "问题 B"}


def test_failed_only_retry_can_complete_cross_type_chunk_cleanup(tmp_path, monkeypatch):
    store = _store(tmp_path); objects = LocalObjectStore(tmp_path / "objects")
    source = _source(store, objects, "retry-cleanup", "A" * 800 + "B" * 800)
    text = store.create_knowledge_library("重试清理文本", "text")
    qa = store.create_knowledge_library("重试清理问答", "qa")
    graph = store.create_knowledge_library("重试清理图谱", "graph")
    outputs = {"text": text["id"], "qa": qa["id"], "graph": graph["id"]}
    job = store.create_knowledge_job([source["version"]["id"]], outputs, "flow_standard-multi")
    monkeypatch.setattr(runner, "_llm_json", lambda prompt: {"items": [
        {"question": "问题 A" if "A" * 20 in prompt else "问题 B", "answer": "答案"}
        if "question 和 answer" in prompt else
        {"subject": "实体 A" if "A" * 20 in prompt else "实体 B", "predicate": "关联", "object": "目标"}
    ]})
    assert execute_job(store, objects, job["id"])["status"] == "completed"
    # Use the same source version but change the persisted chunk view to model
    # a retried processing run with a smaller current source-chunk set.
    with store.sessions.begin() as session:
        version = session.get(__import__("dataforge.v7.models", fromlist=["SourceVersion"]).SourceVersion, source["version"]["id"])
        version.object_key = objects.put_bytes("sources/retry-cleanup-reduced.txt", ("A" * 800).encode("utf-8"), "text/plain").key


    def fail_graph(prompt):
        if "subject、predicate、object" in prompt:
            raise ValueError("graph temporary failure")
        return {"items": [{"question": "问题 A", "answer": "新答案"}]}

    monkeypatch.setattr(runner, "_llm_json", fail_graph)
    # Requeue the same processing record to exercise its failed-only retry
    # boundary without changing its formal source-version identity.
    with store.sessions.begin() as session:
        persisted = session.get(__import__("dataforge.v7.models", fromlist=["KnowledgeJob"]).KnowledgeJob, job["id"])
        persisted.status, persisted.stage = "queued", "queued"
    assert execute_job(store, objects, job["id"])["status"] == "completed_with_warnings"
    store.manage_jobs([job["id"]], "retry")
    monkeypatch.setattr(runner, "_llm_json", lambda _: {"items": [{"subject": "实体 A", "predicate": "关联", "object": "目标"}]})
    assert execute_job(store, objects, job["id"])["status"] == "completed"
    assert {item["data"]["question"] for item in store.list_knowledge_items(qa["id"]) if item["status"] == "active"} == {"问题 A"}


def test_index_profile_publish_requires_collection_validator(tmp_path):
    store = _store(tmp_path)
    created = store.create_index_profile("custom-index", "text", "existing_collection", "custom-embed", "model", 8, "COSINE", None,
                                         DEFAULT_INDEX_FIELD_MAPPING)
    with pytest.raises(ValueError, match="未配置可验证"):
        store.publish_index_profile(created["id"], None)
    seen = []
    assert store.publish_index_profile(created["id"], lambda name, fields, dimension: seen.append((name, fields, dimension)))["status"] == "published"
    assert seen == [("existing_collection", DEFAULT_INDEX_FIELD_MAPPING, 8)]


def test_prompt_and_quality_revisions_require_explicit_publish(tmp_path):
    store = _store(tmp_path)
    prompt = store.create_prompt_template("safe-prompt", "受控提示", "只生成结构化结果", {}, {"type": "object"})
    quality = store.create_quality_profile("safe-quality", "受控质量", {"pass_score": 0.9, "review_score": 0.7})
    assert prompt["status"] == quality["status"] == "draft"
    assert store.publish_prompt_template(prompt["id"])["status"] == "published"
    assert store.publish_quality_profile(quality["id"])["status"] == "published"


def test_mysql_upgrade_backfills_sink_json_without_a_server_default(monkeypatch):
    migration_path = Path(__file__).parents[1] / "src" / "dataforge" / "v7" / "alembic" / "versions" / "20260811_02_v7_governed_catalog.py"
    spec = importlib.util.spec_from_file_location("v7_governed_catalog_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Inspector:
        existing_columns = {
            "knowledge_types": {"kind", "current_revision_id"},
            "knowledge_flow_template_revisions": {"execution_snapshot_id"},
            "knowledge_libraries": {"knowledge_type_revision_id"},
            "knowledge_items": {"knowledge_type_revision_id"},
            "knowledge_jobs": {"execution_snapshot_id"},
        }

        def get_columns(self, table):
            return [{"name": name} for name in self.existing_columns[table]]

        def get_table_names(self):
            return ["knowledge_flow_template_revisions"]

    bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "mysql"})()})()
    added, executed, altered = [], [], []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _: Inspector())
    monkeypatch.setattr(migration.op, "add_column", lambda table, column: added.append((table, column)))
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: altered.append((args, kwargs)))
    monkeypatch.setattr(migration.Base.metadata, "create_all", lambda *args, **kwargs: None)

    migration.upgrade()

    assert len(added) == 1
    table, column = added[0]
    assert table == "knowledge_jobs"
    assert column.name == "sink_library_ids"
    assert column.nullable is True
    assert column.server_default is None
    assert executed == ["UPDATE knowledge_jobs SET sink_library_ids = JSON_OBJECT() WHERE sink_library_ids IS NULL"]
    assert len(altered) == 1
    alter_args, alter_kwargs = altered[0]
    assert alter_args == ("knowledge_jobs", "sink_library_ids")
    assert isinstance(alter_kwargs["existing_type"], migration.sa.JSON)
    assert alter_kwargs["existing_nullable"] is True
    assert alter_kwargs["nullable"] is False


def test_operator_examples_migration_backfills_mysql_json_without_defaults(monkeypatch):
    migration_path = Path(__file__).parents[1] / "src" / "dataforge" / "v7" / "alembic" / "versions" / "20260814_01_operator_examples.py"
    spec = importlib.util.spec_from_file_location("operator_examples_migration", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)

    class Inspector:
        def get_columns(self, table):
            return [{"name": "id"}]

    bind = type("Bind", (), {"dialect": type("Dialect", (), {"name": "mysql"})()})()
    added, executed, altered = [], [], []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _: Inspector())
    monkeypatch.setattr(migration.op, "add_column", lambda table, column: added.append((table, column)))
    monkeypatch.setattr(migration.op, "execute", lambda statement: executed.append(str(statement)))
    monkeypatch.setattr(migration.op, "alter_column", lambda *args, **kwargs: altered.append((args, kwargs)))

    migration.upgrade()

    assert [(table, column.name, column.nullable) for table, column in added] == [
        ("operator_definitions", "description", True),
        ("operator_versions", "input_example", True),
        ("operator_versions", "output_example", True),
    ]
    assert executed == [
        "UPDATE operator_definitions SET description = '' WHERE description IS NULL",
        "UPDATE operator_versions SET input_example = JSON_OBJECT() WHERE input_example IS NULL",
        "UPDATE operator_versions SET output_example = JSON_OBJECT() WHERE output_example IS NULL",
    ]
    assert len(altered) == 3 and all(kwargs["nullable"] is False for _, kwargs in altered)
