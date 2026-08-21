"""Literal detection and normalisation for graph objects.

A ``literal`` is a measured or typed value (number, range, percentage, dose,
temperature, duration, date, boolean, plain string) that must never become a
graph entity.  Detection here is the deterministic rule layer; the final
decision is the three-way guarantee of rule detection + LLM ``object_kind`` +
Schema/Quality Validator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

LITERAL_DATATYPES: tuple[str, ...] = (
    "number", "range", "percentage", "duration", "temperature",
    "dosage", "date", "boolean", "string",
)

_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"[A-Za-zµμ℃°%/]{1,12}"
_DATE_RE = re.compile(r"^(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})$")
_PERCENT_RE = re.compile(rf"^({_NUM})\s*%$")
_TEMP_RANGE_RE = re.compile(rf"^({_NUM})\s*[~～\-]\s*({_NUM})\s*[℃°]C?$")
_TEMP_RE = re.compile(rf"^({_NUM})\s*[℃°]C?$")
_DURATION_RE = re.compile(rf"^(?:<\s*)?({_NUM})\s*(s|sec|min|h|hr|ms|秒|分钟|小时|天|d)$", re.IGNORECASE)
_DURATION_RANGE_RE = re.compile(rf"^({_NUM})\s*[~～\-]\s*({_NUM})\s*(s|sec|min|h|hr|ms|秒|分钟|小时|天|d)$", re.IGNORECASE)
_DOSAGE_RE = re.compile(rf"^({_NUM})\s*(?:[~～\-]\s*({_NUM}))?\s*(mg/kg|ml/kg|g/kg|ug/kg|iu/kg)$", re.IGNORECASE)
_RANGE_RE = re.compile(rf"^({_NUM})\s*[~～\-]\s*({_NUM})\s*({_UNIT})?$")
_NUMBER_RE = re.compile(rf"^(?:<\s*|>\s*|≤\s*|≥\s*)?({_NUM})\s*({_UNIT})?$")
_BOOL_RE = re.compile(r"^(true|false|yes|no|是|否)$", re.IGNORECASE)


@dataclass(frozen=True)
class LiteralValue:
    datatype: str
    raw_value: str
    normalized_value: Any
    unit: str | None = None


def _number(value: str) -> int | float:
    return int(value) if re.fullmatch(r"\d+", value) else float(value)


def detect_literal(value: str) -> LiteralValue | None:
    """Return a :class:`LiteralValue` if ``value`` is a literal, else ``None``."""
    text = str(value or "").strip()
    if not text:
        return None

    if _BOOL_RE.match(text):
        return LiteralValue("boolean", text, text.lower() in {"true", "yes", "是"})

    match = _PERCENT_RE.match(text)
    if match:
        return LiteralValue("percentage", text, _number(match.group(1)), "%")

    match = _TEMP_RANGE_RE.match(text)
    if match:
        return LiteralValue("temperature", text, {"min": _number(match.group(1)), "max": _number(match.group(2))}, "℃")

    match = _TEMP_RE.match(text)
    if match:
        return LiteralValue("temperature", text, _number(match.group(1)), "℃")

    match = _DURATION_RE.match(text)
    if match:
        return LiteralValue("duration", text, _number(match.group(1)), match.group(2))

    match = _DURATION_RANGE_RE.match(text)
    if match:
        return LiteralValue("duration", text, {"min": _number(match.group(1)), "max": _number(match.group(2))}, match.group(3))

    match = _DOSAGE_RE.match(text)
    if match:
        unit = match.group(3)
        normalized: Any = _number(match.group(1))
        if match.group(2) is not None:
            normalized = {"min": _number(match.group(1)), "max": _number(match.group(2))}
        return LiteralValue("dosage", text, normalized, unit)

    match = _RANGE_RE.match(text)
    if match:
        unit = match.group(3) or None
        return LiteralValue("range", text, {"min": _number(match.group(1)), "max": _number(match.group(2))}, unit)

    match = _NUMBER_RE.match(text)
    if match:
        return LiteralValue("number", text, _number(match.group(1)), match.group(2) or None)

    if _DATE_RE.match(text):
        return LiteralValue("date", text, text)

    return None


def literal_payload(value: LiteralValue) -> dict[str, Any]:
    """The ``data`` JSON block carried by a triple literal object."""
    return {
        "object_kind": "literal",
        "literal_datatype": value.datatype,
        "literal_unit": value.unit,
        "literal_raw_value": value.raw_value,
        "literal_normalized_value": value.normalized_value,
    }


def classify_object(text: str) -> dict[str, Any]:
    """Classify one triple object string as entity or literal metadata.

    Returns ``{"object_kind": "literal", ...}`` when literal, otherwise
    ``{"object_kind": "entity"}``.  This is the rule layer; the runner still
    confirms against the LLM ``object_kind`` and the schema/quality gates.
    """
    literal = detect_literal(text)
    return literal_payload(literal) if literal else {"object_kind": "entity"}
