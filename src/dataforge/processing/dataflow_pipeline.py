"""DataFlow-native operators and pipeline.

This module must only be imported after the DataFlow repository is available on
``sys.path``. Keeping it isolated prevents platform modules from depending on
DataFlow internals during metadata-only operations.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from dataflow.core import OperatorABC
from dataflow.pipeline import PipelineABC
from dataflow.utils.registry import OPERATOR_REGISTRY
from dataflow.utils.storage import DataFlowStorage, FileStorage

from .native import normalize_medical_text, split_text


@OPERATOR_REGISTRY.register()
class NormalizeMedicalTextOperator(OperatorABC):
    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "raw_content",
        output_key: str = "normalized_content",
    ) -> list[str]:
        dataframe = storage.read("dataframe")
        if input_key not in dataframe.columns:
            raise ValueError(f"Missing input column: {input_key}")
        dataframe[output_key] = dataframe[input_key].map(normalize_medical_text)
        storage.write(dataframe)
        return [output_key]


@OPERATOR_REGISTRY.register()
class ChunkMedicalTextOperator(OperatorABC):
    def __init__(self, chunk_size: int, chunk_overlap: int):
        super().__init__()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def run(
        self,
        storage: DataFlowStorage,
        input_key: str = "normalized_content",
        output_key: str = "content",
    ) -> list[str]:
        import hashlib

        dataframe = storage.read("dataframe")
        if input_key not in dataframe.columns:
            raise ValueError(f"Missing input column: {input_key}")

        rows: list[dict] = []
        seen: set[str] = set()
        for _, row in dataframe.iterrows():
            for chunk_index, content in enumerate(
                split_text(str(row[input_key]), self.chunk_size, self.chunk_overlap)
            ):
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                rows.append(
                    {
                        "chunk_id": f"chk_{digest[:24]}",
                        "document_id": row["document_id"],
                        "source_id": row["source_id"],
                        "source_version_id": row["source_version_id"],
                        "source_record_index": int(row["source_record_index"]),
                        "chunk_index": chunk_index,
                        output_key: content,
                        "content_sha256": digest,
                        "char_count": len(content),
                    }
                )
        storage.write(pd.DataFrame(rows))
        return [output_key]


class MedicalDocumentPipeline(PipelineABC):
    def __init__(
        self,
        input_file: Path,
        cache_dir: Path,
        file_prefix: str,
        chunk_size: int,
        chunk_overlap: int,
    ):
        super().__init__()
        self.storage = FileStorage(
            first_entry_file_name=str(input_file),
            cache_path=str(cache_dir),
            file_name_prefix=file_prefix,
            cache_type="jsonl",
        )
        self.normalize = NormalizeMedicalTextOperator()
        self.chunk = ChunkMedicalTextOperator(chunk_size, chunk_overlap)

    def forward(self):
        self.normalize.run(
            storage=self.storage.step(),
            input_key="raw_content",
            output_key="normalized_content",
        )
        self.chunk.run(
            storage=self.storage.step(),
            input_key="normalized_content",
            output_key="content",
        )
