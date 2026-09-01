"""Freeze and read content/evidence at the AssetVersion boundary."""
from copy import deepcopy
from datetime import timezone
from types import SimpleNamespace
import uuid

from sqlalchemy import select

from .models import KnowledgeAssetItem, KnowledgeEvidence, SourceVersion, Source


def freeze_asset_items(session, asset, items):
    session.flush()
    for item in items:
        evidence = []
        rows = session.execute(select(KnowledgeEvidence, SourceVersion, Source).join(
            SourceVersion, SourceVersion.id == KnowledgeEvidence.source_version_id,
        ).join(Source, Source.id == SourceVersion.source_id).where(
            KnowledgeEvidence.knowledge_item_id == item.id,
        ).order_by(KnowledgeEvidence.id)).all()
        for link, version, source in rows:
            evidence.append({
                "source_id": source.id, "source_name": source.name,
                "original_filename": version.original_filename, "relative_path": source.relative_path,
                "source_version_id": version.id, "source_version_no": version.version_no,
                "flow_chunk_id": link.flow_chunk_id,
                "flow_chunk_revision_id": link.flow_chunk_revision_id,
                "flow_chunk_review_snapshot_id": link.flow_chunk_review_snapshot_id,
                "source_anchor": link.source_anchor, "anchor": deepcopy(link.anchor_json),
                "evidence_text": link.evidence_text, "is_primary": link.is_primary,
            })
        if not evidence:
            raise ValueError("AssetVersion 条目缺少 Evidence")
        session.add(KnowledgeAssetItem(
            id=f"kai_{uuid.uuid4().hex}", asset_version_id=asset.id, knowledge_item_id=item.id,
            source_knowledge_id=item.source_knowledge_id, canonical_content=item.canonical_content,
            data_json=deepcopy(item.data_json), content_hash=item.content_hash, evidence_json=evidence,
            knowledge_review_json={
                "status": item.review_status,
                "revision": item.review_revision,
                "reviewed_by": item.reviewed_by,
                "reviewed_at": ((item.reviewed_at if item.reviewed_at.tzinfo else
                                  item.reviewed_at.replace(tzinfo=timezone.utc)).isoformat()
                                 if item.reviewed_at else None),
                "note": item.review_note,
            },
        ))


def vector_items(session, asset_id):
    # Adapt the immutable row to the existing vector writer without joining current knowledge.
    return [SimpleNamespace(id=row.knowledge_item_id, source_knowledge_id=row.source_knowledge_id,
                            canonical_content=row.canonical_content, data_json=deepcopy(row.data_json),
                            content_hash=row.content_hash)
            for row in session.scalars(select(KnowledgeAssetItem).where(
                KnowledgeAssetItem.asset_version_id == asset_id,
            ).order_by(KnowledgeAssetItem.source_knowledge_id))]
