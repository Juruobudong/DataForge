"""SourceAnchorV2 helpers shared by preparation, review and API payloads."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any


def normalize_pdf_bbox(value: Any) -> list[float] | None:
    """Normalize a MinerU bbox to top-left coordinates in the inclusive 0..1 range."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        coords = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(isfinite(item) for item in coords):
        return None
    if max(coords) > 1:
        if min(coords) < 0 or max(coords) > 1000:
            return None
        coords = [item / 1000 for item in coords]
    x0, y0, x1, y1 = coords
    if min(coords) < 0 or max(coords) > 1 or x1 <= x0 or y1 <= y0:
        return None
    return [round(item, 6) for item in coords]


def finalize_source_blocks(raw_blocks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Assign stable document offsets while preserving parser block metadata."""
    blocks: list[dict[str, Any]] = []
    parts: list[str] = []
    cursor = 0
    for raw in raw_blocks:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        if parts:
            parts.append("\n\n")
            cursor += 2
        block = {**raw, "text": text, "block_index": len(blocks), "char_start": cursor,
                 "char_end": cursor + len(text)}
        block.setdefault("block_id", f"block:{len(blocks)}")
        blocks.append(block)
        parts.append(text)
        cursor += len(text)
    return "".join(parts), blocks


def _position_key(position: dict[str, Any]) -> tuple[Any, ...]:
    bbox = tuple(position.get("bbox") or ())
    source_range = tuple(position.get("source_range") or ())
    return (
        position.get("kind"), position.get("block_id"), position.get("page_index"), bbox,
        position.get("block_index"), source_range,
    )


def sort_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate positions and keep PDF/DOCX reading order deterministic."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in positions:
        if not isinstance(raw, dict):
            continue
        position = deepcopy(raw)
        key = _position_key(position)
        existing = unique.get(key)
        if existing and isinstance(existing.get("chunk_range"), list) and isinstance(position.get("chunk_range"), list):
            existing["chunk_range"] = [
                min(int(existing["chunk_range"][0]), int(position["chunk_range"][0])),
                max(int(existing["chunk_range"][1]), int(position["chunk_range"][1])),
            ]
        else:
            unique[key] = position

    def order(item: dict[str, Any]) -> tuple[Any, ...]:
        bbox = item.get("bbox") or [0, 0, 0, 0]
        return (
            0 if item.get("kind") in {"pdf_bbox", "pdf_page"} else 1,
            int(item.get("page_index", 10**9)), float(bbox[1]), float(bbox[0]),
            int(item.get("block_index", 10**9)), str(item.get("block_id") or ""),
        )

    return sorted(unique.values(), key=order)


def api_source_anchor(anchor: dict[str, Any] | None) -> dict[str, Any]:
    """Return an explicit location precision without rewriting stored legacy anchors."""
    value = deepcopy(anchor or {})
    if value.get("anchor_version") == 2:
        value["positions"] = sort_positions(list(value.get("positions") or []))
        value.setdefault("precision", "block" if value["positions"] else "unavailable")
        return value
    value.setdefault("anchor_version", 1)
    value.setdefault("precision", "page" if value.get("page") or value.get("page_start") else "unavailable")
    return value


def edited_anchor(anchor: dict[str, Any] | None) -> dict[str, Any]:
    value = deepcopy(anchor or {})
    value["review_adjusted"] = True
    return value


def merge_source_anchors(anchors: list[dict[str, Any]], contents: list[str] | None = None) -> dict[str, Any]:
    values = [deepcopy(item or {}) for item in anchors]
    offsets: list[int] = []
    cursor = 0
    for index, value in enumerate(values):
        offsets.append(cursor)
        if contents and index < len(contents):
            cursor += len(contents[index]) + (2 if index + 1 < len(values) else 0)
        for position in value.get("positions") or []:
            chunk_range = position.get("chunk_range")
            if isinstance(chunk_range, list) and len(chunk_range) == 2:
                position["chunk_range"] = [int(chunk_range[0]) + offsets[-1], int(chunk_range[1]) + offsets[-1]]
    versioned = [item for item in values if item.get("anchor_version") == 2]
    if len(versioned) != len(values) or not versioned:
        legacy_pages = [int(page) for item in values for page in (
            item.get("page"), item.get("page_start"), item.get("page_end")
        ) if isinstance(page, int)]
        result = {"source_anchors": values, "review_merged": True,
                  "precision": "page" if legacy_pages else "unavailable"}
        if legacy_pages:
            result.update(page=min(legacy_pages), page_start=min(legacy_pages), page_end=max(legacy_pages))
        return result
    positions = sort_positions([position for item in versioned for position in item.get("positions") or []])
    starts = [int(item["char_start"]) for item in versioned if isinstance(item.get("char_start"), int)]
    ends = [int(item["char_end"]) for item in versioned if isinstance(item.get("char_end"), int)]
    pages = [int(position["page"]) for position in positions if isinstance(position.get("page"), int)]
    precision = "parent" if any(item.get("precision") == "parent" for item in versioned) else (
        "block" if positions else "unavailable"
    )
    result: dict[str, Any] = {
        "anchor_version": 2,
        "source_version_id": versioned[0].get("source_version_id"),
        "source_type": versioned[0].get("source_type"),
        "precision": precision,
        "positions": positions,
        "review_merged": True,
    }
    if starts and ends:
        result.update(char_start=min(starts), char_end=max(ends))
    if pages:
        result.update(page=min(pages), page_start=min(pages), page_end=max(pages))
    if any(item.get("position_status") == "partial" for item in versioned):
        result["position_status"] = "partial"
    return result


def sequential_part_ranges(content: str, parts: list[str]) -> list[tuple[int, int]] | None:
    """Locate trimmed split parts without accepting omitted non-whitespace source text."""
    cursor = 0
    ranges: list[tuple[int, int]] = []
    for part in parts:
        start = content.find(part, cursor)
        if start < 0 or content[cursor:start].strip():
            return None
        end = start + len(part)
        ranges.append((start, end))
        cursor = end
    if content[cursor:].strip():
        return None
    return ranges


def split_source_anchor(anchor: dict[str, Any] | None, child_range: tuple[int, int] | None) -> dict[str, Any]:
    value = deepcopy(anchor or {})
    if (value.get("anchor_version") != 2 or child_range is None or value.get("review_adjusted")
            or value.get("precision") == "parent"):
        value["precision"] = "parent" if value else "unavailable"
        value["review_split_inherited"] = True
        return value
    child_start, child_end = child_range
    positions: list[dict[str, Any]] = []
    for raw in value.get("positions") or []:
        chunk_range = raw.get("chunk_range")
        if not isinstance(chunk_range, list) or len(chunk_range) != 2:
            continue
        start, end = int(chunk_range[0]), int(chunk_range[1])
        intersection_start, intersection_end = max(start, child_start), min(end, child_end)
        if intersection_end <= intersection_start:
            continue
        positions.append({**raw, "chunk_range": [intersection_start - child_start, intersection_end - child_start]})
    value["positions"] = sort_positions(positions)
    if isinstance(value.get("char_start"), int):
        origin = int(value["char_start"])
        value["char_start"], value["char_end"] = origin + child_start, origin + child_end
    pages = [int(position["page"]) for position in positions if isinstance(position.get("page"), int)]
    if pages:
        value.update(page=min(pages), page_start=min(pages), page_end=max(pages))
    else:
        value.pop("page", None); value.pop("page_start", None); value.pop("page_end", None)
    if not positions:
        value["precision"] = "unavailable"
    elif all(position.get("kind") == "pdf_page" for position in positions):
        value["precision"] = "page"
    else:
        value["precision"] = "block"
    value["review_split"] = True
    return value
