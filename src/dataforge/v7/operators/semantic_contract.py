"""Reviewed semantic model identity; safe to load in isolated maintenance/worker Python."""

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
PROFILE = "semantic-multilingual-v1"
MAX_GROUP_RECORDS = 5000


def validate_model_bundle(bundle):
    if (not isinstance(bundle, dict) or bundle.get("semantic_model") != MODEL
            or bundle.get("semantic_revision") != REVISION):
        raise ValueError("OPERATOR_RESOURCE_DRIFT: 语义模型或修订不符合审核契约")
