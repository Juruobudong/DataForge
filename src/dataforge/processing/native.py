from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from ..models import ProcessingResult


WHITESPACE_RE = re.compile(r"[\t\f\v ]+")
PARAGRAPH_RE = re.compile(r"\n{2,}")


def normalize_medical_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if max_chars < 50:
        raise ValueError("chunk_size must be at least 50")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")

    paragraphs = [part.strip() for part in PARAGRAPH_RE.split(text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        step = max_chars - overlap
        start = 0
        while start < len(paragraph):
            chunks.append(paragraph[start : start + max_chars])
            start += step
    if current:
        chunks.append(current)
    return chunks or ([text] if text else [])


def transform_records(
    records: Iterable[dict[str, Any]],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    source_records = 0

    for record in records:
        source_records += 1
        normalized = normalize_medical_text(record.get("raw_content"))
        for chunk_index, content in enumerate(split_text(normalized, chunk_size, chunk_overlap)):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
            output.append(
                {
                    "chunk_id": f"chk_{digest[:24]}",
                    "document_id": record["document_id"],
                    "source_id": record["source_id"],
                    "source_version_id": record["source_version_id"],
                    "source_record_index": int(record["source_record_index"]),
                    "chunk_index": chunk_index,
                    "content": content,
                    "content_sha256": digest,
                    "char_count": len(content),
                }
            )

    metrics = {
        "input_records": source_records,
        "output_chunks": len(output),
        "deduplicated_chunks": duplicates,
        "average_chunk_chars": round(
            sum(item["char_count"] for item in output) / len(output), 2
        )
        if output
        else 0,
    }
    return output, metrics


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class NativeMedicalEngine:
    name = "native"
    version = "1"

    def run(self, input_file: Path, work_dir: Path, parameters: dict[str, Any]) -> ProcessingResult:
        records = read_jsonl(input_file)
        transformed, metrics = transform_records(
            records,
            chunk_size=int(parameters.get("chunk_size", 600)),
            chunk_overlap=int(parameters.get("chunk_overlap", 80)),
        )
        output = work_dir / "output" / "medical_chunks.jsonl"
        write_jsonl(output, transformed)
        return ProcessingResult(
            output_file=output,
            engine_name=self.name,
            engine_version=self.version,
            record_count=len(transformed),
            schema=asset_schema(),
            metrics=metrics,
        )


def asset_schema() -> dict[str, str]:
    return {
        "chunk_id": "string",
        "document_id": "string",
        "source_id": "string",
        "source_version_id": "string",
        "source_record_index": "integer",
        "chunk_index": "integer",
        "content": "string",
        "content_sha256": "string",
        "char_count": "integer",
    }
