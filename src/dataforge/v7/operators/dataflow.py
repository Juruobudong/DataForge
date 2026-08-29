"""Thin artifact adapters. Algorithms execute exclusively in the upstream package."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from openai import APITimeoutError

from .base import OperatorResult
from .runtime import OperatorRuntime
from .diagnostics import capture_operator_diagnostics
from .outcomes import capture_generation_metrics
from .qa import QAChunkSession, generate_qa_chunks, serving_snapshot
from ..llm_serving import get_llm_serving_registry


def serving_call(params, runtime):
    override = runtime.get("operator_serving")
    if override:
        return override
    registry = runtime.get("llm_serving_registry") or get_llm_serving_registry()
    serving_id = params.get("llm_serving")
    def call(message):
        frozen = params.get("_resolved_serving")
        if not frozen or serving_snapshot(registry, serving_id) != frozen:
            raise ValueError("SERVING_CONFIG_DRIFT: 模型配置与发布快照不一致，请重新发布")
        config, client = registry.client(serving_id)
        diagnostics = runtime.get("_operator_diagnostics")
        if diagnostics:
            diagnostics.add_secrets({"api_key": getattr(client, "api_key", None)})
        outputs = []
        for prompt in message["user_inputs"]:
            messages = []
            if message.get("system_prompt"):
                messages.append({"role": "system", "content": message["system_prompt"]})
            messages.append({"role": "user", "content": prompt})
            kwargs = {"model": config.model_name, "messages": messages, "max_tokens": config.max_tokens}
            kwargs["extra_body"] = {"app_id": "dataforge"}
            if config.disable_thinking:
                kwargs["extra_body"]["chat_template_kwargs"] = {"enable_thinking": False}
            try:
                answer = client.chat.completions.create(**kwargs)
            except APITimeoutError as exc:
                raise TimeoutError(f"上游 LLM Serving {config.id} 请求超时") from exc
            except Exception as exc:
                # Never persist provider response bodies, URLs or credentials in artifacts.
                raise RuntimeError(f"上游 LLM Serving {config.id} 调用失败（{type(exc).__name__}）") from exc
            text = answer.choices[0].message.content
            if not isinstance(text, str) or not text.strip():
                raise ValueError("LLM 返回空正文")
            outputs.append(text)
        return outputs
    return call


class DataFlowOperatorExecutor:
    def __init__(self, spec, runtime=None):
        self.code, self.version = spec["code"], spec["version"]
        self.spec = deepcopy(spec["runtime_requirements"])
        self.parameter_schema = deepcopy(spec.get("parameter_schema") or {})
        self.adapter = self.spec["adapter_version"]
        self.runtime = runtime or OperatorRuntime()

    @capture_operator_diagnostics
    @capture_generation_metrics
    def execute(self, *, inputs, params, context):
        values = deepcopy(inputs)
        if not values:
            return OperatorResult()
        callback = serving_call(params, context.runtime) if self.spec.get("uses_llm") and self.adapter != "source-chunk-to-qa-v4" else None
        if self.adapter in {"governance-evaluate-v1", "governance-multihop-v1"}:
            from .governance import guarded_serving
            callback = guarded_serving(self.adapter, callback)
        if self.adapter == "candidate-prompted-filter-v1":
            raw_callback = callback
            def scored_callback(message):
                outputs = raw_callback(message)
                if (len(outputs) != len(message.get("user_inputs", []))
                        or any(not isinstance(value, str) or value.strip() not in {"1", "2", "3", "4", "5"} for value in outputs)):
                    raise ValueError("PROMPTED_FILTER_SCORE_INVALID: 模型必须为每条输入返回一个1–5整数分数")
                return outputs
            callback = scored_callback
        def invoke(records, **kwargs):
            serving = callback
            session = None
            if self.adapter == "source-chunk-to-qa-v4":
                session = QAChunkSession(params, context, records[0]["_df_row"], "dataflow")
                serving = session.dataflow_callback(params.get("questions_per_chunk", 1))
                kwargs["timeout"] = session.remaining()
            result = self.runtime.call(self.spec, records=records, serving=serving,
                                     cancelled=context.runtime.get("cancelled"),
                                     diagnostics=context.runtime["_operator_diagnostics"], **kwargs)
            if session:
                session.remaining()
                session.succeeded()
            return result.get("early_result", result["outputs"])
        if self.adapter.startswith("governance-"):
            from .governance import execute_governance
            return execute_governance(self, values, params, context, invoke)
        if self.adapter == "source-chunk-to-qa-v4":
            from .derived_text import prepare_generation, restore_evidence
            inputs, originals = prepare_generation(values, "qa", context)
            outputs = self._qa(inputs, params, context, invoke, allow_no_match=True) if inputs else []
            outputs = restore_evidence(outputs, originals, "qa", context)
        elif self.adapter in {"candidate-hash-deduplicate-v1", "candidate-minhash-deduplicate-v1"}:
            if "method" in params:
                raise ValueError("去重算法由算子身份固定，不接受 method 参数")
            method = "identity" if self.adapter == "candidate-hash-deduplicate-v1" else "minhash"
            outputs = self._deduplicate(values, params, invoke, method=method)
        elif self.adapter == "candidate-refiner-v1":
            outputs = self._refine(values, params, invoke)
        elif self.adapter in {"candidate-row-filter-v1", "candidate-ngram-deduplicate-v1",
                             "candidate-simhash-deduplicate-v1", "candidate-prompted-filter-v1"}:
            outputs = self._filter_candidates(values, params, invoke)
        else:
            raise ValueError(f"未批准的 DataFlow 字段适配器：{self.adapter}")
        metrics = {"input_records": len(values), "output_records": len(outputs)}
        if self.adapter == "source-chunk-to-qa-v4":
            metrics["qa_recovery"] = dict(context.runtime.get("_qa_recovery", {}))
        return OperatorResult(outputs=outputs, metrics=metrics)

    def _filter_candidates(self, values, params, invoke):
        from jsonschema import Draft202012Validator

        properties = self.parameter_schema.get("properties", {})
        business = {key: deepcopy(params[key] if key in params else spec["default"])
                    for key, spec in properties.items() if key in params or "default" in spec}
        Draft202012Validator(self.parameter_schema).validate(business)
        if params.get("knowledge_type") not in {"text", "qa"}:
            raise ValueError("精选过滤器仅支持文本与问答候选")
        init = {key: value for key, value in business.items() if key not in {"llm_serving", "prompt_template_revision_id"}}
        if self.adapter == "candidate-prompted-filter-v1":
            if init["min_score"] > init["max_score"]:
                raise ValueError("最低保留分不能高于最高保留分")
            prompt = (params.get("_resolved_prompt_template") or {}).get("body")
            if not prompt:
                raise ValueError("智能过滤必须使用已冻结 Prompt Revision")
            init["system_prompt"] = prompt
        groups = defaultdict(list)
        deduplicate = self.adapter in {"candidate-ngram-deduplicate-v1", "candidate-simhash-deduplicate-v1"}
        for index, value in enumerate(values):
            if not all(value.get(key) for key in ("source_knowledge_id", "source_chunk_id", "source_version_ids")):
                raise ValueError("SOURCE_LINEAGE_MISSING: 过滤候选缺少来源身份")
            content = value.get("canonical_content")
            if not isinstance(content, str) and not (self.code == "ContentNullFilter" and content is None):
                raise ValueError("候选正文必须是字符串")
            group = (tuple(value["source_version_ids"]), value["source_chunk_id"]) if deduplicate else ()
            groups[group].append({"_df_row": index, "text": content})
        retained = set()
        for records in groups.values():
            batches = [(records, init, {})]
            if self.adapter == "candidate-ngram-deduplicate-v1":
                short = [row for row in records if len(row["text"]) < init["n_gram"]]
                normal = [row for row in records if len(row["text"]) >= init["n_gram"]]
                batches = [(short, {"hash_func": "sha256"}, {"implementation": self.spec["implementations"]["short_text"]}), (normal, init, {})]
            for batch, arguments, options in batches:
                if not batch:
                    continue
                rows = invoke(batch, init=arguments, run_arguments={"input_key": "text"}, **options)
                allowed = {row["_df_row"] for row in batch}
                for row in rows:
                    row_id = row.get("_df_row")
                    if type(row_id) is not int or row_id not in allowed or row_id in retained:
                        raise ValueError("SOURCE_LINEAGE_MISMATCH: 过滤器返回缺失、重复或未知行身份")
                    retained.add(row_id)
        return [deepcopy(value) for index, value in enumerate(values) if index in retained]

    _qa = staticmethod(generate_qa_chunks)

    def _deduplicate(self, values, params, invoke, *, method=None):
        method = method or params.get("method", "identity")
        if method not in {"identity", "minhash"}:
            raise ValueError("未知去重方式")
        groups = defaultdict(list)
        for index, value in enumerate(values):
            if not value.get("source_knowledge_id"):
                raise ValueError("候选缺少 source_knowledge_id")
            if method == "minhash":
                if params.get("knowledge_type") not in {"text", "qa"} or not value.get("source_chunk_id"):
                    raise ValueError("MinHash 仅支持带来源 Chunk 的文本与问答")
                group = (tuple(value.get("source_version_ids", [])), value["source_chunk_id"])
                text = value.get("canonical_content")
                if not isinstance(text, str) or not text:
                    raise ValueError("MinHash 候选正文不能为空")
            else:
                group, text = (), str(value["source_knowledge_id"])
            groups[group].append({"_df_row": index, "text": text})
        retained = set()
        for records in groups.values():
            # Short strings have empty n-gram signatures; use upstream exact hash
            # for this subgroup, rather than silently deleting unrelated records.
            batches = [records] if method == "identity" else [[r for r in records if len(r["text"]) < 5], [r for r in records if len(r["text"]) >= 5]]
            for batch in batches:
                if not batch:
                    continue
                algorithm = "minhash" if method == "minhash" and len(batch[0]["text"]) >= 5 else "identity"
                init = {"threshold": params.get("threshold", 0.9), "num_perm": 128, "ngram": 5} if algorithm == "minhash" else {"hash_func": "sha256"}
                rows = invoke(batch, init=init, implementation=self.spec["implementations"][algorithm], run_arguments={"input_key": "text"})
                valid = {r["_df_row"] for r in batch}
                for row in rows:
                    if row.get("_df_row") not in valid:
                        raise ValueError("SOURCE_LINEAGE_MISMATCH: 去重返回未知记录")
                    retained.add(row["_df_row"])
        return [value for index, value in enumerate(values) if index in retained]

    @staticmethod
    def _refine(values, params, invoke):
        prompt = (params.get("_resolved_prompt_template") or {}).get("body")
        kind = params.get("knowledge_type")
        if not prompt or kind not in {"text", "qa"}:
            raise ValueError("修订必须使用已冻结 Prompt，且仅支持文本与问答")
        records, originals = [], {}
        for index, value in enumerate(values):
            if not value.get("source_chunk_id") or not value.get("source_version_ids"):
                raise ValueError("SOURCE_LINEAGE_MISSING: 修订候选缺少血缘")
            payload = {"canonical_content": value["canonical_content"]} if kind == "text" else {key: value["data_json"][key] for key in ("question", "answer")}
            records.append({"_df_row": index, "text": json.dumps(payload, ensure_ascii=False)})
            originals[index] = value
        rows = invoke(records, init={"system_prompt": prompt}, run_arguments={"input_key": "text"})
        if len(rows) != len(values) or {row.get("_df_row") for row in rows} != set(originals):
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 修订结果不能增删或替换记录")
        outputs = []
        for row in rows:
            payload = json.loads(row["text"])
            expected = {"canonical_content"} if kind == "text" else {"question", "answer"}
            if not isinstance(payload, dict) or set(payload) != expected or any(not isinstance(v, str) or not v.strip() for v in payload.values()):
                raise ValueError("REFINER_OUTPUT_INVALID: 修订输出不满足知识契约")
            value = deepcopy(originals[row["_df_row"]])
            if kind == "qa":
                value["data_json"] = {**value["data_json"], **payload}
                value["canonical_content"] = f"{payload['question']} {payload['answer']}"
            else:
                value["canonical_content"] = payload["canonical_content"]
            outputs.append(value)
        return outputs
