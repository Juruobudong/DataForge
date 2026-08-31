"""Read-only database state inspection for the V7 initialization gate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.schema import ForeignKeyConstraint, UniqueConstraint

from .models import Base


CURRENT_SCHEMA_REVISION = "20260830_v7_baseline"
IGNORED_TABLES = frozenset({"alembic_version"})


class DatabaseState(str, Enum):
    EMPTY = "empty"
    CURRENT = "current"
    NON_EMPTY_CURRENT_UNKNOWN = "non_empty_current_unknown"
    NON_EMPTY_OLD_REVISION = "non_empty_old_revision"
    NON_EMPTY_NO_REVISION = "non_empty_no_revision"


@dataclass(frozen=True)
class DatabasePreflightResult:
    state: DatabaseState
    is_empty: bool
    current_revision: str | None
    expected_revision: str
    table_count: int
    dataforge_table_count: int
    tables: tuple[str, ...]
    schema_errors: tuple[str, ...]
    requires_user_decision: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["tables"] = list(self.tables)
        payload["schema_errors"] = list(self.schema_errors)
        return payload


class DatabaseUserDecisionRequired(RuntimeError):
    code = "DATABASE_USER_DECISION_REQUIRED"

    def __init__(self, preflight: DatabasePreflightResult):
        super().__init__(preflight.message)
        self.preflight = preflight

    def to_dict(self) -> dict[str, Any]:
        preflight = self.preflight
        return {
            "error": self.code,
            "database_empty": preflight.is_empty,
            "database_state": preflight.state.value,
            "current_revision": preflight.current_revision,
            "target_revision": preflight.expected_revision,
            "table_count": preflight.table_count,
            "dataforge_table_count": preflight.dataforge_table_count,
            "tables": list(preflight.tables),
            "schema_errors": list(preflight.schema_errors),
            "message": preflight.message,
        }


def _fk_action(value):
    action = str(value or "NO ACTION").upper()
    return "RESTRICT" if action in {"NO ACTION", "RESTRICT"} else action


def _expected_foreign_keys(table):
    result = set()
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        elements = tuple(constraint.elements)
        result.add((
            tuple(element.parent.name for element in elements),
            elements[0].column.table.name,
            tuple(element.column.name for element in elements),
            (_fk_action(constraint.ondelete), _fk_action(constraint.onupdate)),
        ))
    return result


def _actual_foreign_keys(inspector, table_name: str):
    if inspector.bind.dialect.name == "sqlite":
        # SQLite's SQL text reflection can omit actions on inline REFERENCES.
        # PRAGMA reports the actual engine contract, including composite order.
        from contextlib import nullcontext
        from sqlalchemy.engine import Connection
        bind = inspector.bind
        quoted = bind.dialect.identifier_preparer.quote_identifier(table_name)
        with (nullcontext(bind) if isinstance(bind, Connection) else bind.connect()) as connection:
            rows = connection.exec_driver_sql(f"PRAGMA foreign_key_list({quoted})").mappings().all()
        groups = {}
        for row in rows:
            groups.setdefault(row["id"], []).append(row)
        result = set()
        for rows in groups.values():
            rows.sort(key=lambda row: row["seq"])
            remote = rows[0]["table"]
            primary = inspector.get_pk_constraint(remote).get("constrained_columns") or () if any(row["to"] is None for row in rows) else ()
            columns = tuple(row["to"] if row["to"] is not None else primary[row["seq"]] for row in rows)
            result.add((tuple(row["from"] for row in rows), remote, columns,
                        (_fk_action(rows[0]["on_delete"]), _fk_action(rows[0]["on_update"]))))
        return result
    return {
        (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table") or ""),
            tuple(item.get("referred_columns") or ()),
            (_fk_action((item.get("options") or {}).get("ondelete")), _fk_action((item.get("options") or {}).get("onupdate"))),
        )
        for item in inspector.get_foreign_keys(table_name)
    }


def _expected_unique_constraints(table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } | {tuple(column.name for column in index.columns) for index in table.indexes if index.unique}


def _actual_unique_constraints(inspector, table_name: str) -> set[tuple[str, ...]]:
    constraints = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table_name)
    }
    constraints.update(
        tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    return constraints


def schema_validation_errors(inspector, actual_tables: set[str]) -> tuple[str, ...]:
    """Compare the current database with the model contract without mutating it."""
    expected_tables = set(Base.metadata.tables)
    errors: list[str] = []

    for table_name in sorted(expected_tables - actual_tables):
        errors.append(f"缺少表：{table_name}")
    for table_name in sorted(actual_tables - expected_tables):
        errors.append(f"存在未识别表：{table_name}")

    for table_name in sorted(expected_tables & actual_tables):
        expected_table = Base.metadata.tables[table_name]
        from .database_schema_contract import column_and_index_errors
        try:
            errors.extend(column_and_index_errors(inspector, expected_table))
        except (ValueError, KeyError, NotImplementedError) as exc:
            errors.append(f"{table_name} 无法确认结构契约：{exc}")
        actual_columns = {item["name"] for item in inspector.get_columns(table_name)}
        for column_name in sorted(set(expected_table.columns.keys()) - actual_columns):
            errors.append(f"{table_name} 缺少列：{column_name}")

        missing_foreign_keys = _expected_foreign_keys(expected_table) - _actual_foreign_keys(
            inspector, table_name,
        )
        for local_columns, remote_table, remote_columns, actions in sorted(missing_foreign_keys):
            errors.append(
                f"{table_name} 缺少外键：{','.join(local_columns)} -> "
                f"{remote_table}({','.join(remote_columns)}) [DELETE {actions[0]}, UPDATE {actions[1]}]"
            )

        missing_uniques = _expected_unique_constraints(expected_table) - _actual_unique_constraints(
            inspector, table_name,
        )
        for columns in sorted(missing_uniques):
            errors.append(f"{table_name} 缺少唯一约束：{','.join(columns)}")
        for columns in sorted(_actual_unique_constraints(inspector, table_name) - _expected_unique_constraints(expected_table)):
            errors.append(f"{table_name} 存在未声明唯一约束：{','.join(columns)}")
        for columns, remote, remote_columns, actions in sorted(_actual_foreign_keys(inspector, table_name) - _expected_foreign_keys(expected_table)):
            errors.append(f"{table_name} 存在未声明外键：{','.join(columns)} -> {remote}({','.join(remote_columns)}) [DELETE {actions[0]}, UPDATE {actions[1]}]")

    return tuple(errors)


def _message(state: DatabaseState, current_revision: str | None, table_count: int) -> str:
    revision = current_revision or "未识别"
    if state == DatabaseState.EMPTY:
        return "检测到空数据库，可以初始化当前 DataForge V7 Baseline。"
    if state == DatabaseState.CURRENT:
        return f"数据库已是当前合法 Schema Revision：{CURRENT_SCHEMA_REVISION}。"
    if state == DatabaseState.NON_EMPTY_CURRENT_UNKNOWN:
        return (
            f"数据库记录的 Revision 为 {revision}，但当前 Schema 与 DataForge 模型不一致；"
            "已停止自动初始化。"
        )
    if state == DatabaseState.NON_EMPTY_OLD_REVISION:
        return (
            f"检测到 {table_count} 个业务表，当前 Revision 为 {revision}，目标为 "
            f"{CURRENT_SCHEMA_REVISION}；当前版本不会自动迁移历史数据库。"
        )
    return (
        f"检测到 {table_count} 个业务表，但 Schema Revision 未识别；"
        "当前版本不会自动迁移、回填或修改已有数据。"
    )


def inspect_database(database_url: str) -> DatabasePreflightResult:
    """Inspect revision, tables and structural contract without database writes."""
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            all_tables = set(inspector.get_table_names())
            business_tables = all_tables - IGNORED_TABLES
            heads = MigrationContext.configure(connection).get_current_heads()
            revision = heads[0] if len(heads) == 1 else ",".join(sorted(heads)) or None
            schema_errors = (
                schema_validation_errors(inspector, business_tables)
                if revision == CURRENT_SCHEMA_REVISION
                else ()
            )
    finally:
        engine.dispose()

    if not business_tables and revision is None:
        state = DatabaseState.EMPTY
    elif revision == CURRENT_SCHEMA_REVISION and not schema_errors:
        state = DatabaseState.CURRENT
    elif revision == CURRENT_SCHEMA_REVISION:
        state = DatabaseState.NON_EMPTY_CURRENT_UNKNOWN
    elif revision is None:
        state = DatabaseState.NON_EMPTY_NO_REVISION
    else:
        state = DatabaseState.NON_EMPTY_OLD_REVISION

    tables = tuple(sorted(business_tables))
    expected_tables = set(Base.metadata.tables)
    return DatabasePreflightResult(
        state=state,
        is_empty=state == DatabaseState.EMPTY,
        current_revision=revision,
        expected_revision=CURRENT_SCHEMA_REVISION,
        table_count=len(tables),
        dataforge_table_count=len(business_tables & expected_tables),
        tables=tables,
        schema_errors=schema_errors,
        requires_user_decision=state not in {DatabaseState.EMPTY, DatabaseState.CURRENT},
        message=_message(state, revision, len(tables)),
    )
