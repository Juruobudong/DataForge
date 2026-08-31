"""Dialect-aware structural comparisons; no database mutation or upgrade policy."""
import re
from sqlalchemy import types as sqltypes
from sqlalchemy.schema import CheckConstraint


def type_contract(value, dialect):
    value = value.dialect_impl(dialect)
    # MySQL reflects BOOLEAN as TINYINT(1), not SQLAlchemy Boolean.
    if isinstance(value, sqltypes.Boolean) or (dialect.name == "mysql" and
            type(value).__name__ == "TINYINT" and getattr(value, "display_width", None) == 1
            and not getattr(value, "unsigned", False)):
        return ("boolean",)
    if isinstance(value, sqltypes.JSON):
        return ("json",)
    if isinstance(value, sqltypes.BigInteger):
        return ("bigint", bool(getattr(value, "unsigned", False)))
    if isinstance(value, sqltypes.SmallInteger):
        return ("smallint", bool(getattr(value, "unsigned", False)))
    if isinstance(value, sqltypes.Integer):
        if type(value).__name__ in {"TINYINT", "MEDIUMINT"}:
            return (type(value).__name__.lower(), bool(getattr(value, "unsigned", False)))
        return ("integer", bool(getattr(value, "unsigned", False)))
    if isinstance(value, sqltypes.Float):
        return ("float", value.precision)
    if isinstance(value, sqltypes.Numeric):
        return ("numeric", value.precision, value.scale, bool(getattr(value, "unsigned", False)))
    if isinstance(value, sqltypes.Text):
        family = type(value).__name__.lower()
        return (family if family in {"tinytext", "mediumtext", "longtext"} else "text",)
    if isinstance(value, sqltypes.String) and not isinstance(value, sqltypes.Enum):
        return ("char" if isinstance(value, sqltypes.CHAR) else "varchar", value.length)
    if isinstance(value, sqltypes.DateTime):
        return ("timestamp" if isinstance(value, sqltypes.TIMESTAMP) else "datetime",
                getattr(value, "fsp", None) or 0)
    if isinstance(value, sqltypes.Date):
        return ("date",)
    if isinstance(value, sqltypes.Time):
        return ("time", getattr(value, "fsp", None) or 0)
    if isinstance(value, sqltypes.LargeBinary):
        return ("binary", value.length)
    raise ValueError(f"无法确认字段类型契约：{type(value).__name__}")


def default_contract(value):
    if value is None:
        return None
    text = str(value).strip()
    # Strip only balanced, fully enclosing parentheses, respecting literals.
    while text.startswith("(") and text.endswith(")"):
        depth, quote, encloses = 0, None, True
        for index, char in enumerate(text):
            if char in "'\"":
                quote = None if quote == char else char if quote is None else quote
            if quote is None:
                depth += (char == "(") - (char == ")")
                if depth == 0 and index < len(text) - 1:
                    encloses = False
                    break
        if not encloses:
            break
        text = text[1:-1].strip()
    text = re.sub(r"^_[a-zA-Z0-9]+(?=')", "", text)
    if text.upper() == "NULL":
        return None
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return ("literal", text[1:-1].replace("''", "'"))
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
        from decimal import Decimal
        return ("number", Decimal(text))
    if re.fullmatch(r"current_timestamp(\(\d*\))?", text, re.I):
        return ("current_timestamp", re.search(r"\d+", text).group() if re.search(r"\d+", text) else "0")
    raise ValueError("无法确认 server default 表达式契约")


def check_contract(expression):
    """Parse the model's AND/OR/IS NULL owner constraint, ignoring dialect quoting."""
    raw = str(expression)
    tokens = re.findall(r"`[^`]+`|\"[^\"]+\"|[a-zA-Z_][a-zA-Z0-9_]*|[()]", raw)
    if re.sub(r"\s+", "", raw) != "".join(tokens):
        raise ValueError("无法确认 Check Constraint 表达式契约")
    tokens = [token.strip('`"').lower() for token in tokens]
    position = 0
    def primary():
        nonlocal position
        if tokens[position] == "(":
            position += 1
            result = expr("or")
            if tokens[position] != ")":
                raise ValueError("Check Constraint 括号不完整")
            position += 1
            return result
        name = tokens[position]
        position += 1
        if tokens[position] != "is":
            raise ValueError("无法确认 Check Constraint 运算符")
        position += 1
        negated = tokens[position] == "not"
        position += int(negated)
        if tokens[position] != "null":
            raise ValueError("无法确认 Check Constraint 比较值")
        position += 1
        return ("is_not_null" if negated else "is_null", name)
    def expr(level):
        nonlocal position
        operand = (lambda: expr("and")) if level == "or" else primary
        values = [operand()]
        while position < len(tokens) and tokens[position] == level:
            position += 1
            values.append(operand())
        flattened = []
        for value in values:
            flattened.extend(value[1] if value[0] == level else [value])
        return flattened[0] if len(flattened) == 1 else (level, tuple(sorted(flattened, key=repr)))
    try:
        result = expr("or")
        if position != len(tokens):
            raise ValueError("Check Constraint 存在未知表达式")
        return result
    except IndexError as exc:
        raise ValueError("Check Constraint 不完整") from exc


def column_and_index_errors(inspector, table):
    name, dialect = table.name, inspector.bind.dialect
    actual = {item["name"]: item for item in inspector.get_columns(name)}
    errors = []
    for extra in sorted(set(actual) - set(table.columns.keys())):
        errors.append(f"{name} 存在未识别列：{extra}")
    for column in table.columns:
        if column.name not in actual:
            continue
        value = actual[column.name]
        label = f"{name}.{column.name}"
        try:
            expected_type, actual_type = type_contract(column.type, dialect), type_contract(value["type"], dialect)
            if expected_type != actual_type:
                errors.append(f"{label} 类型不一致：预期 {expected_type}，实际 {actual_type}")
            if bool(column.nullable) != bool(value["nullable"]):
                errors.append(f"{label} nullable 不一致")
            expected_default = str(column.server_default.arg.compile(dialect=dialect)) if column.server_default is not None and hasattr(column.server_default.arg, "compile") else (
                "'" + str(column.server_default.arg).replace("'", "''") + "'" if column.server_default is not None else None)
            if default_contract(expected_default) != default_contract(value.get("default")):
                errors.append(f"{label} server default 不一致")
        except (ValueError, KeyError, NotImplementedError) as exc:
            errors.append(f"{label} 无法校验：{exc}")
    if tuple(c.name for c in table.primary_key) != tuple(inspector.get_pk_constraint(name).get("constrained_columns") or ()):
        errors.append(f"{name} 主键不一致")
    indexes = {(tuple(i.get("column_names") or ()), bool(i.get("unique"))) for i in inspector.get_indexes(name)}
    unique_constraints = {tuple(c.get("column_names") or ()) for c in inspector.get_unique_constraints(name)}
    for index in table.indexes:
        identity = (tuple(c.name for c in index.columns), bool(index.unique))
        if identity not in indexes and not (index.unique and identity[0] in unique_constraints):
            errors.append(f"{name} 缺少索引：{','.join(identity[0])}")
    try:
        expected_checks = {check_contract(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)}
        actual_checks = {check_contract(c["sqltext"]) for c in inspector.get_check_constraints(name)}
        if expected_checks != actual_checks:
            errors.append(f"{name} Check Constraint 不一致")
    except (ValueError, KeyError, NotImplementedError) as exc:
        errors.append(f"{name} 无法校验 Check Constraint：{exc}")
    return errors
