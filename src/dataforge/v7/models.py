"""V7 persistence model.

The model contains only V7 concepts.  In particular, it has no organisations,
candidate-confirmation batches, target-project bindings, or legacy knowledge-base
tables.  A knowledge library is the single current state for its knowledge.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
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
    __table_args__ = (UniqueConstraint("source_id", "version_no", name="uq_source_version_number"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    __table_args__ = (UniqueConstraint("source_version_id", "flow_run_id", "chunk_index", name="uq_source_chunk_version_run_index"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    source_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


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
    __table_args__ = (UniqueConstraint("document_library_template_binding_id", "source_version_id", "knowledge_flow_template_revision_id", name="uq_doc_processing_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_library_template_binding_id: Mapped[str] = mapped_column(ForeignKey("document_library_template_bindings.id"), nullable=False, index=True)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("source_versions.id"), nullable=False, index=True)
    knowledge_flow_template_revision_id: Mapped[str] = mapped_column(ForeignKey("knowledge_flow_template_revisions.id"), nullable=False, index=True)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)


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
    output_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


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
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    exposure: Mapped[str] = mapped_column(String(32), default="canvas", nullable=False)
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
    parameter_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    runtime_requirements: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class FlowRun(Timestamped, Base):
    __tablename__ = "flow_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, index=True)
    execution_snapshot_id: Mapped[str] = mapped_column(ForeignKey("flow_execution_snapshots.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FlowNodeRun(Timestamped, Base):
    __tablename__ = "flow_node_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    input_artifact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output_artifact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Artifact(Timestamped, Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_run_id: Mapped[str] = mapped_column(ForeignKey("flow_runs.id"), nullable=False, index=True)
    flow_node_run_id: Mapped[str | None] = mapped_column(ForeignKey("flow_node_runs.id"), nullable=True, index=True)
    type_code: Mapped[str] = mapped_column(String(120), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ArtifactLineage(Timestamped, Base):
    __tablename__ = "artifact_lineage"
    __table_args__ = (UniqueConstraint("parent_artifact_id", "child_artifact_id", name="uq_artifact_lineage"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)
    child_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False, index=True)


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
    fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
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


class VectorSyncJob(Timestamped, Base):
    __tablename__ = "vector_sync_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)
    index_profile_id: Mapped[str] = mapped_column(ForeignKey("knowledge_index_profiles.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    synced_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class ProjectOrgRoute(Timestamped, Base):
    __tablename__ = "project_org_routes"
    __table_args__ = (UniqueConstraint("project_task_id", "org_code", name="uq_task_org_route"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_task_id: Mapped[str] = mapped_column(ForeignKey("project_tasks.id"), nullable=False, index=True)
    org_code: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class ProjectOrgRouteLibrary(Timestamped, Base):
    __tablename__ = "project_org_route_libraries"
    __table_args__ = (UniqueConstraint("project_org_route_id", "knowledge_library_id", name="uq_route_library"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_org_route_id: Mapped[str] = mapped_column(ForeignKey("project_org_routes.id"), nullable=False, index=True)
    knowledge_library_id: Mapped[str] = mapped_column(ForeignKey("knowledge_libraries.id"), nullable=False, index=True)


class ProjectRouteVersion(Base):
    __tablename__ = "project_route_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_no", name="uq_project_route_version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
