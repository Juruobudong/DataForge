"""add governed SourceChunkSet lifecycle.

Revision ID: 20260825_source_chunk_sets
Revises: 20260824_chunk_review_gate
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260825_source_chunk_sets"
down_revision = "20260824_chunk_review_gate"
branch_labels = None
depends_on = None


def _index(table: str, name: str, columns: list[str]) -> None:
    if name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(name, table, columns)


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints(table) if item.get("name")}


def _foreign_keys(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_foreign_keys(table) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    if "source_chunk_sets" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "source_chunk_sets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_version_id", sa.String(64), sa.ForeignKey("source_versions.id"), nullable=False),
        sa.Column("source_preparation_job_id", sa.String(64), sa.ForeignKey("source_preparation_jobs.id"), nullable=True),
        sa.Column("flow_run_id", sa.String(64), sa.ForeignKey("flow_runs.id"), nullable=True),
        sa.Column("execution_snapshot_id", sa.String(64), nullable=True),
        sa.Column("preparation_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("content_digest", sa.String(64), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_version_id", "preparation_revision", name="uq_source_chunk_set_preparation"),
            sa.UniqueConstraint("source_preparation_job_id", name="uq_source_chunk_set_job"),
        )
    for column in (
        "source_version_id", "source_preparation_job_id", "flow_run_id", "execution_snapshot_id",
        "status", "content_digest",
    ):
        _index("source_chunk_sets", f"ix_source_chunk_sets_{column}", [column])

    if "active_chunk_set_id" not in _columns("source_versions"):
        op.add_column("source_versions", sa.Column("active_chunk_set_id", sa.String(64), nullable=True))
    if "candidate_chunk_set_id" not in _columns("source_versions"):
        op.add_column("source_versions", sa.Column("candidate_chunk_set_id", sa.String(64), nullable=True))
    _index("source_versions", "ix_source_versions_active_chunk_set_id", ["active_chunk_set_id"])
    _index("source_versions", "ix_source_versions_candidate_chunk_set_id", ["candidate_chunk_set_id"])

    if "chunk_set_id" not in _columns("source_chunks"):
        op.add_column("source_chunks", sa.Column("chunk_set_id", sa.String(64), nullable=True))
    if "chunk_set_id" not in _columns("source_review_snapshots"):
        op.add_column("source_review_snapshots", sa.Column("chunk_set_id", sa.String(64), nullable=True))

    version_ids = set(bind.execute(sa.text("SELECT DISTINCT source_version_id FROM source_chunks")).scalars())
    version_ids.update(bind.execute(sa.text("SELECT DISTINCT source_version_id FROM source_review_snapshots")).scalars())
    for version_id in sorted(value for value in version_ids if value):
        version = bind.execute(sa.text(
            "SELECT review_status, current_review_snapshot_id FROM source_versions WHERE id = :id"
        ), {"id": version_id}).mappings().first()
        if not version:
            continue
        job = bind.execute(sa.text(
            "SELECT id, preparation_revision, execution_snapshot_id FROM source_preparation_jobs "
            "WHERE source_version_id = :id ORDER BY preparation_revision DESC"
        ), {"id": version_id}).mappings().first()
        run = bind.execute(sa.text(
            "SELECT flow_run_id FROM source_chunks WHERE source_version_id = :id AND flow_run_id IS NOT NULL "
            "ORDER BY created_at DESC"
        ), {"id": version_id}).mappings().first()
        chunk_set_id = f"chunkset_{uuid.uuid4().hex}"
        active = version["review_status"] == "approved" and bool(version["current_review_snapshot_id"])
        revision_no = int(job["preparation_revision"]) if job else 1
        bind.execute(sa.text(
            "INSERT INTO source_chunk_sets "
            "(id, source_version_id, source_preparation_job_id, flow_run_id, execution_snapshot_id, "
            "preparation_revision, status, content_digest, chunk_count, metrics_json, activated_at, created_at, updated_at) "
            "VALUES (:id, :version, :job, :run, :snapshot, :revision, :status, NULL, "
            "(SELECT COUNT(*) FROM source_chunks WHERE source_version_id = :version), :metrics, :activated, :now, :now)"
        ), {
            "id": chunk_set_id, "version": version_id, "job": job["id"] if job else None,
            "run": run["flow_run_id"] if run else None,
            "snapshot": job["execution_snapshot_id"] if job else None, "revision": revision_no,
            "status": "active" if active else "candidate", "metrics": "{}",
            "activated": now if active else None, "now": now,
        })
        bind.execute(sa.text(
            "UPDATE source_chunks SET chunk_set_id = :chunk_set WHERE source_version_id = :version"
        ), {"chunk_set": chunk_set_id, "version": version_id})
        bind.execute(sa.text(
            "UPDATE source_review_snapshots SET chunk_set_id = :chunk_set WHERE source_version_id = :version"
        ), {"chunk_set": chunk_set_id, "version": version_id})
        pointer = "active_chunk_set_id" if active else "candidate_chunk_set_id"
        bind.execute(sa.text(
            f"UPDATE source_versions SET {pointer} = :chunk_set WHERE id = :version"
        ), {"chunk_set": chunk_set_id, "version": version_id})

    chunk_uniques, chunk_fks = _uniques("source_chunks"), _foreign_keys("source_chunks")
    with op.batch_alter_table("source_chunks") as batch:
        batch.alter_column("chunk_set_id", existing_type=sa.String(64), nullable=False)
        if "fk_source_chunks_chunk_set" not in chunk_fks and not any(
            item.get("referred_table") == "source_chunk_sets" for item in sa.inspect(bind).get_foreign_keys("source_chunks")
        ):
            batch.create_foreign_key("fk_source_chunks_chunk_set", "source_chunk_sets", ["chunk_set_id"], ["id"])
        if "uq_source_chunk_version_logical_id" in chunk_uniques:
            batch.drop_constraint("uq_source_chunk_version_logical_id", type_="unique")
        if "uq_source_chunk_set_logical_id" not in chunk_uniques:
            batch.create_unique_constraint("uq_source_chunk_set_logical_id", ["chunk_set_id", "source_chunk_id"])
    _index("source_chunks", "ix_source_chunks_chunk_set_id", ["chunk_set_id"])

    review_uniques, review_fks = _uniques("source_review_snapshots"), _foreign_keys("source_review_snapshots")
    with op.batch_alter_table("source_review_snapshots") as batch:
        batch.alter_column("chunk_set_id", existing_type=sa.String(64), nullable=False)
        if "fk_source_review_snapshot_chunk_set" not in review_fks and not any(
            item.get("referred_table") == "source_chunk_sets" for item in sa.inspect(bind).get_foreign_keys("source_review_snapshots")
        ):
            batch.create_foreign_key("fk_source_review_snapshot_chunk_set", "source_chunk_sets", ["chunk_set_id"], ["id"])
        if "uq_source_review_digest" in review_uniques:
            batch.drop_constraint("uq_source_review_digest", type_="unique")
        if "uq_source_review_chunk_set_digest" not in review_uniques:
            batch.create_unique_constraint("uq_source_review_chunk_set_digest", ["chunk_set_id", "content_digest"])
    _index("source_review_snapshots", "ix_source_review_snapshots_chunk_set_id", ["chunk_set_id"])

    version_fks = _foreign_keys("source_versions")
    with op.batch_alter_table("source_versions") as batch:
        if "fk_source_version_active_chunk_set" not in version_fks and not any(
            item.get("constrained_columns") == ["active_chunk_set_id"] for item in sa.inspect(bind).get_foreign_keys("source_versions")
        ):
            batch.create_foreign_key("fk_source_version_active_chunk_set", "source_chunk_sets", ["active_chunk_set_id"], ["id"])
        if "fk_source_version_candidate_chunk_set" not in version_fks and not any(
            item.get("constrained_columns") == ["candidate_chunk_set_id"] for item in sa.inspect(bind).get_foreign_keys("source_versions")
        ):
            batch.create_foreign_key("fk_source_version_candidate_chunk_set", "source_chunk_sets", ["candidate_chunk_set_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("source_versions") as batch:
        batch.drop_constraint("fk_source_version_candidate_chunk_set", type_="foreignkey")
        batch.drop_constraint("fk_source_version_active_chunk_set", type_="foreignkey")
    with op.batch_alter_table("source_review_snapshots") as batch:
        batch.drop_constraint("fk_source_review_snapshot_chunk_set", type_="foreignkey")
        batch.drop_constraint("uq_source_review_chunk_set_digest", type_="unique")
        batch.create_unique_constraint("uq_source_review_digest", ["source_version_id", "content_digest"])
        batch.drop_column("chunk_set_id")
    with op.batch_alter_table("source_chunks") as batch:
        batch.drop_constraint("fk_source_chunks_chunk_set", type_="foreignkey")
        batch.drop_constraint("uq_source_chunk_set_logical_id", type_="unique")
        batch.create_unique_constraint("uq_source_chunk_version_logical_id", ["source_version_id", "source_chunk_id"])
        batch.drop_column("chunk_set_id")
    op.drop_index("ix_source_versions_candidate_chunk_set_id", table_name="source_versions")
    op.drop_index("ix_source_versions_active_chunk_set_id", table_name="source_versions")
    op.drop_column("source_versions", "candidate_chunk_set_id")
    op.drop_column("source_versions", "active_chunk_set_id")
    op.drop_table("source_chunk_sets")
