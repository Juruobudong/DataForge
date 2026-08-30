"""Read-only, version-bound retrieval diagnostics. No conversational generation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from .models import KnowledgeAssetVersion, KnowledgeAssetItem, StorageContractRevision
from .reranker import RerankerError
from .servings import EmbeddingServingRegistry
from .vector import V7Milvus


class RetrievalError(ValueError):
    pass


class RetrievalFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: Literal["eq", "in", "gt", "gte", "lt", "lte"]
    value: Any


class RetrievalOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: int = Field(default=10, ge=1, le=200, strict=True)
    final_top_k: int = Field(default=5, ge=1, le=200, strict=True)
    reranker_serving_code: str | None = None


class RetrievalDebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_stage: Literal["test", "production"]
    route_mode: Literal["draft", "published", "historical"] = "published"
    version_no: int | None = Field(default=None, ge=1)
    org_code: str = Field(min_length=1, max_length=120)
    task_code: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=8192)
    filters: list[RetrievalFilter] = Field(default_factory=list, max_length=32)
    overrides: RetrievalOverrides | None = None


class PublicRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    org_code: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=8192)

    @field_validator("org_code", "query")
    @classmethod
    def non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class PublicRetrievalError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


NUMERIC_TYPES = {"INT8", "INT16", "INT32", "INT64", "FLOAT", "DOUBLE"}
SCALAR_TYPES = NUMERIC_TYPES | {"VARCHAR", "BOOL"}
STAGES = ("routing", "embedding", "recall", "reranker", "final", "context", "evidence")


def filter_fields(schema):
    return {field["name"]: str(field.get("type", "")).upper()
            for field in schema.get("fields", [])
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field.get("name", ""))
            and str(field.get("type", "")).upper() in SCALAR_TYPES}


def compile_filters(filters: list[RetrievalFilter], allowed: dict[str, str]) -> str:
    expressions = []
    for condition in filters:
        dtype = allowed.get(condition.field)
        if not dtype:
            raise RetrievalError("过滤字段不属于冻结 Storage Contract 的标量字段")
        values = condition.value if condition.op == "in" else [condition.value]
        if not isinstance(values, list) or not values or len(values) > 200:
            raise RetrievalError("集合过滤需要 1～200 个值")
        for value in values:
            valid = (type(value) is str if dtype == "VARCHAR" else type(value) is bool if dtype == "BOOL"
                     else type(value) in (int, float) and math.isfinite(value))
            if not valid or dtype.startswith("INT") and type(value) is not int:
                raise RetrievalError("过滤值类型与字段不匹配")
            if isinstance(value, str) and len(value) > 8192:
                raise RetrievalError("过滤值过长")
        if condition.op not in {"eq", "in"} and dtype not in NUMERIC_TYPES:
            raise RetrievalError("范围过滤只支持数值字段")
        operator = {"eq": "==", "in": "in", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[condition.op]
        literal = json.dumps(values if condition.op == "in" else values[0], ensure_ascii=True, allow_nan=False)
        expressions.append(f"({condition.field} {operator} {literal})")
    return " and ".join(expressions)


class RetrievalDebugService:
    def __init__(self, store, manager, *, embedding_registry=None, milvus_factory=V7Milvus,
                 milvus_resolver):
        self.store, self.manager = store, manager
        self.embedding_registry = embedding_registry or EmbeddingServingRegistry(manager)
        self.milvus_factory = milvus_factory
        self.milvus_resolver = milvus_resolver

    def snapshot(self, deployment_id, release_stage, route_mode, version_no=None):
        if route_mode == "historical":
            if version_no is None:
                raise RetrievalError("历史模式必须选择 version_no")
            version = self.store.route_version_detail(deployment_id, version_no, release_stage)
            if version["status"] not in {"frozen", "published"}:
                raise RetrievalError("只允许选择冻结或发布的历史版本")
            snapshot, number = version["snapshot"], version["version_no"]
            checksum = version["checksum"]
        elif route_mode == "published":
            if version_no is not None:
                raise RetrievalError("当前发布模式不能指定历史版本号")
            version = self.store.published_route_version(deployment_id, release_stage=release_stage)
            snapshot, number, checksum = version.snapshot_json, version.version_no, version.checksum
        else:
            if version_no is not None:
                raise RetrievalError("Draft 模式没有版本号")
            snapshot, number, checksum = self.store.routing_snapshot(deployment_id, release_stage), None, None
        snapshot = deepcopy(snapshot)
        if snapshot.get("project_deployment", {}).get("id") != deployment_id or snapshot.get("release_stage") != release_stage:
            raise RetrievalError("RoutingSnapshot 归属或环境不匹配")
        return snapshot, {"route_mode": route_mode, "version_no": number, "checksum": checksum or hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}

    def options(self, deployment_id, release_stage, route_mode, version_no=None):
        snapshot, identity = self.snapshot(deployment_id, release_stage, route_mode, version_no)
        tasks = []
        with self.store.sessions() as session:
            for task in snapshot.get("tasks", []):
                profile = task.get("index_profile") or {}
                contract_id = profile.get("storage_contract_revision_id")
                contract = session.get(StorageContractRevision, contract_id) if contract_id else None
                tasks.append({"task_code": task["task_code"], "task_name": task.get("task_name"),
                              "org_routes": task.get("org_routes", []), "top_k": task["top_k"],
                              "final_top_k": task.get("final_top_k", min(5, task["top_k"])),
                              "reranker_serving_code": task.get("reranker_serving_code"),
                              "filter_fields": filter_fields(contract.schema_json) if contract else {}})
        return {**identity, "tasks": tasks, "versions": self.store.list_route_versions(deployment_id, release_stage),
                "rerankers": self.manager.list("reranker")}

    def run(self, deployment_id: str, request: RetrievalDebugRequest, *, instance_mode: str):
        if not request.query.strip():
            raise RetrievalError("query 不能为空")
        snapshot, identity = self.snapshot(deployment_id, request.release_stage, request.route_mode, request.version_no)
        return self.run_resolved(snapshot, identity, request, instance_mode=instance_mode)

    def run_resolved(self, snapshot: dict[str, Any], identity: dict[str, Any],
                     request: RetrievalDebugRequest, *, instance_mode: str):
        """Execute one already-resolved immutable routing snapshot.

        Public retrieval resolves the Published snapshot by logical codes once and
        calls this method so a concurrent publish cannot mix route versions.
        """
        if not request.query.strip():
            raise RetrievalError("query 不能为空")
        task = next((item for item in snapshot.get("tasks", []) if item["task_code"] == request.task_code), None)
        org = next((item for item in (task or {}).get("org_routes", []) if item["org_code"] == request.org_code), None)
        if not task or not org:
            raise LookupError("所选 Routing 版本没有该任务和机构授权")
        baseline = {"top_k": task["top_k"], "final_top_k": task.get("final_top_k", min(5, task["top_k"])),
                    "reranker_serving_code": task.get("reranker_serving_code")}
        overrides = request.overrides.model_dump(exclude_unset=True) if request.overrides else {}
        effective = {**baseline, **overrides}
        if not 1 <= effective["final_top_k"] <= effective["top_k"] <= 200:
            raise RetrievalError("检索数量必须满足 1 ≤ final_top_k ≤ top_k ≤ 200")
        profile = task.get("index_profile") or {}
        with self.store.sessions() as session:
            contract = session.get(StorageContractRevision, profile.get("storage_contract_revision_id")) \
                if profile.get("storage_contract_revision_id") else None
            expression = compile_filters(request.filters, filter_fields(contract.schema_json) if contract else {})
        result = {"status": "completed", **identity, "release_stage": request.release_stage,
                  "experimental": bool(overrides), "baseline": baseline, "effective": effective,
                  "stages": [{"key": key, "status": "pending", "latency_ms": 0, "data": {}} for key in STAGES]}
        stages = {item["key"]: item for item in result["stages"]}
        started, active = time.monotonic(), "routing"
        step_start = started

        def complete(key, data, status="completed"):
            nonlocal step_start
            stages[key].update(status=status, data=data, latency_ms=round((time.monotonic() - step_start) * 1000))
            step_start = time.monotonic()

        try:
            libraries = org.get("libraries", [])
            if not libraries or not profile:
                raise RetrievalError("Routing 缺少授权知识库或索引配置")
            complete("routing", {"project": snapshot["project"], "deployment": snapshot["deployment"],
                                 "task_code": request.task_code, "org_code": request.org_code,
                                 "libraries": libraries, "milvus_target": snapshot["milvus_target"]})
            if instance_mode == "central" and snapshot["deployment"].get("scope") == "institution":
                result["status"] = "blocked"
                result["notice"] = "中心不连接机构现场 Milvus；请在机构本地执行完整检索调试。"
                return result
            active = "embedding"
            assets = self._assets(libraries, profile)
            identities = {(asset.embedding_serving_id, asset.embedding_model, asset.embedding_dimension) for asset in assets}
            if len(identities) != 1:
                raise RetrievalError("所选资产的 Embedding 合同不一致，不能合并分数")
            serving_code, model_name, dimension = next(iter(identities))
            if not serving_code or not model_name or not dimension:
                raise RetrievalError("AssetVersion 缺少冻结的 Embedding 身份")
            try:
                provider, config = self.embedding_registry.provider(serving_code)
            except ValueError:
                raise RetrievalError("Embedding Serving 未配置、未启用或凭据不可用；请检查模型服务") from None
            if config.model_name != model_name or config.dimension != dimension:
                raise RetrievalError("Embedding Serving 与 AssetVersion 冻结的模型或维度不匹配")
            vectors = provider.embed([request.query], model=model_name, dimension=dimension)
            if len(vectors) != 1 or len(vectors[0]) != dimension or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in vectors[0]
            ):
                raise RetrievalError("Query Embedding 响应维度或数值无效")
            complete("embedding", {"serving_code": serving_code, "model_name": model_name,
                                   "expected_dimension": dimension, "observed_dimension": len(vectors[0])})
            active = "recall"
            connection = self.milvus_resolver.snapshot(snapshot)
            milvus = self.milvus_factory(connection.uri, connection.token)
            metric = str((profile.get("storage") or profile.get("embedding") or {}).get("metric_type", "")).upper()
            if metric not in {"COSINE", "IP", "L2"}:
                raise RetrievalError("不支持所选向量度量")
            candidates = self._recall(milvus, assets, profile, vectors[0], effective["top_k"], expression, metric)
            complete("recall", {"candidates": candidates, "metric_type": metric,
                                "score_direction": "ascending" if metric == "L2" else "descending",
                                "filters": [item.model_dump() for item in request.filters]})
            active = "reranker"
            if not candidates or not effective["reranker_serving_code"]:
                ranked = deepcopy(candidates)
                complete("reranker", {"reason": "没有召回候选" if not candidates else "未启用重排"}, "skipped")
            else:
                expected = task.get("reranker") if effective["reranker_serving_code"] == baseline["reranker_serving_code"] else None
                if not expected and "reranker_serving_code" not in overrides:
                    raise RetrievalError("Routing 版本缺少冻结的 Reranker 身份")
                reranked = self.manager.reranker_registry.rerank(effective["reranker_serving_code"], request.query,
                    [item["content"] for item in candidates], expected_identity=expected)
                ranked = [{**candidates[item["index"]], "rerank_score": item["relevance_score"], "rerank_rank": rank}
                          for rank, item in enumerate(reranked["results"], 1)]
                complete("reranker", {**reranked, "candidates": ranked})
            active = "final"
            final = [{**item, "citation_id": f"C{index}"} for index, item in enumerate(ranked[:effective["final_top_k"]], 1)]
            complete("final", {"top_k": effective["final_top_k"], "count": len(final), "results": final})
            active = "context"
            context = "\n\n".join(f'[{item["citation_id"]}] {item["content"]}' for item in final)
            complete("context", {"text": context[:32000], "truncated": len(context) > 32000, "total_characters": len(context)})
            active = "evidence"
            complete("evidence", {"citations": self._evidence(final)})
        except Exception as exc:
            message = str(exc) if isinstance(exc, (RetrievalError, RerankerError)) else f"阶段调用失败（{type(exc).__name__}）"
            stages[active].update(status="failed", error=message, latency_ms=round((time.monotonic() - step_start) * 1000))
            result["status"] = "failed"
        finally:
            for stage in result["stages"]:
                if stage["status"] == "pending":
                    stage.update(status="skipped", data={"reason": "上游阶段未完成"})
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
        return result

    def _assets(self, libraries, profile):
        assets = []
        with self.store.sessions() as session:
            for library in libraries:
                asset = session.get(KnowledgeAssetVersion, library.get("asset_version_id")) if library.get("asset_version_id") else None
                if not asset or asset.status != "ready" or asset.review_gate_status != "approved" or not asset.review_snapshot_digest:
                    raise RetrievalError("Routing 引用的 AssetVersion 不可用或未通过审核")
                if (asset.knowledge_library_id != library["knowledge_library_id"] or asset.partition_name != library["partition_name"]
                    or asset.collection_name != profile["collection_name"] or asset.index_profile_revision_id != profile["index_profile_revision_id"]):
                    raise RetrievalError("AssetVersion 与冻结 Routing 的物理位置或 Profile 不匹配")
                session.expunge(asset)
                assets.append(asset)
        return assets

    def _recall(self, milvus, assets, profile, vector, limit, expression, metric):
        fields = profile["fields"]
        candidates = {}
        for priority, asset in enumerate(assets):
            milvus.validate_collection(asset.collection_name, fields, asset.embedding_dimension)
            if not milvus.partition_exists(asset.collection_name, asset.partition_name):
                raise RetrievalError("所选版本的 Partition 不存在；不会回退最新资产")
            milvus._assert_v7_partition(asset.partition_name)
            response = milvus.client().search(collection_name=asset.collection_name,
                partition_names=[asset.partition_name], data=[vector], anns_field=fields["vector"],
                search_params={"metric_type": metric}, filter=expression, limit=limit,
                output_fields=[fields[key] for key in ("knowledge_library_id", "source_knowledge_id", "content", "data")])
            hits = response[0] if response and isinstance(response[0], list) else response
            source_ids = [str((hit.get("entity") or {}).get(fields["source_knowledge_id"], "")) for hit in hits]
            with self.store.sessions() as session:
                frozen = {row.source_knowledge_id: row for row in session.scalars(select(KnowledgeAssetItem).where(
                    KnowledgeAssetItem.asset_version_id == asset.id, KnowledgeAssetItem.source_knowledge_id.in_(source_ids)))}
                for hit in hits:
                    entity = hit.get("entity") or {}
                    source_id = str(entity.get(fields["source_knowledge_id"], ""))
                    row = frozen.get(source_id)
                    if entity.get(fields["knowledge_library_id"]) != asset.knowledge_library_id or not row:
                        raise RetrievalError("召回结果超出授权或缺少对应资产条目快照")
                    if entity.get(fields["content"]) != row.canonical_content or entity.get(fields["data"]) != row.data_json:
                        raise RetrievalError("向量正文与 AssetVersion 条目快照不一致")
                    score = hit.get("distance", hit.get("score"))
                    if type(score) not in (int, float) or not math.isfinite(score):
                        raise RetrievalError("向量召回返回非法分数")
                    key = (asset.knowledge_library_id, source_id)
                    candidate = {"asset_version_id": asset.id, "asset_version_no": asset.version_no,
                                 "knowledge_library_id": asset.knowledge_library_id, "source_knowledge_id": source_id,
                                 "collection_name": asset.collection_name, "partition_name": asset.partition_name,
                                 "content": row.canonical_content, "data": deepcopy(row.data_json),
                                 "vector_score": score, "library_priority": priority}
                    old = candidates.get(key)
                    if old is None or (score < old["vector_score"] if metric == "L2" else score > old["vector_score"]):
                        candidates[key] = candidate
        ordered = sorted(candidates.values(), key=lambda item: (
            item["vector_score"] if metric == "L2" else -item["vector_score"],
            item["library_priority"], item["source_knowledge_id"]))[:limit]
        return [{**item, "vector_rank": index} for index, item in enumerate(ordered, 1)]

    def _evidence(self, results):
        citations = []
        with self.store.sessions() as session:
            for result in results:
                row = session.scalar(select(KnowledgeAssetItem).where(
                    KnowledgeAssetItem.asset_version_id == result["asset_version_id"],
                    KnowledgeAssetItem.source_knowledge_id == result["source_knowledge_id"]))
                if not row or not row.evidence_json:
                    raise RetrievalError("对应资产条目缺少冻结的 Evidence")
                citations.append({"citation_id": result["citation_id"], "asset_version_id": result["asset_version_id"],
                                  "source_knowledge_id": result["source_knowledge_id"], "sources": deepcopy(row.evidence_json)})
        return citations


class PublicRetrievalService:
    """Published-only business retrieval boundary.

    Internal routing and diagnostic payloads may contain physical storage data.
    This service never passes them through; every public field is constructed
    explicitly below.
    """

    CONTRACT_VERSION = 1

    def __init__(self, store, debug_service: RetrievalDebugService):
        self.store, self.debug_service = store, debug_service

    @staticmethod
    def _not_found() -> PublicRetrievalError:
        return PublicRetrievalError("route_not_found", "公共检索路由不存在", 404)

    def _published(self, project_code: str, deployment_code: str, release_stage: str,
                   task_code: str, org_code: str, *, allowed_deployment_id: str | None = None):
        try:
            snapshot = deepcopy(self.store.runtime_routing_snapshot(
                project_code, deployment_code, release_stage,
            ))
        except ValueError as exc:
            raise self._not_found() from exc
        if (snapshot.get("project", {}).get("code") != project_code
                or snapshot.get("deployment", {}).get("code") != deployment_code
                or snapshot.get("release_stage") != release_stage):
            raise self._not_found()
        if (allowed_deployment_id is not None
                and snapshot.get("project_deployment", {}).get("deployment_id") != allowed_deployment_id):
            raise self._not_found()
        task = next((item for item in snapshot.get("tasks", [])
                     if item.get("task_code") == task_code), None)
        org = next((item for item in (task or {}).get("org_routes", [])
                    if item.get("org_code") == org_code), None)
        if not task or not org or not org.get("libraries"):
            raise self._not_found()
        version = snapshot.get("version")
        checksum = str(snapshot.get("checksum") or "")
        if type(version) is not int or version < 1 or not checksum:
            raise PublicRetrievalError(
                "published_contract_invalid", "已发布检索契约不可用", 503,
            )
        identity = {"route_mode": "published", "version_no": version, "checksum": checksum}
        return snapshot, task, org, identity

    @staticmethod
    def _route(snapshot, task_code, org_code):
        return {
            "project_code": snapshot["project"]["code"],
            "deployment_code": snapshot["deployment"]["code"],
            "release_stage": snapshot["release_stage"],
            "task_code": task_code,
            "org_code": org_code,
            "route_version": snapshot["version"],
            "route_checksum": snapshot["checksum"],
        }

    @staticmethod
    def _policy(task):
        return {
            "top_k": task["top_k"],
            "final_top_k": task.get("final_top_k", min(5, task["top_k"])),
            "reranker_enabled": bool(task.get("reranker_serving_code")),
        }

    def contract(self, project_code: str, deployment_code: str, release_stage: str,
                 task_code: str, org_code: str, *, request_id: str,
                 allowed_deployment_id: str | None = None):
        snapshot, task, _org, _identity = self._published(
            project_code, deployment_code, release_stage, task_code, org_code,
            allowed_deployment_id=allowed_deployment_id,
        )
        return {
            "schema": "dataforge.retrieval-contract.v1",
            "contract_version": self.CONTRACT_VERSION,
            "request_id": request_id,
            "route": self._route(snapshot, task_code, org_code),
            "policy": self._policy(task),
            "capabilities": {
                "context": True, "evidence": True,
                "filters": False, "request_overrides": False,
            },
        }

    @staticmethod
    def _public_evidence(source):
        return {
            "source_id": source.get("source_id"),
            "source_name": source.get("source_name"),
            "original_filename": source.get("original_filename"),
            "relative_path": source.get("relative_path"),
            "source_version_id": source.get("source_version_id"),
            "source_version_no": source.get("source_version_no"),
            "source_chunk_id": source.get("source_chunk_id"),
            "source_chunk_revision_id": source.get("source_chunk_revision_id"),
            "source_review_snapshot_id": source.get("source_review_snapshot_id"),
            "source_anchor": source.get("source_anchor"),
            "anchor": deepcopy(source.get("anchor")),
            "evidence_text": source.get("evidence_text"),
            "is_primary": bool(source.get("is_primary")),
        }

    def query(self, project_code: str, deployment_code: str, release_stage: str,
              task_code: str, request: PublicRetrievalRequest, *, request_id: str,
              instance_mode: str, allowed_deployment_id: str | None = None):
        snapshot, task, _org, identity = self._published(
            project_code, deployment_code, release_stage, task_code, request.org_code,
            allowed_deployment_id=allowed_deployment_id,
        )
        debug_request = RetrievalDebugRequest(
            release_stage=release_stage, route_mode="published",
            task_code=task_code, org_code=request.org_code, query=request.query,
        )
        result = self.debug_service.run_resolved(
            snapshot, identity, debug_request, instance_mode=instance_mode,
        )
        if result["status"] == "blocked":
            raise PublicRetrievalError(
                "wrong_execution_site", "机构检索必须在对应机构本地 DataForge 执行", 409,
            )
        if result["status"] != "completed":
            raise PublicRetrievalError(
                "retrieval_unavailable", "公共检索依赖或冻结契约不可用", 503,
            )
        stages = {item["key"]: item for item in result["stages"]}
        final = stages["final"]["data"].get("results", [])
        citations = {item["citation_id"]: item for item in
                     stages["evidence"]["data"].get("citations", [])}
        direction = stages["recall"]["data"].get("score_direction", "descending")
        public_results = []
        for rank, item in enumerate(final, 1):
            reranked = item.get("rerank_score") is not None
            citation = citations.get(item["citation_id"], {})
            public_results.append({
                "rank": rank,
                "citation_id": item["citation_id"],
                "content": item["content"],
                "data": deepcopy(item.get("data") or {}),
                "score": {
                    "kind": "reranker" if reranked else "vector",
                    "value": item["rerank_score"] if reranked else item["vector_score"],
                    "direction": "descending" if reranked else direction,
                },
                "knowledge_library_id": item["knowledge_library_id"],
                "asset_version_no": item["asset_version_no"],
                "source_knowledge_id": item["source_knowledge_id"],
                "evidence": [self._public_evidence(source)
                             for source in citation.get("sources", [])],
            })
        context = stages["context"]["data"]
        return {
            "schema": "dataforge.retrieval-result.v1",
            "contract_version": self.CONTRACT_VERSION,
            "request_id": request_id,
            "route": self._route(snapshot, task_code, request.org_code),
            "policy": self._policy(task),
            "results": public_results,
            "context": {
                "text": context.get("text", ""),
                "truncated": bool(context.get("truncated")),
                "total_characters": int(context.get("total_characters") or 0),
            },
            "latency_ms": result["latency_ms"],
        }
