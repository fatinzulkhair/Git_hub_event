"""
=====================================================
product_validation_engine: /product_datatypes.py
=====================================================
Implements the "Daftar Data Type" section of the rule-engine
documentation: Exist, Text, Number, Boolean, Date, Enum, Regex,
Romanize, Length, Unique — plus ListEntries, which checks the entries of a
JSON list one by one (see check_list_entries).

Each function takes the raw field value (or the product + field
name, for the ones that need DB/batch context) and the
Constraint string documented for that type, e.g.
"min=1;max=10" or "pattern=alpha_only".

A failed check says why in ctx["failure_details"] (see explain()). The rule
engine adds those reasons to the description of a rule that fails, so the
result names the field and the value at fault.
"""

from datetime import datetime

import regex

from ..product_database import query_one, table_definition
from ..product_parser import flatten_product_fields
from .product_operators import evaluate_operator
from .product_values import ProductCaseInsensitiveRecord, is_empty, split_csv


def _parse_constraint(constraint: str) -> dict:
    """Parse 'min=1;max=10;pattern=alpha_only' into a dict."""
    result = {}
    if not constraint:
        return result
    for part in str(constraint).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, val = part.partition("=")
            result[key.strip()] = val.strip()
        else:
            result[part] = True
    return result


def explain(ctx: dict, reason: str) -> None:
    """Record why a check failed (see the module docstring)."""
    if ctx is not None:
        ctx.setdefault("failure_details", []).append(reason)


def _short(value) -> str:
    """A value as it appears in a failure reason: at most 80 characters."""
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


# ---------------------------------------------------
# Individual data-type checks
# ---------------------------------------------------

def check_exist(value, constraint: str = None) -> bool:
    return not is_empty(value)


def check_text(value, constraint: str = None) -> bool:
    if is_empty(value):
        return False
    opts = _parse_constraint(constraint)
    if opts.get("pattern") == "alpha_only":
        return bool(regex.fullmatch(r"[A-Za-z\s]+", str(value)))
    return True  # any non-empty scalar counts as text


def check_number(value, constraint: str = None) -> bool:
    if is_empty(value):
        return False
    opts = _parse_constraint(constraint)
    number_type = opts.get("type", "float")
    try:
        parsed = int(value) if number_type == "int" else float(value)
    except (TypeError, ValueError):
        return False
    if "min" in opts and parsed < float(opts["min"]):
        return False
    if "max" in opts and parsed > float(opts["max"]):
        return False
    return True


def check_boolean(value, constraint: str = None) -> bool:
    truthy = {"true", "y", "yes", "1"}
    falsy = {"false", "n", "no", "0"}
    if isinstance(value, bool):
        return True
    return str(value).strip().lower() in truthy | falsy


def check_date(value, constraint: str = None) -> bool:
    if is_empty(value):
        return False
    opts = _parse_constraint(constraint)
    fmt = opts.get("format", "%Y-%m-%d")
    try:
        datetime.strptime(str(value), fmt)
        return True
    except ValueError:
        return False


def check_enum(value, expected_value: str = None) -> bool:
    if is_empty(value) or not expected_value:
        return False
    allowed = [v.strip() for v in str(expected_value).split(",")]
    return str(value) in allowed


def check_regex(value, expected_value: str = None) -> bool:
    if is_empty(value) or not expected_value:
        return False
    return bool(regex.search(str(expected_value), str(value)))


def check_romanize(value, constraint: str = None) -> bool:
    """True if the value is already Latin/ASCII."""
    if is_empty(value):
        return True  # nothing to romanize is not a violation
    text = str(value)
    return bool(regex.fullmatch(r"[\p{Latin}\p{N}\p{P}\p{Z}]+", text))


def check_length(value, constraint: str = None) -> bool:
    if is_empty(value):
        return False
    opts = _parse_constraint(constraint)
    length = len(str(value))
    if "min" in opts and length < int(opts["min"]):
        return False
    if "max" in opts and length > int(opts["max"]):
        return False
    return True


def check_unique(value, field_name: str, reference_source: str, ctx: dict) -> bool:
    """Unique check. `reference_source` is either:
      - "batch" (or empty): unique among the products processed together in
        one payload, tracked in ctx['batch_seen'] separately for each rule.
      - "database.<table>.<column>": unique against that column, ignoring
        the current product's own rows (same product_hash). The table must
        be one of this program's tables with a product_hash column, and the
        column one of its columns, so a rule cannot reach anything else.
    Anything else fails closed.
    """
    if is_empty(value):
        explain(ctx, f"{field_name} is missing or empty")
        return False

    ref = (reference_source or "").strip()
    ctx = ctx if ctx is not None else {}
    current_hash = ctx.get("current_product_hash")

    if ref in ("batch", ""):
        # Keyed by rule as well as field: two rules checking the same field
        # must not see each other's values as duplicates.
        seen = ctx.setdefault("batch_seen", {}).setdefault(
            (ctx.get("current_rule_id"), field_name), set()
        )
        if str(value) in seen:
            explain(ctx, f"{field_name} '{_short(value)}' appears more than once in this payload")
            return False
        seen.add(str(value))
        return True

    if ref.startswith("database."):
        _, _, rest = ref.partition("database.")
        table, _, column = rest.partition(".")
        table, column = _known_column(table.strip(), column.strip() or field_name, ref)
        row = query_one(
            f"SELECT COUNT(*) AS matches FROM {table} "
            f"WHERE {column} = ? AND product_hash <> ?",
            (value, current_hash or ""),
            conn=ctx.get("conn"),
        )
        if row["matches"]:
            explain(ctx, f"{field_name} '{_short(value)}' is already used in {table}.{column}")
        return row["matches"] == 0

    # Unknown reference source: fail closed rather than silently pass
    explain(ctx, f"Unique: unknown reference source '{ref}'")
    return False


def _known_column(table: str, column: str, ref: str) -> tuple:
    """(table, column) as defined in product_database.PRODUCT_TABLES, or
    ValueError — so a rule names only this program's tables, never SQL of
    its own."""
    definition = table_definition(table)
    if definition is None or "product_hash" not in definition.column_names:
        raise ValueError(f"Unique: '{ref}' does not name a product table of this program")
    by_lower = {name.lower(): name for name in definition.column_names}
    if column.lower() not in by_lower:
        raise ValueError(f"Unique: table {definition.name} has no column '{column}'")
    return definition.name, by_lower[column.lower()]


def check_list_entries(product: dict, field_name: str, operator: str,
                       expected_value: str, constraint: str, ctx: dict) -> bool:
    """Check a JSON list entry by entry, which the flattened (comma-joined)
    fields cannot do. Used in a Rule Condition row:

    field_name      "<list>[].<field>": the list, and the field of each entry
                    the operator tests, e.g. "AlternateProductKeys[].ProductKey".
                    "<list>[]" (or just "<list>") takes every entry, e.g. "Units[]".
    operator        which entries count, e.g. starts_with with expected_value
    expected_value  "urn:inexto:id:prd:msku" (comma-separated values are OR'd,
                    as for every operator). Without an operator, an entry
                    counts when it has the field.
    constraint_expr "required=A,B": fields every counted entry must have,
                    non-empty (0 is a value); "min=1", "max=2" or "count=2":
                    how many entries must count.

    Fails, saying why, when the count is out of range or a counted entry
    lacks a required field.
    """
    list_path, _, entry_field = str(field_name).partition("[]")
    list_path, entry_field = list_path.strip(), entry_field.strip().lstrip(".")
    if operator and not entry_field:
        raise ValueError(f"ListEntries: '{field_name}' needs a field after [] for the operator")

    entries = product.get(list_path)
    if entries is None:
        entries = []                     # a missing list has no entries
    if not isinstance(entries, list):
        raise ValueError(f"ListEntries: {list_path} is not a list of objects")

    opts = _parse_constraint(constraint)
    required = split_csv(opts.get("required"))
    low, high = _count_bounds(opts)

    counted, reasons = 0, []
    for index, item in enumerate(entries):
        entry = ProductCaseInsensitiveRecord(
            flatten_product_fields(item) if isinstance(item, dict) else {}
        )
        if entry_field:
            value = entry.get(entry_field)
            if operator:
                selected = evaluate_operator(value, operator, expected_value)
            else:
                selected = not is_empty(value)
            if not selected:
                continue
        counted += 1
        missing = [name for name in required if is_empty(entry.get(name))]
        if missing:
            label = f" ({_short(entry.get(entry_field))})" if entry_field else ""
            reasons.append(f"{list_path}[{index}]{label} is missing {', '.join(missing)}")

    if counted < low or (high is not None and counted > high):
        which = ""
        if entry_field:
            which = f" with {entry_field} {operator} {expected_value}" if operator else f" with {entry_field}"
        noun = "entry" if counted == 1 else "entries"
        reasons.insert(0, f"{list_path} has {counted} {noun}{which}; needs {_needed(low, high)}")

    for reason in reasons:
        explain(ctx, reason)
    return not reasons


def _count_bounds(opts: dict) -> tuple:
    """(lowest, highest or None) from count= / min= / max=."""
    if "count" in opts:
        return int(opts["count"]), int(opts["count"])
    return int(opts.get("min", 0)), (int(opts["max"]) if "max" in opts else None)


def _needed(low: int, high) -> str:
    if high is None:
        return f"at least {low}"
    if low == high:
        return f"exactly {low}"
    return f"at most {high}" if low == 0 else f"between {low} and {high}"


_CHECKERS = {
    "exist": check_exist,
    "text": check_text,
    "number": check_number,
    "boolean": check_boolean,
    "date": check_date,
    "enum": check_enum,
    "regex": check_regex,
    "romanize": check_romanize,
    "length": check_length,
    # "unique" and "listentries" are handled separately: they need more
    # than the value (field name and context, or the whole condition row)
}


def evaluate_datatype(product: dict, field_name: str, data_type: str,
                      expected_or_constraint: str, reference_source: str,
                      ctx: dict, condition: dict = None) -> bool:
    """Dispatch a Data Type check for one field of one product.

    `condition` is the Rule Condition row when called for one; ListEntries
    reads its operator / expected_value / constraint_expr columns.
    """
    if not data_type:
        raise ValueError("Data type is required")

    dtype = data_type.strip().lower()

    if dtype == "listentries":
        if condition is None:
            raise ValueError(
                "ListEntries can only be used in a Rule Condition (it reads the "
                "operator, expected_value and constraint_expr columns)"
            )
        return check_list_entries(
            product, field_name, condition.get("operator"),
            condition.get("expected_value"), condition.get("constraint_expr"), ctx,
        )

    value = product.get(field_name)

    if dtype == "unique":
        return check_unique(value, field_name, reference_source, ctx)

    checker = _CHECKERS.get(dtype)
    if checker is None:
        raise ValueError(f"Unknown data type: {data_type}")

    passed = checker(value, expected_or_constraint)
    if not passed:
        if is_empty(value):
            explain(ctx, f"{field_name} is missing or empty")
        else:
            argument = f" {expected_or_constraint}" if expected_or_constraint else ""
            explain(ctx, f"{field_name} '{_short(value)}' fails {data_type}{argument}")
    return passed
