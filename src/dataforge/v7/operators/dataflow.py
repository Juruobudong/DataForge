"""Thin artifact adapters. Algorithms execute exclusively in the upstream package."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from openai import APITimeoutError

from .base import OperatorResult
from .runtime import OperatorRuntime
from ..llm_serving import get_llm_serving_registry


def serving_snapshot(registry, serving_id):
    value = registry.require(serving_id)
    keys = ("id", "type", "model_name", "base_url", "timeout_seconds", "max_retries", "max_tokens", "disable_thinking")
    return {key: getattr(value, key) for key in keys}


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
        self.adapter = self.spec["adapter_version"]
        self.runtime = runtime or OperatorRuntime()

    def execute(self, *, inputs, params, context):
        values = deepcopy(inputs)
        if not values:
            return OperatorResult()
        callback = serving_call(params, context.runtime) if self.spec.get("uses_llm") else None
        def invoke(records, **kwargs):
            return self.runtime.call(self.spec, records=records, serving=callback,
                                     cancelled=context.runtime.get("cancelled"), **kwargs)["outputs"]
        if self.adapter == "source-chunk-to-qa-v1":
            outputs = self._qa(values, params, context, invoke)
        elif self.adapter == "candidate-deduplicate-v1":
            outputs = self._deduplicate(values, params, invoke)
        elif self.adapter == "candidate-refiner-v1":
            outputs = self._refine(values, params, invoke)
        else:
            raise ValueError(f"未批准的 DataFlow 字段适配器：{self.adapter}")
        return OperatorResult(outputs=outputs, metrics={"input_records": len(values), "output_records": len(outputs)})

    @staticmethod
    def _qa(values, params, context, invoke):
        outputs = []
        runtime = context.runtime
        outcome = runtime.setdefault("generation", {}).setdefault("qa", {"successful": [], "failed": [], "targeted": []})
        store, job = runtime.get("store"), runtime.get("job_id")
        count = params.get("questions_per_chunk", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
            raise ValueError("questions_per_chunk 必须为 1–10")
        for chunk in values:
            scope = ("qa", str(chunk.get("source_version_id", "")), str(chunk.get("source_chunk_id", "")))
            if runtime.get("retry_scope") is not None and scope not in runtime["retry_scope"]:
                continue
            outcome["targeted"].append(chunk)
            try:
                if not all(chunk.get(key) for key in ("source_id", "source_version_id", "source_chunk_id")):
                    raise ValueError("SOURCE_LINEAGE_MISSING: 来源 Chunk 身份缺失")
                if not isinstance(chunk.get("content"), str):
                    raise ValueError("来源正文必须是字符串")
                generated = []
                if chunk["content"].strip():
                    generated = invoke([{"text": chunk["content"], "_df_row": chunk["source_chunk_id"]}],
                                       run_arguments={"input_key": "text", "input_question_num": count,
                                                      "output_question_key": "question", "output_answer_key": "answer"})
                    if not generated:
                        raise ValueError("QA_EMPTY_OUTPUT: 未生成合法问答")
                candidates = []
                for row in generated:
                    if row.get("_df_row") != chunk["source_chunk_id"]:
                        raise ValueError("SOURCE_LINEAGE_MISMATCH: 上游来源关联错误")
                    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("question", "answer")):
                        raise ValueError("QA_OUTPUT_INVALID: 问题和答案必须为非空文本")
                    data = {key: row[key].strip() for key in ("question", "answer")}
                    identity = f"{chunk['source_id']}|qa|{chunk.get('chunk_index', 0)}|{data['question']}"
                    candidates.append({
                        "source_knowledge_id": hashlib.sha256(identity.encode()).hexdigest(),
                        "canonical_content": f"{data['question']} {data['answer']}", "data_json": data,
                        "source_version_ids": [chunk["source_version_id"]], "source_chunk_id": chunk["source_chunk_id"],
                        "source_chunk_revision_id": chunk.get("source_chunk_revision_id"),
                        "source_review_snapshot_id": chunk.get("source_review_snapshot_id"),
                        "source_anchor": f"{chunk.get('filename', '')}#chunk-{chunk.get('chunk_index', 0)}",
                        "anchor_json": deepcopy(chunk.get("anchor_json") or chunk.get("anchor") or {}),
                        "evidence_text": chunk["content"], "is_primary": True,
                    })
                outcome["successful"].append(chunk); outputs.extend(candidates)
                if store and job:
                    store.record_chunk_generation(job, "qa", chunk, status="completed", candidate_count=len(candidates))
            except Exception as exc:
                if "OPERATOR_CANCELLED" in str(exc):
                    raise
                outcome["failed"].append({**chunk, "error": str(exc)})
                if store and job:
                    store.record_chunk_generation(job, "qa", chunk, status="failed", error=str(exc))
        if outcome["failed"] and not outcome["successful"]:
            raise ValueError(outcome["failed"][0]["error"])
        return outputs

    def _deduplicate(self, values, params, invoke):
        method = params.get("method", "identity")
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
