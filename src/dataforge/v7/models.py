"""V7 persistence model.

The model contains only V7 concepts.  In particular, it has no organisations,
candidate-confirmation batches, target-project bindings, or legacy knowledge-base
tables.  A knowledge library is the single current state for its knowledge.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class DocumentLibrary(Timestamped, Base):
    __tablename__ = "document_libraries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), default="local", nullable=False, index=True)
    origin_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    origin_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    origin_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class Source(Timestamped, Base):
    __tablename__ = "sources"
    # MySQL utf8mb4 permits at most 3,072 bytes in an InnoDB key.  Keep the
    # full path for browsing while using its fixed-width digest for identity.
    __table_args__ = (
        UniqueConstraint("document_library_id", "relative_path_hash", name="uq_library_relative_path"),
        Index("ix_library_directory_path", "document_library_id", "directory_path_hash"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_id: Mapped[str] = mapped_column(ForeignKey("document_libraries.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    relative_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    directory_path: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    directory_path_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="file", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False, index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SourceVersion(Timestamped, Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_no", name="uq_source_version_number"),
        UniqueConstraint("source_id", "sha256", name="uq_source_version_sha256"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_uri: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    activation_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    preparation_status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    current_review_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    active_chunk_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_chunk_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


@event.listens_for(SourceVersion, "before_update")
def _protect_source_version_content(_mapper, _connection, target: SourceVersion) -> None:
    """Keep content identity immutable while allowing lifecycle state transitions."""
    state = inspect(target)
    immutable = ("source_id", "version_no", "blob_uri", "sha256", "size_bytes", "media_type", "original_filename")
    changed = [name for name in immutable if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(f"SourceVersion 内容字段不可修改：{', '.join(changed)}")


class DocumentIR(Timestamped, Base):
    """A persisted parser result for a concrete source version and flow run."""
    __tablename__ = "document_irs"
    __table_args__ = (UniqueConstraint("source_version_id", "flow_run_id", name="uq_document_ir_version_run"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    parser_adapter: Mapped[str] = mapped_column(String(120), nullable=False)
    parser_profile: Mapped[str] = mapped_column(String(32), default="auto", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceChunk(Timestamped, Base):
    """A formal source chunk retained independently from execution artifacts."""
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("chunk_set_id", "source_chunk_id", name="uq_source_chunk_set_logical_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    chunk_set_id: Mapped[str] = mapped_column(ForeignKey("source_chunk_sets.id"), nullable=False, index=True)
    flow_run_id: Mapped[str | None] = mapped_column(ForeignKey("flow_runs.id"), nullable=True, index=True)
    origin_flow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="pending_review", nullable=False, index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceChunkRevision(Timestamped, Base):
    """Immutable human-review revision for one logical SourceChunk."""
    __tablename__ = "source_chunk_revisions"
    __table_args__ = (UniqueConstraint("source_chunk_id", "revision_no", name="uq_source_chunk_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_chunk_id: Mapped[str] = mapped_column(ForeignKey("source_chunks.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), default="prepared", nullable=False, index=True)
    parent_chunk_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), default="system", nullable=False)


class SourceReviewSnapshot(Timestamped, Base):
    """Immutable, ordered approval boundary for one SourceVersion."""
    __tablename__ = "source_review_snapshots"
    __table_args__ = (
        UniqueConstraint("source_version_id", "review_no", name="uq_source_review_number"),
        UniqueConstraint("chunk_set_id", "content_digest", name="uq_source_review_chunk_set_digest"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    chunk_set_id: Mapped[str] = mapped_column(ForeignKey("source_chunk_sets.id"), nullable=False, index=True)
    review_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="approved", nullable=False, index=True)


class SourceReviewSnapshotChunk(Timestamped, Base):
    __tablename__ = "source_review_snapshot_chunks"
    __table_args__ = (
        UniqueConstraint("source_review_snapshot_id", "ordinal", name="uq_source_review_chunk_ordinal"),
        UniqueConstraint("source_review_snapshot_id", "source_chunk_revision_id", name="uq_source_review_chunk_revision"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_review_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=False, index=True
    )
    source_chunk_id: Mapped[str] = mapped_column(ForeignKey("source_chunks.id"), nullable=False, index=True)
    source_chunk_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_chunk_revisions.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SourcePreparationJob(Timestamped, Base):
    __tablename__ = "source_preparation_jobs"
    __table_args__ = (UniqueConstraint("source_version_id", "preparation_revision", name="uq_source_preparation_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    preparation_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    execution_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SourceChunkSet(Timestamped, Base):
    """One immutable preparation result that can be reviewed and promoted."""
    __tablename__ = "source_chunk_sets"
    __table_args__ = (
        UniqueConstraint("source_version_id", "preparation_revision", name="uq_source_chunk_set_preparation"),
        UniqueConstraint("source_preparation_job_id", name="uq_source_chunk_set_job"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    source_preparation_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_preparation_jobs.id"), nullable=True, index=True
    )
    flow_run_id: Mapped[str | None] = mapped_column(ForeignKey("flow_runs.id"), nullable=True, index=True)
    execution_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    preparation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="candidate", nullable=False, index=True)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeDispatch(Timestamped, Base):
    __tablename__ = "knowledge_dispatches"
    __table_args__ = (UniqueConstraint(
        "source_review_snapshot_id", "activation_no", name="uq_knowledge_dispatch_snapshot_activation"
    ),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_review_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=False, index=True
    )
    activation_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentLibraryMember(Timestamped, Base):
    __tablename__ = "document_library_members"
    __table_args__ = (UniqueConstraint("document_library_id", "source_id", name="uq_library_source"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_id: Mapped[str] = mapped_column(ForeignKey("document_libraries.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)


class DocumentLibraryTemplateBinding(Timestamped, Base):
    """An active document-library subscription to a published knowledge template."""
    __tablename__ = "document_library_template_bindings"
    __table_args__ = (UniqueConstraint("document_library_id", "knowledge_flow_template_id", name="uq_doc_library_template"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_id: Mapped[str] = mapped_column(ForeignKey("document_libraries.id"), nullable=False, index=True)
    knowledge_flow_template_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_templates.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    last_successful_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DocumentLibraryTemplateOutput(Timestamped, Base):
    """Stable result-library mapping for one binding and output type."""
    __tablename__ = "document_library_template_outputs"
    __table_args__ = (UniqueConstraint("document_library_template_binding_id", "output_key", name="uq_doc_binding_output_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_template_binding_id: Mapped[str] = mapped_column(ForeignKey("document_library_template_bindings.id"), nullable=False, index=True)
    knowledge_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    output_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    graph_mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)


class DocumentLibraryProcessingRecord(Timestamped, Base):
    """Successful processing is recorded per binding, source version and template revision."""
    __tablename__ = "document_library_processing_records"
    __table_args__ = (UniqueConstraint(
        "document_library_template_binding_id", "source_version_id", "knowledge_flow_template_revision_id",
        "source_review_snapshot_id", "activation_no", name="uq_doc_processing_review_activation",
    ),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_template_binding_id: Mapped[str] = mapped_column(ForeignKey("document_library_template_bindings.id"), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    knowledge_flow_template_revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_template_revisions.id"), nullable=False, index=True)
    source_review_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=True, index=True
    )
    activation_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)


class DocumentLibraryProcessingBaseline(Timestamped, Base):
    """Read-only processing success imported without fabricating a local Job/Run."""
    __tablename__ = "document_library_processing_baselines"
    __table_args__ = (
        UniqueConstraint(
            "document_library_template_binding_id", "source_version_id",
            "knowledge_flow_template_revision_id", name="uq_doc_processing_baseline",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_template_binding_id: Mapped[str] = mapped_column(
        ForeignKey("document_library_template_bindings.id"), nullable=False, index=True
    )
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    knowledge_flow_template_revision_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_flow_template_revisions.id"), nullable=False, index=True
    )
    origin_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    imported_release_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_success_status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)


class KnowledgeType(Timestamped, Base):
    __tablename__ = "knowledge_types"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="builtin", nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class KnowledgeFlowTemplate(Timestamped, Base):
    __tablename__ = "knowledge_flow_templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="knowledge", nullable=False, index=True)
    needs_review_upgrade: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # 编辑形态：standard（标准配置，definition_json 存 stage config）| advanced（高级编排，definition_json 存 Flow DSL）
    authoring_mode: Mapped[str] = mapped_column(String(32), default="advanced", nullable=False)
    # 标准配置绑定的托管模板 code（如 standard-qa），advanced 模式下为 None
    managed_template_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Informational authoring provenance only.  Keep these as stable IDs rather
    # than FKs to avoid a template↔revision deletion cycle during controlled rebuilds.
    derived_from_template_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    derived_from_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class KnowledgeFlowTemplateRevision(Timestamped, Base):
    __tablename__ = "knowledge_flow_template_revisions"
    __table_args__ = (UniqueConstraint("knowledge_flow_template_id", "revision_no", name="uq_flow_template_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_flow_template_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_templates.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), default="knowledge", nullable=False, index=True)
    authoring_mode: Mapped[str] = mapped_column(String(32), default="advanced", nullable=False)
    managed_template_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class KnowledgeLibrary(Timestamped, Base):
    __tablename__ = "knowledge_libraries"
    __table_args__ = (UniqueConstraint("code", name="uq_knowledge_library_code"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    knowledge_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    graph_mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    knowledge_type_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    embedding_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partition_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    graph_schema_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    graph_schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_template_revision_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_flow_template_revisions.id"), nullable=True)
    origin_type: Mapped[str] = mapped_column(String(32), default="local", nullable=False, index=True)
    origin_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    origin_asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    origin_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    migration_status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False, index=True)


class KnowledgeItem(Timestamped, Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (UniqueConstraint("knowledge_library_id", "source_knowledge_id", name="uq_library_source_knowledge"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    knowledge_type_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_knowledge_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_content: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class KnowledgeItemSource(Timestamped, Base):
    __tablename__ = "knowledge_item_sources"
    __table_args__ = (UniqueConstraint("knowledge_item_id", "source_version_id", "source_chunk_id", name="uq_item_source_version_chunk"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    source_chunk_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_chunk_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_chunk_revisions.id"), nullable=True, index=True
    )
    source_review_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=True, index=True
    )
    source_anchor: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class KnowledgeChange(Timestamped, Base):
    __tablename__ = "knowledge_changes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    knowledge_item_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_items.id"), nullable=True)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    before_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class KnowledgeJob(Timestamped, Base):
    __tablename__ = "knowledge_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_flow_template_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_templates.id"), nullable=False)
    knowledge_flow_template_revision_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_flow_template_revisions.id"), nullable=True)
    source_version_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output_library_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sink_library_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    execution_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    document_library_template_binding_id: Mapped[str | None] = mapped_column(ForeignKey("document_library_template_bindings.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeJobReviewInput(Timestamped, Base):
    __tablename__ = "knowledge_job_review_inputs"
    __table_args__ = (UniqueConstraint("knowledge_job_id", "source_version_id", name="uq_job_review_source_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    source_review_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=False, index=True
    )
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    activation_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class KnowledgeLibraryWorkLease(Timestamped, Base):
    """Cross-process writer lease shared by knowledge and vector work."""
    __tablename__ = "knowledge_library_work_leases"
    knowledge_library_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_libraries.id"), primary_key=True
    )
    work_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    work_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lease_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ComponentHeartbeat(Timestamped, Base):
    """Latest liveness signal for one Worker or Runner process."""
    __tablename__ = "component_heartbeats"
    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ComponentCheckRun(Timestamped, Base):
    __tablename__ = "component_check_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    selected_components: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ComponentCheckResult(Timestamped, Base):
    __tablename__ = "component_check_results"
    __table_args__ = (UniqueConstraint("check_run_id", "component", name="uq_component_check_run_item"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    check_run_id: Mapped[str] = mapped_column(ForeignKey("component_check_runs.id"), nullable=False, index=True)
    component: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunkGeneration(Timestamped, Base):
    """Latest generation result for one output type and formal source chunk.

    This is deliberately separate from execution Artifacts.  It is the durable
    retry and partial-publication boundary: an LLM failure must not make the
    successful neighbouring chunks lose their published knowledge.
    """
    __tablename__ = "knowledge_chunk_generations"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_job_id", "knowledge_type", "source_version_id", "source_chunk_id",
            name="uq_job_type_source_chunk_generation",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)
    knowledge_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    source_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class KnowledgeTypeRevision(Timestamped, Base):
    __tablename__ = "knowledge_type_revisions"
    __table_args__ = (UniqueConstraint("knowledge_type_id", "revision_no", name="uq_type_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_type_id: Mapped[str] = mapped_column(ForeignKey("knowledge_types.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    canonical_field: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_policy: Mapped[str] = mapped_column(String(32), default="single", nullable=False)
    quality_profile_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeTypeModeRevision(Timestamped, Base):
    """A mode-specific contract below a top-level knowledge family revision."""
    __tablename__ = "knowledge_type_mode_revisions"
    __table_args__ = (
        UniqueConstraint("knowledge_type_revision_id", "mode", "revision_no", name="uq_type_mode_revision"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_type_revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_type_revisions.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    canonical_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    identity_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_policy: Mapped[str] = mapped_column(String(32), default="multiple", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="published", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeTypeIndexBinding(Timestamped, Base):
    __tablename__ = "knowledge_type_index_bindings"
    __table_args__ = (UniqueConstraint("knowledge_type_revision_id", "index_profile_id", name="uq_type_revision_index"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_type_revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_type_revisions.id"), nullable=False, index=True)
    index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False, index=True)
    index_profile_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    field_path: Mapped[str] = mapped_column(String(255), default="canonical_content", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="primary", nullable=False)


class OperatorDefinition(Timestamped, Base):
    __tablename__ = "operator_definitions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name_zh: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scenarios: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    knowledge_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_predecessors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_successors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="published", nullable=False, index=True)
    exposure: Mapped[str] = mapped_column(String(32), default="canvas", nullable=False)
    surfaces: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class OperatorVersion(Timestamped, Base):
    __tablename__ = "operator_versions"
    __table_args__ = (UniqueConstraint("operator_definition_id", "version_no", name="uq_operator_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_definition_id: Mapped[str] = mapped_column(ForeignKey("operator_definitions.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_code: Mapped[str] = mapped_column(String(120), nullable=False)
    input_ports: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_ports: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input_example: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_example: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    parameter_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    parameter_docs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    runtime_requirements: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperatorValidationRun(Timestamped, Base):
    __tablename__ = "operator_validation_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_version_id: Mapped[str] = mapped_column(ForeignKey("operator_versions.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class PromptTemplate(Timestamped, Base):
    __tablename__ = "prompt_templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class PromptTemplateRevision(Timestamped, Base):
    __tablename__ = "prompt_template_revisions"
    __table_args__ = (UniqueConstraint("prompt_template_id", "revision_no", name="uq_prompt_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_template_id: Mapped[str] = mapped_column(ForeignKey("prompt_templates.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    knowledge_types: Mapped[list] = mapped_column(JSON, default=lambda: ["*"], nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QualityProfile(Timestamped, Base):
    __tablename__ = "quality_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class QualityProfileRevision(Timestamped, Base):
    __tablename__ = "quality_profile_revisions"
    __table_args__ = (UniqueConstraint("quality_profile_id", "revision_no", name="uq_quality_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    quality_profile_id: Mapped[str] = mapped_column(ForeignKey("quality_profiles.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    knowledge_types: Mapped[list] = mapped_column(JSON, default=lambda: ["*"], nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FlowSubgraph(Timestamped, Base):
    __tablename__ = "flow_subgraphs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class FlowSubgraphRevision(Timestamped, Base):
    __tablename__ = "flow_subgraph_revisions"
    __table_args__ = (UniqueConstraint("flow_subgraph_id", "revision_no", name="uq_subgraph_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_subgraph_id: Mapped[str] = mapped_column(ForeignKey("flow_subgraphs.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    input_contract: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_contract: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FlowExecutionSnapshot(Timestamped, Base):
    __tablename__ = "flow_execution_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_flow_template_revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_template_revisions.id"), nullable=False, index=True)
    compiled_definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dependency_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="published", nullable=False)


@event.listens_for(KnowledgeFlowTemplateRevision, "before_update")
def _protect_published_flow_revision(mapper, connection, target):
    fields = ("id", "knowledge_flow_template_id", "revision_no", "definition_json", "status", "published_at",
              "execution_snapshot_id", "purpose", "authoring_mode", "managed_template_code")
    previous = connection.execute(select(KnowledgeFlowTemplateRevision.__table__).where(
        KnowledgeFlowTemplateRevision.id == inspect(target).identity[0])).mappings().one()
    if previous["status"] == "published" and any(previous[key] != getattr(target, key) for key in fields):
        # SQLite returns naive datetimes even for timezone=True; unchanged fields
        # must not be treated as edits just because of that representation.
        if any(inspect(target).attrs[key].history.has_changes() for key in fields):
            raise ValueError("PUBLISHED_REVISION_IMMUTABLE")
    if previous["execution_snapshot_id"] and previous["execution_snapshot_id"] != target.execution_snapshot_id:
        raise ValueError("REVISION_SNAPSHOT_ALREADY_BOUND")
    if target.status == "published" and previous["status"] != "published":
        snapshot = connection.execute(select(FlowExecutionSnapshot.__table__).where(
            FlowExecutionSnapshot.id == target.execution_snapshot_id)).mappings().first()
        if (not snapshot or snapshot["status"] != "published"
                or snapshot["knowledge_flow_template_revision_id"] != target.id or not target.published_at):
            raise ValueError("PUBLISHED_REVISION_REQUIRES_SNAPSHOT")


@event.listens_for(FlowExecutionSnapshot, "before_update")
def _protect_execution_snapshot(mapper, connection, target):
    fields = ("id", "knowledge_flow_template_revision_id", "compiled_definition_json", "dependency_json", "checksum", "status")
    if any(inspect(target).attrs[key].history.has_changes() for key in fields):
        raise ValueError("EXECUTION_SNAPSHOT_IMMUTABLE")


@event.listens_for(FlowExecutionSnapshot, "before_delete")
@event.listens_for(KnowledgeFlowTemplateRevision, "before_delete")
def _protect_flow_publication_delete(mapper, connection, target):
    if isinstance(target, FlowExecutionSnapshot) or target.status == "published":
        raise ValueError("FLOW_PUBLICATION_IMMUTABLE")


@event.listens_for(Session, "do_orm_execute")
def _prevent_bulk_flow_publication_writes(state):
    # Bulk ORM/Core statements bypass mapper before_update/before_delete.
    # Flow writes must go through the checked instance lifecycle above.
    if state.is_update or state.is_delete:
        table = getattr(state.statement, "table", None)
        if getattr(table, "name", None) in {"knowledge_flow_template_revisions", "flow_execution_snapshots"}:
            raise ValueError("FLOW_PUBLICATION_BULK_WRITE_FORBIDDEN")


class DebugRunInputSnapshot(Timestamped, Base):
    """Immutable authoring, input and preview-binding boundary for Debug Runs."""
    __tablename__ = "debug_run_input_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_flow_template_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_flow_templates.id"), nullable=False, index=True
    )
    knowledge_flow_template_revision_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_flow_template_revisions.id"), nullable=False, index=True
    )
    execution_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("flow_execution_snapshots.id"), nullable=False, index=True
    )
    authoring_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    source_definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_definition_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    output_types_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reusable_node_map_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sink_library_bindings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input_source: Mapped[str] = mapped_column(String(32), default="source_review_snapshot", nullable=False, index=True)
    input_descriptor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resolved_chunks_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    sink_preview_targets_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), default="admin", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class DebugRunReviewInput(Timestamped, Base):
    __tablename__ = "debug_run_review_inputs"
    __table_args__ = (
        UniqueConstraint("debug_input_snapshot_id", "source_version_id", name="uq_debug_review_source_version"),
        UniqueConstraint("debug_input_snapshot_id", "ordinal", name="uq_debug_review_ordinal"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    debug_input_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("debug_run_input_snapshots.id"), nullable=False, index=True
    )
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    source_review_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("source_review_snapshots.id"), nullable=False, index=True
    )
    activation_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class DebugRunFlowMaterialization(Timestamped, Base):
    """Idempotent audit boundary for applying or saving a Debug configuration."""
    __tablename__ = "debug_run_flow_materializations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    target_template_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FlowRun(Timestamped, Base):
    __tablename__ = "flow_runs"
    __table_args__ = (
        UniqueConstraint("parent_flow_run_id", "idempotency_key", name="uq_derived_run_request"),
        CheckConstraint(
            "(knowledge_job_id IS NOT NULL AND source_preparation_job_id IS NULL AND debug_input_snapshot_id IS NULL) OR "
            "(knowledge_job_id IS NULL AND source_preparation_job_id IS NOT NULL AND debug_input_snapshot_id IS NULL) OR "
            "(knowledge_job_id IS NULL AND source_preparation_job_id IS NULL AND debug_input_snapshot_id IS NOT NULL)",
            name="ck_flow_run_single_owner",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_job_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=True, index=True)
    source_preparation_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_preparation_jobs.id"), nullable=True, index=True
    )
    debug_input_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("debug_run_input_snapshots.id"), nullable=True, index=True
    )
    execution_snapshot_id: Mapped[str] = mapped_column(ForeignKey("flow_execution_snapshots.id"), nullable=False, index=True)
    parent_flow_run_id: Mapped[str | None] = mapped_column(ForeignKey("flow_runs.id"), nullable=True, index=True)
    run_mode: Mapped[str] = mapped_column(String(32), default="full", nullable=False, index=True)
    start_node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parameter_overrides: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sink_policy: Mapped[str] = mapped_column(String(32), default="commit", nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FlowNodeRun(Timestamped, Base):
    __tablename__ = "flow_node_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    operator_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    logs_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    input_artifact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output_artifact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Artifact(Timestamped, Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_flow_run_created_at_id", "flow_run_id", "created_at", "id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    flow_node_run_id: Mapped[str | None] = mapped_column(ForeignKey("flow_node_runs.id"), nullable=True, index=True)
    source_version_id: Mapped[str | None] = mapped_column(ForeignKey("source_versions.id"), nullable=True, index=True)
    type_code: Mapped[str] = mapped_column(String(120), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_format: Mapped[str] = mapped_column(String(32), default="json", nullable=False)
    replayable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class FlowNodeArtifactBinding(Timestamped, Base):
    __tablename__ = "flow_node_artifact_bindings"
    __table_args__ = (UniqueConstraint("flow_node_run_id", "direction", "port_name", "ordinal", name="uq_node_artifact_port"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_node_run_id: Mapped[str] = mapped_column(ForeignKey("flow_node_runs.id"), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    port_name: Mapped[str] = mapped_column(String(120), default="input", nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FlowRunEvent(Base):
    __tablename__ = "flow_run_events"
    __table_args__ = (UniqueConstraint("flow_run_id", "sequence_no", name="uq_flow_run_event_sequence"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class FlowRunSinkPreview(Timestamped, Base):
    __tablename__ = "flow_run_sink_previews"
    __table_args__ = (UniqueConstraint("flow_run_id", "output_key", name="uq_flow_run_sink_preview"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    output_key: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_library_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=True, index=True)
    baseline_kind: Mapped[str] = mapped_column(String(32), default="knowledge_library", nullable=False, index=True)
    candidates_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    successful_chunks_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    diff_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quality_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    base_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactLineage(Timestamped, Base):
    __tablename__ = "artifact_lineage"
    __table_args__ = (UniqueConstraint("parent_artifact_id", "child_artifact_id", name="uq_artifact_lineage"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)
    child_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)


class ModelServing(Timestamped, Base):
    __tablename__ = "model_servings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    serving_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    serving_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credential_key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=16384, nullable=False)
    disable_thinking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    last_check_status: Mapped[str] = mapped_column(String(64), default="pending_configuration", nullable=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmbeddingServing(Timestamped, Base):
    __tablename__ = "embedding_servings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    serving_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credential_key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    last_check_status: Mapped[str] = mapped_column(String(64), default="pending_configuration", nullable=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_observed_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RerankerServing(Timestamped, Base):
    __tablename__ = "reranker_servings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    serving_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credential_key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_batch_size: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    last_check_status: Mapped[str] = mapped_column(String(64), default="not_checked", nullable=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmbeddingProfile(Timestamped, Base):
    __tablename__ = "embedding_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class KnowledgeIndexProfile(Timestamped, Base):
    __tablename__ = "knowledge_index_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    knowledge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"), nullable=False)
    embedding_serving_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    embedding_input: Mapped[str] = mapped_column(String(32), default="canonical_content", nullable=False)
    fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="manual", nullable=False, index=True)
    owner_knowledge_type_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_types.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class KnowledgeIndexProfileRevision(Timestamped, Base):
    """Published index settings are immutable snapshots selected by type revisions."""
    __tablename__ = "knowledge_index_profile_revisions"
    __table_args__ = (UniqueConstraint("knowledge_index_profile_id", "revision_no", name="uq_index_profile_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"), nullable=False)
    embedding_serving_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    embedding_input: Mapped[str] = mapped_column(String(32), default="canonical_content", nullable=False)
    fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    storage_contract_revision_id: Mapped[str | None] = mapped_column(ForeignKey("storage_contract_revisions.id"), nullable=True, index=True)
    collection_policy: Mapped[str] = mapped_column(String(32), default="external", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StorageContract(Timestamped, Base):
    __tablename__ = "storage_contracts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class StorageContractRevision(Timestamped, Base):
    __tablename__ = "storage_contract_revisions"
    __table_args__ = (
        UniqueConstraint("storage_contract_id", "revision_no", name="uq_storage_contract_revision"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_contract_id: Mapped[str] = mapped_column(ForeignKey("storage_contracts.id"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    embedding_profile_id: Mapped[str] = mapped_column(ForeignKey("embedding_profiles.id"), nullable=False)
    vector_type: Mapped[str] = mapped_column(String(32), default="FLOAT_VECTOR", nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False)
    index_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    storage_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="published", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ManagedCollection(Timestamped, Base):
    __tablename__ = "managed_collections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_contract_revision_id: Mapped[str] = mapped_column(ForeignKey("storage_contract_revisions.id"), nullable=False, index=True)
    collection_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provisioning_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    desired_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False, index=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class ManagedCollectionDeletionJob(Timestamped, Base):
    """Explicit, retryable deletion of one verified DataForge-owned Collection."""
    __tablename__ = "managed_collection_deletion_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    managed_collection_id: Mapped[str] = mapped_column(ForeignKey("managed_collections.id"), nullable=False, index=True)
    preflight_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class VectorSyncJob(Timestamped, Base):
    __tablename__ = "vector_sync_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    synced_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_asset_versions.id"), nullable=True, index=True
    )


class KnowledgeAssetVersion(Timestamped, Base):
    """Immutable physical vector snapshot for one logical knowledge library."""
    __tablename__ = "knowledge_asset_versions"
    __table_args__ = (
        UniqueConstraint("knowledge_library_id", "version_no", name="uq_library_asset_version"),
        UniqueConstraint("collection_name", "partition_name", name="uq_asset_physical_partition"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_libraries.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    index_profile_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_index_profiles.id"), nullable=False, index=True
    )
    index_profile_revision_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_index_profile_revisions.id"), nullable=False, index=True
    )
    storage_contract_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("storage_contract_revisions.id"), nullable=True, index=True
    )
    embedding_serving_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    partition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="building", nullable=False, index=True)
    item_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_snapshot_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    review_gate_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    source_release_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_migration_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unreferenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_observed_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeAssetItem(Timestamped, Base):
    """Immutable content and evidence used to build one physical asset."""
    __tablename__ = "knowledge_asset_items"
    __table_args__ = (UniqueConstraint("asset_version_id", "source_knowledge_id", name="uq_asset_item_source"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_asset_versions.id"), nullable=False, index=True)
    knowledge_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_knowledge_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_content: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class VectorRecordState(Timestamped, Base):
    __tablename__ = "vector_record_states"
    __table_args__ = (UniqueConstraint("knowledge_item_id", "index_profile_id", name="uq_vector_item_profile"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"), nullable=False, index=True)
    index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False)
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class VectorDeletionJob(Timestamped, Base):
    """Retryable deletion of vector records scoped to one library partition."""
    __tablename__ = "vector_deletion_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False, index=True)
    vector_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeLibraryDeletionJob(Timestamped, Base):
    __tablename__ = "knowledge_library_deletion_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentDeletionJob(Timestamped, Base):
    """Asynchronous physical deletion of V7 document-library material only."""
    __tablename__ = "document_deletion_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    document_library_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    object_keys: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    blob_uris: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Project(Timestamped, Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class ProjectTask(Timestamped, Base):
    __tablename__ = "project_tasks"
    __table_args__ = (UniqueConstraint("project_id", "code", name="uq_project_task_code"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    knowledge_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class MilvusTarget(Timestamped, Base):
    __tablename__ = "milvus_targets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    milvus_url: Mapped[str] = mapped_column(String(1024), nullable=False)


class Deployment(Timestamped, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("code", name="uq_deployment_code"),
        UniqueConstraint("institution_code", name="uq_deployment_institution_code"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), default="institution", nullable=False, index=True)
    institution_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    institution_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Legacy compatibility field. New routing operations receive release_stage explicitly.
    release_stage: Mapped[str] = mapped_column(String(16), default="test", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    institution_code_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeploymentTarget(Timestamped, Base):
    __tablename__ = "deployment_targets"
    __table_args__ = (
        UniqueConstraint("deployment_id", "release_stage", "target_kind", name="uq_deployment_stage_target"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False, index=True)
    release_stage: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    target_kind: Mapped[str] = mapped_column(String(32), default="milvus", nullable=False)
    milvus_target_id: Mapped[str] = mapped_column(ForeignKey("milvus_targets.id"), nullable=False, index=True)


class ProjectDeployment(Timestamped, Base):
    __tablename__ = "project_deployments"
    __table_args__ = (
        UniqueConstraint("project_id", "deployment_id", name="uq_project_deployment_binding"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)


class DataForgeInstance(Base):
    __tablename__ = "dataforge_instances"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    instance_mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    bound_deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployments.id"), nullable=True, unique=True)
    source_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ProjectDeploymentTask(Timestamped, Base):
    __tablename__ = "project_deployment_tasks"
    __table_args__ = (UniqueConstraint("project_deployment_id", "project_task_id", name="uq_deployment_project_task"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_deployment_id: Mapped[str] = mapped_column(ForeignKey("project_deployments.id"), nullable=False, index=True)
    project_task_id: Mapped[str] = mapped_column(ForeignKey("project_tasks.id"), nullable=False, index=True)
    index_profile_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=True, index=True)
    qa_embedding_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    final_top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    reranker_serving_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class ProjectOrgRoute(Timestamped, Base):
    __tablename__ = "project_org_routes"
    __table_args__ = (UniqueConstraint("project_deployment_task_id", "org_code", name="uq_deploy_task_org_route"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_deployment_task_id: Mapped[str] = mapped_column(ForeignKey("project_deployment_tasks.id"), nullable=False, index=True)
    org_code: Mapped[str] = mapped_column(String(120), nullable=False)
    org_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class ProjectOrgRouteLibrary(Timestamped, Base):
    __tablename__ = "project_org_route_libraries"
    __table_args__ = (UniqueConstraint("project_org_route_id", "knowledge_library_id", name="uq_route_library"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_org_route_id: Mapped[str] = mapped_column(ForeignKey("project_org_routes.id"), nullable=False, index=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class ProjectRouteVersion(Base):
    __tablename__ = "project_route_versions"
    __table_args__ = (
        UniqueConstraint("project_deployment_id", "release_stage", "version_no", name="uq_deploy_stage_route_version"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    project_deployment_id: Mapped[str] = mapped_column(ForeignKey("project_deployments.id"), nullable=False, index=True)
    release_stage: Mapped[str] = mapped_column(String(16), default="test", nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="central", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectRouteVersionAsset(Base):
    """Immutable knowledge-library to physical asset mapping for a route version."""
    __tablename__ = "project_route_version_assets"
    __table_args__ = (
        UniqueConstraint("project_route_version_id", "knowledge_library_id", name="uq_route_version_library_asset"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_route_version_id: Mapped[str] = mapped_column(
        ForeignKey("project_route_versions.id"), nullable=False, index=True
    )
    knowledge_library_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_asset_version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_asset_versions.id"), nullable=False, index=True
    )
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    partition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class InstitutionReleaseDraft(Timestamped, Base):
    __tablename__ = "institution_release_drafts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False, index=True)
    package_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    base_release_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    selection_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    milvus_override_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    milvus_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class InstitutionReleaseDraftProject(Timestamped, Base):
    __tablename__ = "institution_release_draft_projects"
    __table_args__ = (
        UniqueConstraint("institution_release_draft_id", "project_deployment_id", name="uq_release_draft_project"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    institution_release_draft_id: Mapped[str] = mapped_column(
        ForeignKey("institution_release_drafts.id"), nullable=False, index=True
    )
    project_deployment_id: Mapped[str] = mapped_column(ForeignKey("project_deployments.id"), nullable=False, index=True)
    project_route_version_id: Mapped[str] = mapped_column(ForeignKey("project_route_versions.id"), nullable=False, index=True)


class InstitutionReleaseSnapshot(Timestamped, Base):
    __tablename__ = "institution_release_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    institution_release_draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("institution_release_drafts.id"), nullable=True, index=True
    )
    target_deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id"), nullable=False, index=True)
    package_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_release_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    diff_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tombstones_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="frozen", nullable=False, index=True)


class LocalMilvusConfiguration(Timestamped, Base):
    __tablename__ = "local_milvus_configurations"
    __table_args__ = (UniqueConstraint("dataforge_instance_id", "slot", name="uq_local_milvus_slot"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataforge_instance_id: Mapped[str] = mapped_column(ForeignKey("dataforge_instances.id"), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), default="default", nullable=False)
    tls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_verification", nullable=False, index=True)
    verified_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportedRouteCandidate(Timestamped, Base):
    __tablename__ = "imported_route_candidates"
    __table_args__ = (
        UniqueConstraint("migration_job_id", "project_deployment_id", name="uq_import_route_candidate"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    migration_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_migration_jobs.id"), nullable=False, index=True)
    project_deployment_id: Mapped[str] = mapped_column(ForeignKey("project_deployments.id"), nullable=False, index=True)
    source_route_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_route_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="waiting_assets", nullable=False, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    readiness_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    activated_route_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_route_versions.id"), nullable=True, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeAssetGcJob(Timestamped, Base):
    __tablename__ = "knowledge_asset_gc_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False, index=True)
    execute_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeMigrationJob(Timestamped, Base):
    __tablename__ = "knowledge_migration_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    package_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    package_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    project_deployment_id: Mapped[str | None] = mapped_column(ForeignKey("project_deployments.id"), nullable=True, index=True)
    target_deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployments.id"), nullable=True, index=True)
    release_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("institution_release_snapshots.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="planning", nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), default="planning", nullable=False)
    checkpoint_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    conflict_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    signature_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    package_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    package_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeMigrationItem(Timestamped, Base):
    __tablename__ = "knowledge_migration_items"
    __table_args__ = (UniqueConstraint("migration_job_id", "knowledge_library_id", "collection_name", name="uq_migration_library_collection"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    migration_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_migration_jobs.id"), nullable=False, index=True)
    knowledge_library_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    partition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    target_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False, index=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
