"""Resolve the smallest document graph needed by selected formal knowledge."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from ..models import (
    DocumentLibrary,
    DocumentLibraryMember,
    KnowledgeItem,
    KnowledgeItemSource,
    Source,
    SourceChunk,
    SourceVersion,
)


@dataclass(frozen=True)
class DependencyScope:
    document_library_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_version_ids: tuple[str, ...]
    source_chunk_ids: tuple[str, ...]


def resolve_dependencies(session, library_ids: list[str], *, include_full_document_library: bool = False) -> DependencyScope:
    item_ids = list(session.scalars(select(KnowledgeItem.id).where(KnowledgeItem.knowledge_library_id.in_(library_ids))))
    links = list(session.scalars(select(KnowledgeItemSource).where(KnowledgeItemSource.knowledge_item_id.in_(item_ids)))) if item_ids else []
    version_ids = {link.source_version_id for link in links}
    versions = list(session.scalars(select(SourceVersion).where(SourceVersion.id.in_(version_ids)))) if version_ids else []
    source_ids = {version.source_id for version in versions}
    sources = list(session.scalars(select(Source).where(Source.id.in_(source_ids)))) if source_ids else []
    library_ids_set = {source.document_library_id for source in sources}

    if include_full_document_library and library_ids_set:
        member_source_ids = set(session.scalars(select(DocumentLibraryMember.source_id).where(
            DocumentLibraryMember.document_library_id.in_(library_ids_set)
        )))
        source_ids.update(member_source_ids)
        versions = list(session.scalars(select(SourceVersion).where(SourceVersion.source_id.in_(source_ids))))
        version_ids = {version.id for version in versions}

    chunks: list[SourceChunk] = []
    if version_ids:
        chunk_keys = {(link.source_version_id, link.source_chunk_id) for link in links if link.source_chunk_id}
        candidates = list(session.scalars(select(SourceChunk).where(SourceChunk.source_version_id.in_(version_ids))))
        chunks = candidates if include_full_document_library else [
            chunk for chunk in candidates if (chunk.source_version_id, chunk.source_chunk_id) in chunk_keys
        ]
    if library_ids_set:
        # Fail closed when a referenced container has disappeared.
        existing = set(session.scalars(select(DocumentLibrary.id).where(DocumentLibrary.id.in_(library_ids_set))))
        if existing != library_ids_set:
            raise ValueError("知识引用的文档库容器不存在")
    return DependencyScope(
        tuple(sorted(library_ids_set)), tuple(sorted(source_ids)), tuple(sorted(version_ids)),
        tuple(sorted(chunk.id for chunk in chunks)),
    )
