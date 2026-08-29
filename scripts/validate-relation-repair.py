"""Real-model acceptance for Relation Extractor v8 in a disposable SQLite store.

Uses the repository's reviewed-medical-v2 sample and configured qwen3_32b
Serving. It creates Debug previews only: no formal knowledge or vector jobs.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import unicodedata
from pathlib import Path

from sqlalchemy import func, select

from dataforge.v7.llm_serving import configure_llm_serving_registry
from dataforge.v7.migrations import upgrade
from dataforge.v7.models import Artifact, FlowNodeRun, KnowledgeItem, VectorSyncJob
from dataforge.v7.runner import execute_debug_run
from dataforge.v7.sample_data import SampleDataService
from dataforge.v7.store import V7Store


RESULT_RE = re.compile(
    r"GRAPH_RELATION_RESULT: source_version_id=(\S+) source_chunk_id=(\S+) "
    r"entities=(\d+) relations=(\d+) repair_attempts=(\d+) zero_reason=(\S+)"
)


def normalized(value):
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def validate_endpoints(data):
    entities = data.get("entities") or []
    names = {normalized(item.get("name")) for item in entities}
    aliases = {normalized(alias) for item in entities for alias in (item.get("aliases") or [])}
    invalid = []
    for relation in data.get("relations") or []:
        for role in ("source", "target"):
            endpoint = normalized(relation.get(role))
            if endpoint not in names and endpoint not in aliases:
                invalid.append({"role": role, "endpoint": relation.get(role)})
    return invalid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--serving", default="qwen3_32b")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds != 3:
        parser.error("relation repair acceptance requires exactly three consecutive rounds")

    sample_code = "reviewed-medical-v2"
    sample_count = len(SampleDataService().reviewed_chunks(sample_code)["chunks"])
    report = {"scope": "local temporary SQLite; real model; Debug preview only",
              "sample": sample_code, "sample_count": sample_count,
              "serving": args.serving, "required_rounds": 3, "runs": []}
    with tempfile.TemporaryDirectory(prefix="dataforge-relation-v8-") as directory:
        store = V7Store(f"sqlite:///{Path(directory) / 'acceptance.sqlite3'}")
        try:
            upgrade(str(store.engine.url))
            store.seed()
            configure_llm_serving_registry(store.sessions, None)
            serving = store.llm_serving_registry.require(args.serving)
            report["model"] = serving.model_name
            template_id = "flow_standard-graph-triple"
            with store.sessions() as session:
                before_knowledge = session.scalar(select(func.count()).select_from(KnowledgeItem))
                before_vectors = session.scalar(select(func.count()).select_from(VectorSyncJob))
            for index in range(3):
                options = store.debug_run_options(template_id, "published")
                created = store.create_debug_run(
                    template_id=template_id, revision_id=options["revision"]["id"],
                    expected_compiled_checksum=options["compiled_checksum"], input_source="builtin_sample",
                    sample_code=sample_code, source_review_snapshot_ids=[], sink_library_bindings={},
                    idempotency_key=f"relation-v8-real-{index}",
                )
                store.claim_debug_run(f"relation-v8-real-{index}")
                started = time.monotonic()
                result = execute_debug_run(store, None, created["id"])
                detail = store.flow_run_detail(created["id"])
                node = next(value for value in detail["nodes"] if value["node_id"] == "relations-graph:triple")
                stat = node["metrics"].get("chunk_processing", [{}])[0]
                repair = node["metrics"].get("relation_repair", {})
                log_text = "\n".join(value.get("message", "") for value in node.get("logs", []))
                logged = {match[2]: {"entity_count": int(match[3]), "relation_count": int(match[4]),
                                     "repair_attempts": int(match[5]), "zero_reason": match[6]}
                          for match in RESULT_RE.finditer(log_text)}
                with store.sessions() as session:
                    node_run = session.scalar(select(FlowNodeRun).where(
                        FlowNodeRun.flow_run_id == created["id"], FlowNodeRun.node_id == "relations-graph:triple"))
                    artifacts = list(session.scalars(select(Artifact).where(Artifact.flow_node_run_id == node_run.id)))
                    after_knowledge = session.scalar(select(func.count()).select_from(KnowledgeItem))
                    after_vectors = session.scalar(select(func.count()).select_from(VectorSyncJob))
                by_chunk = {}
                invalid = []
                for artifact in artifacts:
                    data = artifact.data_json or {}
                    chunk = str(data.get("source_chunk_id") or "")
                    invalid.extend({"chunk_id": chunk, **item} for item in validate_endpoints(data))
                    item = logged.setdefault(chunk, {"entity_count": len(data.get("entities") or []),
                                                     "relation_count": len(data.get("relations") or []),
                                                     "repair_attempts": 0,
                                                     "zero_reason": "none" if data.get("relations") else "no_legal_relations"})
                    item["relations"] = [{key: relation.get(key) for key in ("source", "type", "target")}
                                         for relation in (data.get("relations") or [])]
                    item["evidence"] = str(data.get("content") or "")
                    by_chunk[chunk] = item
                previews = detail.get("sink_previews") or []
                candidates = store.sink_preview_candidates(created["id"], previews[0]["id"])["items"] if previews else []
                passed = (result["status"] == "completed" and node["operator_version"] == 8
                          and stat.get("attempted_chunks") == stat.get("successful_chunks") == sample_count
                          and stat.get("failed_chunks") == 0 and len(by_chunk) == sample_count
                          and sum(value["relation_count"] for value in by_chunk.values()) > 0
                          and candidates and not invalid
                          and all(value["repair_attempts"] <= 1 for value in by_chunk.values())
                          and all(value["evidence"] for value in by_chunk.values())
                          and after_knowledge == before_knowledge and after_vectors == before_vectors)
                entry = {"round": index + 1, "passed": bool(passed), "run_id": created["id"],
                         "seconds": round(time.monotonic() - started, 2), "status": result["status"],
                         "operator_version": node["operator_version"], "chunk_processing": stat,
                         "relation_repair": repair, "candidate_count": len(candidates),
                         "invalid_endpoints": invalid, "chunks": by_chunk,
                         "formal_knowledge_delta": after_knowledge - before_knowledge,
                         "vector_job_delta": after_vectors - before_vectors}
                report["runs"].append(entry)
                print(json.dumps({key: value for key, value in entry.items() if key != "chunks"}, ensure_ascii=False), flush=True)
            report["passed"] = len(report["runs"]) == 3 and all(item["passed"] for item in report["runs"])
        finally:
            store.engine.dispose()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit("relation repair real-model acceptance failed; inspect report")


if __name__ == "__main__":
    main()
