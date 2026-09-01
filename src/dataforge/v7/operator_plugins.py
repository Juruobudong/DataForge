"""Admin publication of installed, reviewed operator packages; no code uploads."""
from copy import deepcopy
import re
from uuid import uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import select

from .catalog import OPERATOR_CATEGORIES, PLATFORM_RESERVED_OPERATOR_CODES
from .models import OperatorDefinition, OperatorVersion, OperatorValidationRun, utc_now
from .operator_catalog import version_payload
from .operator_runtime_contract import validate_runtime_requirements
from .operators.base import OperatorExecutionContext
from .operators.custom import CustomOperatorExecutor, PROTECTED, validate_records
from .operators.runtime import OperatorRuntime, digest


ARTIFACT_TYPES = {"parsed_document", "document_row_set", "flow_chunk_review_snapshot", "entity_candidate_set", "relation_candidate_set", "semantic_relation_set",
                  "candidate:text", "candidate:qa", "candidate:graph:triple", "candidate:graph:semantic"}


def validate_manifest(raw):
    value = deepcopy(raw)
    required = {"code", "name", "display_name_zh", "category", "executor", "package", "package_version", "package_digest", "implementation",
                "input_ports", "output_ports", "parameter_schema", "input_example", "output_example"}
    if not isinstance(value, dict) or required - value.keys():
        raise ValueError("Manifest 缺少必需字段")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,100}", value["code"]) or value["code"].casefold() in PLATFORM_RESERVED_OPERATOR_CODES:
        raise ValueError("自定义 code 不合法或覆盖平台保留算子")
    if {"source", "catalog_group", "provider", "driver"} & value.keys():
        raise ValueError("算子来源、目录分组与 Runtime Driver 由 DataForge 管理，Manifest 不能声明")
    if value["category"] not in OPERATOR_CATEGORIES:
        raise ValueError("自定义算子 category 必须使用受控业务分类")
    if value["executor"] not in {"dataflow-storage", "dataflow-llm", "custom-native"}:
        raise ValueError("不支持的自定义执行器")
    if value.get("surfaces", ["advanced-canvas"]) != ["advanced-canvas"]:
        raise ValueError("自定义算子只允许 advanced-canvas")
    if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*(?::|\.)[a-zA-Z_]\w*", value["implementation"]):
        raise ValueError("implementation 必须是包内模块和类路径")
    if not re.fullmatch(r"[a-f0-9]{64}", value["package_digest"]):
        raise ValueError("package_digest 必须是 SHA-256")
    for field, port in (("input_ports", "input"), ("output_ports", "output")):
        if any(key in value[field].get(port, {}) for key in ("accepted_types", "output_by_input")):
            raise ValueError("自定义算子不能声明平台多态正文端口")
        if set(value[field]) != {port} or value[field][port].get("artifact_type") not in ARTIFACT_TYPES:
            raise ValueError("首版仅支持一个已知类型的 input/output 端口")
        if value[field][port].get("binding", "edge") != "edge":
            raise ValueError("插件不能声明系统注入端口")
        if value[field][port].get("cardinality", "one") not in {"one", "many"}:
            raise ValueError("端口 cardinality 必须为 one 或 many")
    schema = value["parameter_schema"]
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("参数 Schema 必须显式禁止额外参数")
    if any(key.startswith("_") or key in {"knowledge_type", "graph_mode", "knowledge_library_id", "flow_chunk_review_snapshot_id"}
           for key in schema.get("properties", {})):
        raise ValueError("参数 Schema 不能开放系统运行参数")
    kinds = value.get("knowledge_types", ["text", "qa", "graph"])
    if not isinstance(kinds, list) or not kinds or any(kind not in {"text", "qa", "graph"} for kind in kinds):
        raise ValueError("自定义算子必须声明支持的知识类型")
    if not isinstance(value.get("graph_modes", []), list) or any(mode not in {"triple", "semantic"} for mode in value.get("graph_modes", [])):
        raise ValueError("未知图谱模式")
    if not set(value.get("init_parameters", [])).issubset(schema.get("properties", {})):
        raise ValueError("构造参数不属于业务参数 Schema")
    capabilities = set(value.get("capabilities", []))
    if capabilities - {"source-review-read", "model-serving"}:
        raise ValueError("插件申请了未批准的文件、网络或业务权限")
    uses_llm = bool(value.get("uses_llm", value["executor"] == "dataflow-llm"))
    if uses_llm and "model-serving" not in capabilities:
        raise ValueError("LLM 插件必须声明 model-serving 权限")
    if not 1 <= value.get("timeout_seconds", 300) <= 600:
        raise ValueError("超时必须在 1–600 秒")
    for field in ("input_mapping", "output_mapping"):
        mapping = value.get(field, {})
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in mapping.items()):
            raise ValueError("字段映射必须为非空字段名称映射，不接受脚本")
        if "_df_row" in mapping or "_df_row" in mapping.values():
            raise ValueError("来源关联键由系统维护")
    if value["executor"] != "custom-native" and (not value.get("input_mapping") or not value.get("output_mapping")):
        raise ValueError("DataFlow 插件必须声明输入输出字段映射")
    if set(value.get("output_mapping", {}).values()) & set(PROTECTED):
        raise ValueError("不能将插件输出映射到受保护来源字段")
    if any(not isinstance(k, str) or not (k.startswith("input_") or k.startswith("output_")) or not isinstance(v, str) for k, v in value.get("run_arguments", {}).items()):
        raise ValueError("run_arguments 只能声明输入输出字段名")
    for key, ports in (("input_example", "input_ports"), ("output_example", "output_ports")):
        records = value[key].get("input" if key == "input_example" else "output")
        validate_records(records, value[ports]["input" if key == "input_example" else "output"]["artifact_type"])
        if not records:
            raise ValueError("发布样例不能为空")
    value.update(surfaces=["advanced-canvas"], uses_llm=uses_llm, knowledge_types=kinds)
    return value


class OperatorPluginService:
    def __init__(self, store):
        self.store = store

    def register(self, manifest):
        value = validate_manifest(manifest)
        runtime = {key: deepcopy(value[key]) for key in ("executor", "package", "package_version", "package_digest", "implementation",
                   "uses_llm", "input_mapping", "output_mapping", "init_parameters", "run_arguments", "capabilities", "timeout_seconds", "knowledge_types", "graph_modes") if key in value}
        runtime.update(driver="custom", adapter_version="custom-records-v1", approved=False,
                       manifest=value, manifest_digest=digest(value))
        validate_runtime_requirements(runtime)
        self.store.require_operator_runtime(runtime)
        with self.store.sessions.begin() as session:
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == value["code"]))
            if definition is None:
                definition = OperatorDefinition(id=f"op_{uuid4().hex}", code=value["code"], name=value["name"],
                    display_name_zh=value["display_name_zh"], summary=value.get("description", value["name"]),
                    description=value.get("description", value["name"]), source="custom", catalog_group="custom",
                    category=value["category"], subcategory="",
                    scenarios=["已审核来源知识处理"], knowledge_types=value.get("knowledge_types", ["text", "qa", "graph"]),
                    surfaces=["advanced-canvas"], exposure="controlled", risk_level="advanced", enabled=True, lifecycle_status="draft")
                session.add(definition); session.flush()
            previous = session.scalars(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id)).all()
            if definition.source != "custom" or definition.catalog_group != "custom":
                raise ValueError("不能覆盖平台版本")
            if definition.category != value["category"]:
                raise ValueError("同一自定义算子 code 的业务分类不能跨版本变更")
            number = max((row.version_no for row in previous), default=0) + 1
            version = OperatorVersion(id=f"oprev_{uuid4().hex}", operator_definition_id=definition.id, version_no=number,
                status="draft", adapter_code="custom-records-v1", input_ports=value["input_ports"], output_ports=value["output_ports"],
                input_example=value["input_example"], output_example=value["output_example"], parameter_schema=value["parameter_schema"],
                parameter_docs=value.get("parameter_docs", {}), runtime_requirements=runtime)
            session.add(version)
            self.store.audit(session, "operator.registered", "operator_version", version.id, {"code": definition.code, "version": number})
            return {"code": definition.code, "version": number, "version_id": version.id, "status": "draft"}

    def versions(self):
        with self.store.sessions() as session:
            rows = session.execute(select(OperatorDefinition, OperatorVersion).join(OperatorVersion).order_by(OperatorVersion.created_at.desc())).all()
            return [version_payload(definition, version) for definition, version in rows if definition.source == "custom"]

    def start_validation(self, code, number):
        with self.store.sessions.begin() as session:
            version = self._version(session, code, number)
            run = OperatorValidationRun(id=f"opcheck_{uuid4().hex}", operator_version_id=version.id,
                                        manifest_digest=digest(version.runtime_requirements), status="queued")
            session.add(run)
            return {"id": run.id, "status": run.status}

    @staticmethod
    def _version(session, code, number):
        version = session.scalar(select(OperatorVersion).join(OperatorDefinition).where(
            OperatorDefinition.code == code, OperatorDefinition.source == "custom", OperatorVersion.version_no == number))
        if not version:
            raise ValueError("自定义算子版本不存在")
        return version

    def validate(self, run_id, *, local=False):
        import os
        runner_url = os.getenv("DATAFORGE_RUNNER_URL")
        if runner_url and not local:
            import httpx
            try:
                response = httpx.post(runner_url.rstrip("/") + "/internal/operators/validate",
                    json={"run_id": run_id}, headers={"Authorization": "Bearer " + os.getenv("DATAFORGE_RUNNER_SERVICE_TOKEN", "")}, timeout=650)
                response.raise_for_status()
            except Exception:
                with self.store.sessions.begin() as session:
                    run = session.get(OperatorValidationRun, run_id)
                    if run and run.status in {"queued", "running"}:
                        run.status, run.report = "failed", {"error": "Runner 验证调用失败，请检查 Runner 后重新验证"}
            return
        with self.store.sessions.begin() as session:
            run = session.get(OperatorValidationRun, run_id)
            if not run or run.status != "queued": return
            version = session.get(OperatorVersion, run.operator_version_id)
            definition = session.get(OperatorDefinition, version.operator_definition_id)
            spec = version_payload(definition, version)
            run.status = "running"
        report, runtime_digest = {}, None
        try:
            requirements = spec["runtime_requirements"]
            state = self.store.require_operator_runtime(requirements)
            runtime_digest = state.get("runtime_digest")
            manifest = validate_manifest(requirements["manifest"])
            params = self.store._schema_defaults(spec["parameter_schema"], manifest.get("sample_parameters", {}))
            Draft202012Validator(spec["parameter_schema"]).validate(params)
            from .operators.custom import CustomOperatorExecutor
            responses = iter(manifest.get("sample_serving_outputs", []))
            context = OperatorExecutionContext(run_id, "sample", runtime={"validation": True,
                "operator_serving": lambda message: [next(responses) for _ in message["user_inputs"]]})
            result = CustomOperatorExecutor(spec).execute(inputs=spec["input_example"]["input"], params=params, context=context)
            expected = spec["output_example"]["output"]
            if len(result.outputs) != len(expected) or any(any(actual.get(key) != value for key, value in row.items()) for actual, row in zip(result.outputs, expected)):
                raise ValueError("样例执行输出与声明不一致")
            # Cancellation is enforced by the host before it accepts any result.
            context.runtime["cancelled"] = lambda: True
            try:
                CustomOperatorExecutor(spec).execute(inputs=spec["input_example"]["input"], params=params, context=context)
            except ValueError as exc:
                if "OPERATOR_CANCELLED" not in str(exc): raise
            else:
                raise ValueError("取消控制验证失败")
            try:
                OperatorRuntime().call(requirements, records=[], action="check", timeout=0)
            except ValueError as exc:
                if "OPERATOR_TIMEOUT" not in str(exc): raise
            else:
                raise ValueError("超时控制验证失败")
            report = {"checks": ["dependencies", "implementation", "parameters", "sample", "output_contract", "lineage", "cancellation", "host_timeout"],
                      "sample_output": result.outputs, "serving": "stub" if requirements.get("uses_llm") else "not_used",
                      "trust_boundary": "reviewed-code; process isolation is not a security sandbox"}
            status = "passed"
        except Exception as exc:
            status, report = "failed", {"error": str(exc)}
        with self.store.sessions.begin() as session:
            run = session.get(OperatorValidationRun, run_id)
            run.status, run.report, run.runtime_digest = status, report, runtime_digest
            self.store.audit(session, "operator.validated", "operator_validation_run", run.id, {"status": status})

    def report(self, run_id):
        with self.store.sessions() as session:
            run = session.get(OperatorValidationRun, run_id)
            if not run: raise ValueError("验证记录不存在")
            return {"id": run.id, "status": run.status, "report": run.report, "manifest_digest": run.manifest_digest, "runtime_digest": run.runtime_digest}

    def publish(self, code, number):
        with self.store.sessions.begin() as session:
            version = self._version(session, code, number)
            if version.status == "published": return {"code": code, "version": number, "status": "published"}
            state = self.store.require_operator_runtime(version.runtime_requirements)
            proof = session.scalar(select(OperatorValidationRun).where(OperatorValidationRun.operator_version_id == version.id,
                OperatorValidationRun.status == "passed", OperatorValidationRun.manifest_digest == digest(version.runtime_requirements),
                OperatorValidationRun.runtime_digest == state.get("runtime_digest")).order_by(OperatorValidationRun.created_at.desc()))
            if not proof: raise ValueError("需要对当前 Manifest 与运行环境重新验证，通过后才能发布")
            version.runtime_requirements = {**version.runtime_requirements, "approved": True, "validation_run_id": proof.id,
                                            "environment_digest": state["runtime_digest"]}
            version.status, version.published_at = "published", utc_now()
            definition = session.get(OperatorDefinition, version.operator_definition_id)
            definition.latest_version, definition.lifecycle_status = max(definition.latest_version or 0, number), "published"
            self.store.audit(session, "operator.published", "operator_version", version.id, {"validation_run_id": proof.id})
            return {"code": code, "version": number, "status": "published"}
