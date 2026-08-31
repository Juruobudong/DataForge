"""Business parameter validation independent of Store, compiler and executors."""
from copy import deepcopy
import math
from jsonschema import Draft202012Validator
from .flow_errors import FlowValidationError


class FlowParameterError(FlowValidationError):
    def __init__(self, code, message, *, node_id=None, field=None):
        super().__init__(message)
        self.code, self.message, self.node_id, self.field = code, message, node_id, field

    def payload(self):
        return {"code": self.code, "message": self.message, "node_id": self.node_id, "field": self.field}


RUNTIME_FIELDS = {"knowledge_type", "graph_mode", "_resolved_serving", "_resolved_prompt_template"}


def schema_defaults(schema, value):
    value = deepcopy(value)
    if isinstance(value, dict):
        for key, spec in schema.get("properties", {}).items():
            if key not in value and "default" in spec:
                value[key] = deepcopy(spec["default"])
            if key in value:
                value[key] = schema_defaults(spec, value[key])
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        value = [schema_defaults(schema["items"], item) for item in value]
    return value


def validate_parameters(schema, params, *, node_id=None, system=None, runtime=False):
    """Validate before projection; system values have an explicit trusted origin."""
    if params is None:
        params = {}
    def fail(message, field=None, code="PARAMETER_SCHEMA_INVALID"):
        raise FlowParameterError(code, message, node_id=node_id, field=field)
    if not isinstance(params, dict):
        fail("算子参数必须是对象")
    properties = schema.get("properties", {})
    trusted = dict(system or {})
    if runtime:
        trusted.update({key: value for key, value in params.items() if key in RUNTIME_FIELDS})
    business = {}
    for key, value in params.items():
        if key in RUNTIME_FIELDS:
            if key not in trusted or value != trusted[key]:
                fail(f"系统参数 {key} 由 Flow Contract 维护，不能修改", key, "SYSTEM_PARAMETER_NOT_EDITABLE")
            if key.startswith("_resolved_") and not isinstance(value, dict):
                fail(f"系统参数 {key} 必须是冻结对象", key)
            if key == "knowledge_type" and (not isinstance(value, str) or not value):
                fail("knowledge_type 必须是非空字符串", key)
            if key == "graph_mode" and value not in (None, "triple", "semantic"):
                fail("graph_mode 非法", key)
        elif key not in properties:
            fail(f"未知业务参数：{key}", key)
        else:
            business[key] = deepcopy(value)
    business = schema_defaults(schema, business)
    def finite(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                finite(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                finite(item, f"{path}[{index}]")
        elif isinstance(value, float) and not math.isfinite(value):
            fail("参数不能为 NaN 或 Infinity", path)
    finite(business)
    errors = sorted(Draft202012Validator(schema).iter_errors(business), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        field = ".".join(map(str, error.path)) or None
        if error.validator == "required":
            field = next((key for key in error.validator_value if key not in error.instance), field)
        fail(error.message, field)
    for low, high in (("min_length", "max_length"), ("min_mtld", "max_mtld"), ("min_hdd", "max_hdd"),
                      ("min_score", "max_score"), ("min_sentences", "max_sentences")):
        if low in business and high in business and business[low] > business[high]:
            fail("最小值不能大于最大值", low)
    return {**business, **trusted}


def business_parameters(schema, params, *, node_id=None):
    validated = validate_parameters(schema, params, node_id=node_id, runtime=True)
    return {key: value for key, value in validated.items() if key not in RUNTIME_FIELDS}
