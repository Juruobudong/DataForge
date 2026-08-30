"""First-class operator source and catalog grouping."""
from alembic import op
import sqlalchemy as sa


revision = "20260829_operator_identity"
down_revision = "20260829_source_identity"
branch_labels = None
depends_on = None

DATAFLOW_CODES = (
    "BlocklistFilter", "CharNumberFilter", "ContentNullFilter", "GeneralFilter",
    "HashDeduplicateFilter", "HtmlEntityFilter", "LexicalDiversityFilter",
    "MeanWordLengthFilter", "MinHashDeduplicateFilter", "NgramHashDeduplicateFilter",
    "PIIAnonymizeRefiner", "PresidioFilter", "PromptedFilter", "PromptedRefiner",
    "SimHashDeduplicateFilter", "SpecialCharacterFilter", "Text2MultiHopQAGenerator",
    "Text2QAGenerator", "Text2QASampleEvaluator", "UniqueWordsFilter", "WatermarkFilter",
)
DATAFORGE_CODES = (
    "artifact-merge", "document-ir-normalizer", "document-parser", "entity-extractor",
    "entity-normalizer", "evidence-binder", "faq-record-mapper", "faq-table-row-builder",
    "graph-extractor", "graph-quality-validator", "kbc-chunker-batch", "kbc-cleaner-batch",
    "language-filter", "literal-detector", "mineru-pipeline-gpu-adapter", "multihop-qa",
    "null-filter", "pii-compliance", "prompt-generator", "qa-extractor", "relation-extractor",
    "reviewed-source-chunk-input", "schema-validator", "semantic-chunker",
    "semantic-relation-builder", "source-chunk-builder", "structured-knowledge-generator",
    "text-cleaner", "text-knowledge-mapper", "text-normalizer", "triple-builder",
    "whitespace-cleaner",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("operator_definitions")}
    indexes = {index["name"] for index in inspector.get_indexes("operator_definitions")}
    with op.batch_alter_table("operator_definitions") as batch:
        if "source" not in columns:
            batch.add_column(sa.Column("source", sa.String(length=32), nullable=True))
        if "catalog_group" not in columns:
            batch.add_column(sa.Column("catalog_group", sa.String(length=32), nullable=True))

    definitions = sa.table(
        "operator_definitions",
        sa.column("code", sa.String(length=120)),
        sa.column("source", sa.String(length=32)),
        sa.column("catalog_group", sa.String(length=32)),
    )
    op.execute(definitions.update().values(source="custom", catalog_group="custom"))
    op.execute(definitions.update().where(definitions.c.code.in_(DATAFORGE_CODES)).values(
        source="dataforge", catalog_group="dataforge",
    ))
    op.execute(definitions.update().where(definitions.c.code.in_(DATAFLOW_CODES)).values(
        source="dataflow", catalog_group="dataflow_featured",
    ))
    remaining = op.get_bind().execute(sa.select(sa.func.count()).select_from(definitions).where(
        sa.or_(definitions.c.source.is_(None), definitions.c.catalog_group.is_(None)),
    )).scalar_one()
    if remaining:
        raise RuntimeError("operator identity backfill left NULL rows")

    with op.batch_alter_table("operator_definitions") as batch:
        batch.alter_column("source", existing_type=sa.String(length=32), nullable=False)
        batch.alter_column("catalog_group", existing_type=sa.String(length=32), nullable=False)
        if "ix_operator_definitions_source" not in indexes:
            batch.create_index("ix_operator_definitions_source", ["source"], unique=False)
        if "ix_operator_definitions_catalog_group" not in indexes:
            batch.create_index("ix_operator_definitions_catalog_group", ["catalog_group"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("operator_definitions") as batch:
        batch.drop_index("ix_operator_definitions_catalog_group")
        batch.drop_index("ix_operator_definitions_source")
        batch.drop_column("catalog_group")
        batch.drop_column("source")
