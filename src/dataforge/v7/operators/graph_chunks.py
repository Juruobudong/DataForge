"""Versioned triple endpoint validation and atomic per-chunk execution."""
from collections import defaultdict

from ..graph_literal import detect_literal
from ..graph_prompt import GRAPH_GUIDANCE_VERSIONS, RELATION_REPAIR_VERSION
from .diagnostics import OperatorDiagnostics


TRIPLE_CHUNK_VERSIONS = {"relation-extractor": 6, "triple-builder": 4, "schema-validator": 4}


def uses_triple_chunks(code, version, params):
    versions = {TRIPLE_CHUNK_VERSIONS.get(code)}
    if code == "relation-extractor":
        versions.add(GRAPH_GUIDANCE_VERSIONS[code])  # Retain the v6 chunk/endpoint contract.
        versions.add(RELATION_REPAIR_VERSION)
    return params.get("graph_mode") == "triple" and version is not None and version in versions


class GraphChunkError(ValueError):
    """An attributable data-contract error, not an infrastructure exception."""


class GraphEndpointUnresolved(GraphChunkError):
    def __init__(self, role, name):
        self.role, self.name = role, name
        super().__init__(f"GRAPH_ENDPOINT_UNRESOLVED: {role} 端点不在已抽取实体中：{name[:200]!r}")


class TripleEndpoints:
    def __init__(self, entities, config, normalize):
        self.config, self.normalize = config, normalize
        self.names, self.aliases = defaultdict(dict), defaultdict(dict)
        for entity in entities:
            if not isinstance(entity, dict) or not isinstance(entity.get("name"), str):
                raise GraphChunkError("GRAPH_ENTITY_INVALID: 实体必须包含名称")
            name = normalize(entity["name"])
            if not name:
                raise GraphChunkError("GRAPH_ENTITY_INVALID: 实体名称不能为空")
            if entity.get("object_kind", "entity") not in ("entity", "literal"):
                raise GraphChunkError("GRAPH_ENTITY_INVALID: 实体 object_kind 不合法")
            identity = (name, str(entity.get("type") or ""), entity.get("object_kind", "entity"))
            self.names[name][identity] = entity
            aliases = entity.get("aliases") or []
            if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
                raise GraphChunkError("GRAPH_ENTITY_INVALID: 实体 aliases 必须是字符串数组")
            for alias in aliases:
                if isinstance(alias, str) and normalize(alias):
                    self.aliases[normalize(alias)][identity] = entity

    def resolve(self, name, *, role):
        if not isinstance(name, str) or not self.normalize(name):
            raise GraphChunkError(f"GRAPH_ENDPOINT_INVALID: {role} 端点名称不能为空")
        key = self.normalize(name)
        matches = self.names.get(key) or self.aliases.get(key) or {}
        if len(matches) > 1:
            raise GraphChunkError(f"GRAPH_ENDPOINT_AMBIGUOUS: {role} 端点匹配多个实体：{name[:200]!r}")
        entity = next(iter(matches.values()), None)
        literal = detect_literal(entity["name"] if entity else name)
        is_literal = literal is not None or (entity and entity.get("object_kind") == "literal")
        if role == "subject" and is_literal:
            raise GraphChunkError(f"GRAPH_SUBJECT_LITERAL: 主体不能是字面值：{name[:200]!r}")
        if role == "object" and is_literal:
            return entity or {"name": name.strip(), "object_kind": "literal"}
        if entity is None:
            raise GraphEndpointUnresolved(role, name)
        code = entity.get("type")
        if not isinstance(code, str) or not code.strip() or (self.config.entity_types and code not in self.config.entity_codes()):
            raise GraphChunkError(f"GRAPH_ENTITY_TYPE_INVALID: {role} 实体 {name[:200]!r} 缺少合法类型")
        return entity

    def relation(self, relation):
        if not isinstance(relation, dict):
            raise GraphChunkError("GRAPH_RELATION_INVALID: 关系必须是对象")
        if not isinstance(relation.get("type"), str) or not relation["type"].strip():
            raise GraphChunkError("GRAPH_RELATION_INVALID: 关系缺少类型")
        subject = self.resolve(relation.get("source"), role="subject")
        target = self.resolve(relation.get("target"), role="object")
        definition = self.config.relation_by_code(str(relation.get("type") or ""))
        if definition and (definition.source_types and subject.get("type") not in definition.source_types
                           or definition.target_types and target.get("type") not in definition.target_types):
            raise GraphChunkError("GRAPH_RELATION_CONSTRAINT_INVALID: 关系端点类型不符合方向约束")
        return {**relation, "source": subject["name"], "target": target["name"]}


def chunk_identity(value):
    version = value.get("source_version_id")
    versions = value.get("source_version_ids")
    if versions is not None:
        if not isinstance(versions, list) or len(versions) != 1 or not versions[0] or (version and version != versions[0]):
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 三元组分块必须对应唯一来源版本")
        version = versions[0]
    chunk = value.get("flow_chunk_id")
    if not isinstance(version, str) or not version.strip() or not isinstance(chunk, str) or not chunk.strip():
        raise ValueError("SOURCE_LINEAGE_MISSING: 三元组分块缺少来源版本或分块身份")
    return version, chunk


class GraphChunkStage:
    def __init__(self, node_id, params, generation, *, isolate_llm_timeout=False):
        self.node_id, self.generation = node_id, generation
        self.output_key = "graph:triple"
        self.isolate_llm_timeout = isolate_llm_timeout
        self.diagnostics = OperatorDiagnostics()
        self.diagnostics.add_secrets(params)
        self.attempted, self.successful, self.failed = set(), set(), set()
        self.relation_repair_enabled = False
        self.repair_attempted = set()
        self.relation_counts = {}

    def claim_relation_repair(self, chunk, error):
        """Share one additional model call across every record of this chunk."""
        key = chunk_identity(chunk)
        if not self.relation_repair_enabled or key in self.repair_attempted:
            return False
        self.repair_attempted.add(key)
        self.diagnostics.append("stdout", self.diagnostics.error(
            f"GRAPH_RELATION_REPAIR_ATTEMPT: {error} [node={self.node_id}, source_version_id={key[0]}, flow_chunk_id={key[1]}, attempt=1]") + "\n")
        return True

    def record_relation_result(self, key, records, outputs):
        count = sum(len(value.get("relations") or []) for value in outputs)
        self.relation_counts[key] = count
        entity_count = sum(len([entity for entity in value.get("entities", []) if entity.get("object_kind") != "literal"]) for value in records)
        zero_reason = "no_entities" if not entity_count else "no_legal_relations"
        self.diagnostics.append("stdout", self.diagnostics.error(
            f"GRAPH_RELATION_RESULT: source_version_id={key[0]} flow_chunk_id={key[1]} entities={entity_count} relations={count} "
            f"repair_attempts={int(key in self.repair_attempted)} zero_reason={zero_reason if not count else 'none'}") + "\n")

    @property
    def metrics(self):
        result = {"chunk_processing": [{"output_key": self.output_key, "attempted_chunks": len(self.attempted),
                                        "successful_chunks": len(self.successful), "failed_chunks": len(self.failed)}]}
        if self.relation_repair_enabled:
            result["relation_repair"] = {"attempted_chunks": len(self.repair_attempted),
                                         "successful_chunks": len(self.repair_attempted & self.successful),
                                         "failed_chunks": len(self.repair_attempted - self.successful),
                                         "relation_count": sum(self.relation_counts.values())}
        return result

    def run(self, values, process, *, store=None, job_id=None):
        groups = defaultdict(list)
        for value in values:
            groups[chunk_identity(value)].append(value)
        outcome = self.generation.setdefault(self.output_key, {"targeted": [], "successful": [], "failed": []})
        outcome["chunk_isolation"] = True
        targeted = {chunk_identity(item): item for item in outcome["targeted"]}
        successful = {chunk_identity(item): item for item in outcome["successful"]}
        failed = {chunk_identity(item): item for item in outcome["failed"]}
        for key in failed:
            successful.pop(key, None)
        outcome["successful"] = list(successful.values())
        result = []
        for key, records in groups.items():
            # A merge must never resurrect a chunk rejected by an earlier branch.
            if key in failed:
                successful.pop(key, None)
                continue
            self.attempted.add(key)
            first = records[0]
            chunk = targeted.get(key) or {
                **first,
                "source_version_id": key[0], "flow_chunk_id": key[1],
                "chunk_index": int(first.get("chunk_index", (first.get("anchor_json") or {}).get("chunk_index", 0))),
            }
            targeted[key] = chunk
            try:
                outputs = process(records)
            except (GraphChunkError, TimeoutError) as exc:
                # Explicitly opted-in Triple LLM extraction treats a timeout as an attributable
                # chunk failure: successful neighbouring chunks must still be
                # eligible for the formal Sink.  Builders, validators, Semantic graphs and every
                # other infrastructure exception retain node-failure semantics.
                if isinstance(exc, TimeoutError) and not self.isolate_llm_timeout:
                    raise
                detail = f"GRAPH_CHUNK_TIMEOUT: {exc}" if isinstance(exc, TimeoutError) else str(exc)
                message = self.diagnostics.error(f"{detail} [node={self.node_id}, source_version_id={key[0]}, flow_chunk_id={key[1]}]")
                successful.pop(key, None)
                failed[key] = {**chunk, "error": message}
                self.failed.add(key)
                self.diagnostics.append("stderr", message + "\n")
                if store and job_id:
                    store.record_chunk_generation(job_id, self.output_key, chunk, status="failed", error=message)
            else:
                successful[key] = chunk
                self.successful.add(key)
                if self.relation_repair_enabled:
                    self.record_relation_result(key, records, outputs)
                result.extend(outputs)
                if store and job_id:
                    store.record_chunk_generation(job_id, self.output_key, chunk, status="success" if outputs else "success_empty", candidate_count=len(outputs))
            finally:
                outcome.update(targeted=list(targeted.values()), successful=list(successful.values()), failed=list(failed.values()))
        if self.attempted and self.failed == self.attempted:
            raise GraphChunkError("GRAPH_CHUNKS_FAILED: 本节点所有尝试的分块均失败，失败分块旧知识保持不变")
        return result
