#!/usr/bin/env python3
"""Opt-in MySQL stress check for deterministic Artifact persistence.

Run only inside the disposable DataForge test Compose environment. The check
creates test jobs and execution records but never deletes or rewrites resources.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import uuid

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from dataforge.config import Settings
from dataforge.v7.models import (
    Artifact, ArtifactLineage, FlowNodeArtifactBinding, FlowNodeRun, FlowRunEvent,
    KnowledgeJob,
)
from dataforge.v7.store import V7Store


def _prepare_run(store: V7Store, prefix: str, round_no: int, worker_no: int) -> tuple[str, str]:
    suffix = f"{prefix}-{round_no}-{worker_no}"
    documents = store.create_document_library(f"Artifact deadlock {suffix}")
    source = store.create_source(
        library_id=documents["id"], name=suffix, filename=f"{suffix}.txt",
        object_key=f"deadlock-test/{suffix}.txt", sha256=uuid.uuid4().hex * 2,
        size_bytes=8, mime_type="text/plain",
    )
    knowledge = store.create_knowledge_library(f"Artifact deadlock {suffix}", "text")
    job = store.create_knowledge_job(
        [source["current_version_id"]], {"text": knowledge["id"]}, "flow_standard-text",
    )
    # The stress check drives Store transactions directly. Keep the persistent
    # Worker from claiming these synthetic jobs and trying to read fake objects.
    with store.sessions.begin() as session:
        persisted_job = session.get(KnowledgeJob, job["id"])
        persisted_job.status, persisted_job.stage = "completed", "completed"
    flow_run_id = store.start_flow_run(job["id"])["id"]
    parent_id = store.record_flow_node(flow_run_id, "stress-parent", [], [{"suffix": suffix}])[0]
    return flow_run_id, parent_id


def _validate_node(store: V7Store, flow_run_id: str, parent_id: str, output_ids: list[str], expected: int) -> None:
    with store.sessions() as session:
        node_run = session.scalar(select(FlowNodeRun).where(
            FlowNodeRun.flow_run_id == flow_run_id,
            FlowNodeRun.node_id == "stress-batch",
        ))
        if not node_run or node_run.output_artifact_ids != output_ids:
            raise RuntimeError("NodeRun 输出顺序不一致")
        artifact_count = session.scalar(select(func.count()).select_from(Artifact).where(
            Artifact.flow_node_run_id == node_run.id,
        ))
        output_binding_count = session.scalar(select(func.count()).select_from(FlowNodeArtifactBinding).where(
            FlowNodeArtifactBinding.flow_node_run_id == node_run.id,
            FlowNodeArtifactBinding.direction == "output",
        ))
        lineage_count = session.scalar(select(func.count()).select_from(ArtifactLineage).where(
            ArtifactLineage.parent_artifact_id == parent_id,
        ))
        event_count = session.scalar(select(func.count()).select_from(FlowRunEvent).where(
            FlowRunEvent.flow_run_id == flow_run_id,
            FlowRunEvent.node_id == "stress-batch",
        ))
    observed = (artifact_count, output_binding_count, lineage_count, event_count)
    if observed != (expected, expected, expected, 1):
        raise RuntimeError(f"Artifact 持久化数量不一致：expected={expected}, observed={observed}")


def run(rounds: int, workers: int, outputs: int) -> dict[str, int]:
    settings = Settings.load()
    database = make_url(settings.platform_database_url)
    if database.get_backend_name() != "mysql" or database.host != "mysql" or database.database != "dataforge":
        raise RuntimeError("该压力检查只允许在测试 Compose 的 mysql/dataforge 数据库中运行")
    store = V7Store(settings.platform_database_url)
    store.assert_schema_current()
    prefix = uuid.uuid4().hex[:10]
    completed = 0
    for round_no in range(rounds):
        prepared = [_prepare_run(store, prefix, round_no, worker_no) for worker_no in range(workers)]
        barrier = Barrier(workers)

        def persist(worker_no: int) -> tuple[str, str, list[str]]:
            flow_run_id, parent_id = prepared[worker_no]
            values = [
                {"round": round_no, "worker": worker_no, "sequence": sequence, "payload": f"value-{sequence}"}
                for sequence in range(outputs)
            ]
            barrier.wait()
            output_ids = store.record_flow_node(flow_run_id, "stress-batch", [parent_id], values)
            return flow_run_id, parent_id, output_ids

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="artifact-deadlock") as pool:
            results = [future.result() for future in [pool.submit(persist, worker_no) for worker_no in range(workers)]]
        for flow_run_id, parent_id, output_ids in results:
            _validate_node(store, flow_run_id, parent_id, output_ids, outputs)
            completed += 1
    return {"rounds": rounds, "workers": workers, "outputs_per_node": outputs, "completed_nodes": completed}


def main() -> None:
    parser = argparse.ArgumentParser(description="DataForge Artifact MySQL 并发死锁压力检查")
    parser.add_argument("--execute", action="store_true", help="确认向空卷测试数据库写入压力测试记录")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--outputs", type=int, default=4000)
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("拒绝执行：必须显式传入 --execute")
    if args.rounds < 1 or args.workers < 2 or args.outputs < 1000:
        raise SystemExit("rounds 必须 >=1、workers 必须 >=2、outputs 必须 >=1000")
    print(json.dumps(run(args.rounds, args.workers, args.outputs), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
