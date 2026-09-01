"""Observe existing generation outcomes without changing execution decisions."""
from functools import wraps


def _chunk_key(value):
    version, chunk = value.get("source_version_id"), value.get("flow_chunk_id")
    return (str(version), str(chunk)) if version and chunk else None


def _keys(values):
    return {key for value in values if (key := _chunk_key(value)) is not None}


def capture_generation_metrics(execute):
    """Persist only counts, scoped to this invocation, including raised failures.

    Most generators append targeted/successful/failed chunks. Relation extraction
    instead revises the preceding entity stage's outcomes in place, so its input
    records define its own attempted scope. Never sum stages into a run total.
    """
    @wraps(execute)
    def wrapped(self, *, inputs, params, context):
        generation = context.runtime.setdefault("generation", {})
        before = {key: {name: len(value.get(name, [])) for name in ("targeted", "successful", "failed")}
                  for key, value in generation.items()}
        def collect(completed):
            summaries = []
            for key, outcome in generation.items():
                previous = before.get(key, {})
                failed = _keys(outcome.get("failed", [])[previous.get("failed", 0):])
                targeted = _keys(outcome.get("targeted", [])[previous.get("targeted", 0):])
                successful = _keys(outcome.get("successful", [])[previous.get("successful", 0):])
                relation_key = f"graph:{params.get('graph_mode')}"
                if self.code == "relation-extractor" and key == relation_key and (completed or failed):
                    targeted = _keys(inputs)
                    successful = _keys(result.outputs) if completed else set()
                failed &= targeted
                successful = (successful & targeted) - failed
                if targeted:
                    summaries.append({"output_key": key, "attempted_chunks": len(targeted),
                                      "successful_chunks": len(successful), "failed_chunks": len(failed)})
            return summaries

        result = None
        try:
            result = execute(self, inputs=inputs, params=params, context=context)
        except Exception as exc:
            metrics = collect(False)
            if metrics and "chunk_processing" not in getattr(exc, "operator_metrics", {}):
                exc.operator_metrics = {**getattr(exc, "operator_metrics", {}), "chunk_processing": metrics}
            raise
        else:
            metrics = collect(True)
            if metrics and "chunk_processing" not in result.metrics:
                result.metrics = {**result.metrics, "chunk_processing": metrics}
            return result

    return wrapped
