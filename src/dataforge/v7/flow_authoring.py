"""Standard-config authoring compiler layered on top of the typed FlowCompiler.

The two authoring modes (``standard`` / ``advanced``) differ only in *how a flow
is edited*, never in *how it executes*.  Both collapse to the same Flow DSL v3
which the existing :class:`~dataforge.v7.flow.FlowCompiler` compiles into a
topologically ordered Operator DAG.

``standard`` stores a stage config (not a DAG) in ``definition_json``; the
:class:`ManagedFlowCompiler` materializes that stage config into a real Flow DSL
using :func:`~dataforge.v7.catalog.builtin_flow_definition` as the single source
of truth, then injects user config and stage metadata onto the nodes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import re
from typing import Any

from .catalog import builtin_flow_definition, catalog_by_code, QA_EXTRACTION_SCHEMA
from .operator_catalog import technical_projection
from .flow import FlowCompiler, FlowValidationError
from .entity_types import custom_type_code, entity_type_catalog, normalize_entity_types


_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": False}
_LLM_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"llm_serving": {"type": "string", "title": "模型服务", "description": "已配置的 Model Serving ID", "x-dataforge-ui": {"widget": "llm-serving-selector"}}},
    "additionalProperties": False,
}
_QA_CONFIG_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    **_LLM_CONFIG_SCHEMA["properties"],
    "questions_per_chunk": {"type": "integer", "title": "每块最多问题数", "minimum": 1, "maximum": 10, "default": 1},
    "extraction_instructions": deepcopy(QA_EXTRACTION_SCHEMA),
}}
_GRAPH_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **_LLM_CONFIG_SCHEMA["properties"],
        "entity_types": {"type": "array", "title": "实体类型", "items": {"type": "object"},
                         "x-dataforge-ui": {"widget": "entity-type-editor"},
                         "description": "实体类型用于约束模型识别哪些对象；未配置的领域实体可通过添加实体类型补充。"},
        "relation_types": {"type": "array", "title": "关系类型", "items": {"type": "string"}, "description": "允许抽取的关系类型 code 列表"},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ManagedStageDefinition:
    code: str
    name: str
    locked: bool = True
    configurable: bool = False
    replaceable: bool = False
    input_contract: str = ""
    output_contract: str = ""
    config_schema: dict[str, Any] = field(default_factory=lambda: dict(_EMPTY_SCHEMA))
    operator_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedFlowDefinition:
    code: str
    name: str
    output_types: tuple[str, ...]
    stages: tuple[ManagedStageDefinition, ...]


_INPUT_STAGE = ManagedStageDefinition(code="input", name="已审核文档输入", locked=True,
                                      operator_refs=("reviewed-source-chunk-input",))
_TEXT_MAPPING_STAGE = ManagedStageDefinition(
    code="mapping", name="文本知识映射", input_contract="source_chunk_set",
    output_contract="candidate:text", operator_refs=("text-knowledge-mapper",),
)
_QUALITY_STAGE = ManagedStageDefinition(code="quality", name="图谱校验", locked=True,
                                        operator_refs=("schema-validator", "graph-quality-validator"))
_SUBMIT_STAGE = ManagedStageDefinition(code="submit", name="知识提交", locked=True)


def _generation(name: str, *, config_schema: dict[str, Any] = _LLM_CONFIG_SCHEMA, operator_refs: tuple[str, ...]) -> ManagedStageDefinition:
    return ManagedStageDefinition(code="generation", name=name, configurable=True, replaceable=False,
                                  config_schema=config_schema, operator_refs=operator_refs)


_STANDARD_FLOWS: tuple[ManagedFlowDefinition, ...] = (
    ManagedFlowDefinition(code="standard-text", name="文本知识", output_types=("text",), stages=(
        _INPUT_STAGE, _TEXT_MAPPING_STAGE,
        _SUBMIT_STAGE,
    )),
    ManagedFlowDefinition(code="standard-qa", name="问答知识", output_types=("qa",), stages=(
        _INPUT_STAGE, _generation("问答生成", config_schema=_QA_CONFIG_SCHEMA, operator_refs=("qa-extractor",)),
        _SUBMIT_STAGE,
    )),
    ManagedFlowDefinition(code="standard-graph-triple", name="三元组图谱", output_types=("graph:triple",), stages=(
        _INPUT_STAGE,
        _generation("实体关系抽取", config_schema=_GRAPH_CONFIG_SCHEMA,
                    operator_refs=("entity-extractor", "literal-detector", "relation-extractor", "triple-builder")),
        _QUALITY_STAGE, _SUBMIT_STAGE,
    )),
    ManagedFlowDefinition(code="standard-graph-semantic", name="语义图谱", output_types=("graph:semantic",), stages=(
        _INPUT_STAGE,
        _generation("语义图谱抽取", config_schema=_GRAPH_CONFIG_SCHEMA,
                    operator_refs=("entity-extractor", "literal-detector", "entity-normalizer",
                                   "relation-extractor", "semantic-relation-builder", "evidence-binder")),
        _QUALITY_STAGE, _SUBMIT_STAGE,
    )),
    ManagedFlowDefinition(code="standard-multi", name="多产出知识", output_types=("text", "qa", "graph:triple"), stages=(
        _INPUT_STAGE, _TEXT_MAPPING_STAGE,
        _generation("多产出生成", config_schema={**_GRAPH_CONFIG_SCHEMA, "properties": {**_GRAPH_CONFIG_SCHEMA["properties"], **_QA_CONFIG_SCHEMA["properties"]}},
                    operator_refs=("qa-extractor", "entity-extractor", "literal-detector",
                                   "relation-extractor", "triple-builder")),
        _QUALITY_STAGE, _SUBMIT_STAGE,
    )),
)


class ManagedTemplateError(FlowValidationError):
    def __init__(self, code: str, message: str, field: str):
        super().__init__(message)
        self.code, self.field = code, field

    def payload(self):
        return {"code": self.code, "message": str(self), "field": self.field}


def normalise_output_key(value: str) -> str:
    value = str(value or "").strip()
    return "graph:triple" if value == "graph" else value


def assert_normalized_output_types_match_managed_template(
    managed_template_code: str, output_types: list[str] | None, *, catalog=None,
) -> list[str]:
    """Validate caller assertions, but always return the catalog's canonical outputs."""
    expected = list((catalog or MANAGED_FLOW_CATALOG).get(managed_template_code).output_types)
    if output_types is not None and {normalise_output_key(value) for value in output_types} != set(expected):
        raise ManagedTemplateError("MANAGED_TEMPLATE_OUTPUT_MISMATCH",
                                   f"标准模板 {managed_template_code} 的输出必须为 {expected}", "output_types")
    return expected


class ManagedFlowCatalog:
    """Registry of the five built-in standard templates."""

    def __init__(self, definitions: tuple[ManagedFlowDefinition, ...] = _STANDARD_FLOWS):
        self._by_code = {definition.code: definition for definition in definitions}

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(self._by_code)

    def get(self, code: str) -> ManagedFlowDefinition:
        definition = self._by_code.get(code)
        if not definition:
            raise ManagedTemplateError("MANAGED_TEMPLATE_CODE_INVALID", f"未知的标准模板：{code}", "managed_template_code")
        return definition

    def list_definitions(self, operator_catalog=None) -> list[dict[str, Any]]:
        values = []
        for definition in self._by_code.values():
            resolved = technical_projection(ManagedFlowCompiler(self).materialize(
                self.default_stage_config(definition.code), list(definition.output_types)), operator_catalog if operator_catalog is not None else catalog_by_code())
            values.append({
                **resolved,
                "code": definition.code,
                "name": definition.name,
                "output_types": list(definition.output_types),
                "default_definition": self.default_stage_config(definition.code),
                "stages": [{
                    "code": stage.code, "name": stage.name,
                    "locked": stage.locked, "replaceable": stage.replaceable, "configurable": stage.configurable,
                    "config_schema": stage.config_schema if stage.configurable else None,
                    "operators": [node for node in resolved["resolved_operators"] if node["stage_id"] == stage.code],
                } for stage in definition.stages],
            })
        return values

    def default_stage_config(self, code: str) -> dict[str, Any]:
        definition = self.get(code)
        stages = {}
        if code in {"standard-graph-triple", "standard-graph-semantic", "standard-multi"}:
            stages["generation"] = {"config": {"entity_types": entity_type_catalog()["base"]}}
        return {"schema_version": 1, "template_code": definition.code, "stages": stages}

    def normalize_config(self, code: str, definition: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize a stage config for persistence (editor state)."""
        flow_definition = self.get(code)
        value = dict(definition or {})
        if value.get("template_code") is not None and value["template_code"] != code:
            raise ManagedTemplateError("MANAGED_TEMPLATE_CODE_MISMATCH", "标准模板标识不一致", "definition.template_code")
        stages = value.get("stages")
        if stages is None:
            stages = {}
        if not isinstance(stages, dict):
            raise ValueError("标准配置的 stages 必须是对象")
        stages = deepcopy(stages)
        if code == "standard-text":
            # This legacy stage never opted a Standard flow into generation.
            stages.pop("generation", None)
        allowed = {stage.code for stage in flow_definition.stages}
        for stage_code, stage_value in stages.items():
            if stage_code not in allowed:
                raise ValueError(f"未知的流程阶段：{stage_code}")
            if stage_value is None:
                continue
            if not isinstance(stage_value, dict):
                raise ValueError(f"阶段 {stage_code} 的配置必须是对象")
            config = stage_value.get("config")
            if config is not None and not isinstance(config, dict):
                raise ValueError(f"阶段 {stage_code} 的 config 必须是对象")
            stage = next(item for item in flow_definition.stages if item.code == stage_code)
            if config is not None and "entity_types" in config:
                config["entity_types"] = normalize_entity_types(config["entity_types"], allow_legacy_strings=True)
            self._validate_config(stage, config or {})
        return {"schema_version": 1, "template_code": flow_definition.code, "stages": stages}

    @staticmethod
    def _validate_config(stage: ManagedStageDefinition, config: dict[str, Any]) -> None:
        properties = (stage.config_schema.get("properties") or {}) if stage.configurable else {}
        for key, config_value in config.items():
            if key not in properties:
                raise ValueError(f"阶段 {stage.name} 不接受参数：{key}")
            spec = properties[key]
            if key == "entity_types":
                normalize_entity_types(config_value)
                continue
            expected = spec.get("type")
            if expected == "string" and not isinstance(config_value, str):
                raise ValueError(f"阶段 {stage.name} 参数 {key} 必须是字符串")
            if expected == "integer" and (not isinstance(config_value, int) or isinstance(config_value, bool) or not spec.get("minimum", 0) <= config_value <= spec.get("maximum", 10)):
                raise ValueError(f"阶段 {stage.name} 参数 {key} 超出整数范围")
            if expected == "array" and (not isinstance(config_value, list) or any(not isinstance(item, str) for item in config_value)):
                raise ValueError(f"阶段 {stage.name} 参数 {key} 必须是字符串数组")


class ManagedFlowCompiler:
    """Materialize a standard stage config into Flow DSL v3."""

    def __init__(self, catalog: ManagedFlowCatalog | None = None):
        self.catalog = catalog or ManagedFlowCatalog()

    def materialize(self, definition: dict[str, Any], output_types: list[str] | None = None) -> dict[str, Any]:
        template_code = (definition or {}).get("template_code")
        if not template_code:
            raise ManagedTemplateError("MANAGED_TEMPLATE_CODE_INVALID", "标准配置缺少 template_code", "definition.template_code")
        flow_definition = self.catalog.get(template_code)
        output_types = assert_normalized_output_types_match_managed_template(template_code, output_types, catalog=self.catalog)
        normalized = self.catalog.normalize_config(template_code, definition)
        stages_config = normalized.get("stages") or {}
        baseline = builtin_flow_definition(list(output_types))
        stage_by_code = {stage.code: stage for stage in flow_definition.stages}
        stage_by_ref: dict[str, ManagedStageDefinition] = {}
        for stage in flow_definition.stages:
            for ref in stage.operator_refs:
                stage_by_ref[ref] = stage
        for node in baseline.get("nodes", []):
            stage = stage_by_ref.get(node.get("ref")) or (stage_by_code.get("submit") if node.get("kind") == "knowledge_sink" else None)
            if stage:
                node["stage_id"] = stage.code
                node["stage_code"] = stage.code
                node["stage_label"] = stage.name
        for stage_code, stage in stage_by_code.items():
            stage_value = stages_config.get(stage_code)
            if not isinstance(stage_value, dict):
                continue
            self._apply_config(baseline, stage, stage_value.get("config") or {})
        return baseline

    def _apply_config(self, baseline: dict[str, Any], stage: ManagedStageDefinition, config: dict[str, Any]) -> None:
        if not config:
            return
        # Stage relation names remain a simple string-list UI; the Graph Schema
        # and runtime still require definitions and stable codes, respectively.
        relations = [{"code": name if re.fullmatch(r"[a-z][a-z0-9_]*", name)
                      else custom_type_code(name), "label": name, "description": ""}
                     for name in config.get("relation_types", [])]
        for node in baseline.get("nodes", []):
            if node.get("kind") != "operator":
                continue
            ref = node.get("ref")
            if ref not in stage.operator_refs:
                continue
            params = node.setdefault("params", {})
            if "llm_serving" in config and "llm_serving" in params:
                params["llm_serving"] = config["llm_serving"]
            if ref == "qa-extractor" and "questions_per_chunk" in config:
                params["questions_per_chunk"] = config["questions_per_chunk"]
            if ref == "qa-extractor" and "extraction_instructions" in config:
                params["extraction_instructions"] = config["extraction_instructions"]
            if ref == "prompt-generator" and "prompt_template_revision_id" in config:
                params["prompt_template_revision_id"] = config["prompt_template_revision_id"]
            if ref == "entity-extractor" and "entity_types" in config:
                params["entity_types"] = [item["code"] for item in config["entity_types"]]
                params["entity_type_scope"] = "all"
            if ref == "relation-extractor" and "relation_types" in config:
                params["relation_types"] = [item["code"] for item in relations]
        if "entity_types" in config or "relation_types" in config:
            graph_config = baseline.setdefault("graph_config", {})
            if "entity_types" in config:
                graph_config["entity_types"] = deepcopy(config["entity_types"])
            if "relation_types" in config:
                graph_config["relation_types"] = relations


class FlowAuthoringCompiler:
    """Single compile entry point for both authoring modes."""

    def __init__(self, catalog: ManagedFlowCatalog | None = None):
        self.catalog = catalog or ManagedFlowCatalog()
        self.managed = ManagedFlowCompiler(self.catalog)

    def materialize(self, definition: dict[str, Any], output_types: list[str] | None = None) -> dict[str, Any]:
        return self.managed.materialize(definition, output_types)

    def compile(self, flow_compiler: FlowCompiler, *, authoring_mode: str, definition: dict[str, Any],
                output_types: list[str]) -> dict[str, Any]:
        if authoring_mode == "standard":
            flow_dsl = self.managed.materialize(definition, output_types)
        elif authoring_mode == "advanced":
            flow_dsl = dict(definition or {})
        else:
            raise FlowValidationError(f"未知的流程编辑模式：{authoring_mode}")
        return flow_compiler.compile(flow_dsl)


MANAGED_FLOW_CATALOG = ManagedFlowCatalog()
FLOW_AUTHORING_COMPILER = FlowAuthoringCompiler(MANAGED_FLOW_CATALOG)
