from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class RunStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"


class AssetStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


@dataclass(frozen=True)
class IngestResult:
    source: dict[str, Any]
    source_version: dict[str, Any]
    created: bool


@dataclass(frozen=True)
class ProcessingResult:
    output_file: Path
    engine_name: str
    engine_version: str
    record_count: int
    schema: dict[str, str]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class FlowResult:
    source: dict[str, Any]
    source_version: dict[str, Any]
    run: dict[str, Any]
    asset: dict[str, Any]
    asset_version: dict[str, Any]
