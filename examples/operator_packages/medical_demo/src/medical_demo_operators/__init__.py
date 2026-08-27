"""Small maintainer-reviewed examples used by the custom operator contract tests."""
from copy import deepcopy
import time


class MedicalEntityDictionaryMatcher:
    """DataFlow-compatible operator; the algorithm belongs to this package."""
    def __init__(self, terms=None):
        self.terms = list(terms or ["高血压", "糖尿病"])

    def run(self, storage, input_key="text", output_key="entities"):
        frame = storage.read("dataframe")
        frame[output_key] = frame[input_key].apply(lambda text: [
            {"name": term, "type": "疾病", "confidence": 1.0} for term in self.terms if term in text])
        storage.write(frame)
        return [output_key]


class NativeReviewedSuffix:
    """Native protocol example; it receives only the serialized read-only context."""
    code = "native-reviewed-suffix"
    version = 1

    def execute(self, *, inputs, params, context):
        suffix = params.get("suffix", "（已审核）")
        if params.get("delay_seconds"):
            time.sleep(params["delay_seconds"])
        return {"outputs": [{**deepcopy(value), "canonical_content": value["canonical_content"] + suffix} for value in inputs]}
