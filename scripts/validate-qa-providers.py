"""Opt-in real-model QA acceptance in a disposable LOCAL SQLite sandbox.

No remote deployment, database, source files or knowledge stores are modified.
Run after conda activate sun. Credentials, if needed, come from the environment.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from sqlalchemy import func, select

from dataforge.v7.catalog import builtin_flow_definition
from dataforge.v7.llm_serving import configure_llm_serving_registry
from dataforge.v7.migrations import upgrade
from dataforge.v7.models import FlowRunSinkPreview, KnowledgeItem, VectorSyncJob
from dataforge.v7.runner import execute_debug_run
from dataforge.v7.sample_data import SampleDataService
from dataforge.v7.store import V7Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        parser.error("rounds must be between 1 and 10")
    os.environ["DATAFORGE_DEFAULT_LLM_BASE_URL"] = args.base_url
    os.environ["DATAFORGE_DEFAULT_LLM_MODEL"] = args.model
    os.environ["DATAFORGE_DEFAULT_LLM_MAX_TOKENS"] = "16384"
    os.environ.pop("DATAFORGE_LLM_SERVINGS_PATH", None)
    sample_code = "reviewed-medical-v2"
    sample_count = len(SampleDataService().reviewed_chunks(sample_code)["chunks"])
    report = {"model": args.model, "sample": sample_code, "sample_count": sample_count, "runs": [],
              "scope": "local SQLite debug runs; real model; no remote deployment"}
    try:
        with tempfile.TemporaryDirectory(prefix="dataforge-qa-acceptance-") as directory:
            store = V7Store(f"sqlite:///{Path(directory) / 'acceptance.sqlite3'}")
            try:
                upgrade(str(store.engine.url))
                store.seed()
                configure_llm_serving_registry(store.sessions, None)
                dsl = builtin_flow_definition(["qa"])
                node = next(n for n in dsl["nodes"] if n.get("ref") == "qa-extractor")
                node["ref"] = "Text2QAGenerator"
                node["params"].pop("extraction_instructions", None)
                flow = store.create_flow_template("qa-dataflow-acceptance", "DataFlow acceptance", ["qa"], dsl, authoring_mode="advanced")
                scenarios = [("dataforge", "flow_standard-qa", "published")] * args.rounds + [("dataflow", flow["id"], "draft")]
                for index, (provider, template_id, revision_kind) in enumerate(scenarios):
                    options = store.debug_run_options(template_id, revision_kind)
                    run = store.create_debug_run(template_id=template_id, revision_id=options["revision"]["id"],
                        expected_compiled_checksum=options["compiled_checksum"], input_source="builtin_sample",
                        sample_code=sample_code, source_review_snapshot_ids=[], sink_library_bindings={},
                        idempotency_key=f"qa-acceptance-{index}")
                    store.claim_debug_run(f"qa-acceptance-{index}")
                    started = time.monotonic()
                    print(json.dumps({"event": "started", "provider": provider, "round": index + 1}), flush=True)
                    result = execute_debug_run(store, None, run["id"])
                    detail = store.flow_run_detail(run["id"])
                    qa_node = next(n for n in detail["nodes"] if n["operator_code"] in {"qa-extractor", "Text2QAGenerator"})
                    with store.sessions() as session:
                        preview = session.scalar(select(FlowRunSinkPreview).where(FlowRunSinkPreview.flow_run_id == run["id"]))
                        candidates = preview.candidates_json if preview else []
                        knowledge_count = session.scalar(select(func.count()).select_from(KnowledgeItem))
                        vector_jobs = session.scalar(select(func.count()).select_from(VectorSyncJob))
                    stat = qa_node["metrics"].get("chunk_processing", [{}])[0]
                    passed = (result["status"] == "completed" and stat.get("successful_chunks") == sample_count
                              and stat.get("failed_chunks") == 0 and len(candidates) == sample_count
                              and len({c["source_chunk_id"] for c in candidates}) == sample_count
                              and all(c.get("evidence_text") and all(c["data_json"].get(k, "").strip() for k in ("question", "answer")) for c in candidates)
                              and knowledge_count == vector_jobs == 0)
                    entry = {"provider": provider, "round": index + 1, "passed": passed,
                             "seconds": round(time.monotonic() - started, 2), "status": result["status"],
                             "candidate_count": len(candidates), "metrics": qa_node["metrics"],
                             "qa_samples": [{"chunk_id": c["source_chunk_id"], **c["data_json"]} for c in candidates],
                             "formal_knowledge_count": knowledge_count, "vector_job_count": vector_jobs,
                             "logs": qa_node.get("logs", [])}
                    report["runs"].append(entry)
                    print(json.dumps({k: v for k, v in entry.items() if k not in {"logs", "qa_samples"}}, ensure_ascii=False), flush=True)
                    if not passed:
                        raise RuntimeError("QA acceptance did not pass; see the bounded diagnostic report")
                report["passed"] = True
            finally:
                store.engine.dispose()
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
