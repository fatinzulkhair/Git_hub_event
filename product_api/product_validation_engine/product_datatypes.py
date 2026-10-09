"""
=====================================================
product_validation_engine: /product_datatypes.py
=====================================================
Implements the "Daftar Data Type" section of the rule-engine
documentation: Exist, Text, Number, Boolean, Date, Enum, Regex,
Romanize, Length, Unique.

Each function takes the raw field value (or the product + field
name, for the ones that need DB/batch context) and the
Constraint string documented for that type, e.g.
"min=1;max=10" or "pattern=alpha_only".
"""

from datetime import datetime

import regex

from ..product_database import query_one
from .product_values import is_empty


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
      - "database.<table>.<column>": unique against that DB column, ignoring
        the current product's own rows (same product_hash).
    Anything else fails closed.
    """
    if is_empty(value):
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
            return False
        seen.add(str(value))
        return True

    if ref.startswith("database."):
        _, _, rest = ref.partition("database.")
        table, _, column = rest.partition(".")
        table = table.strip()
        column = column.strip() or field_name
        row = query_one(
            f"SELECT COUNT(*) AS matches FROM {table} "
            f"WHERE {column} = ? AND product_hash != ?",
            (value, current_hash or ""),
            conn=ctx.get("conn"),
        )
        return row["matches"] == 0

    # Unknown reference source: fail closed rather than silently pass
    return False


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
    # "unique" is handled separately since it needs field_name + ctx
}


def evaluate_datatype(product: dict, field_name: str, data_type: str,
                      expected_or_constraint: str, reference_source: str,
                      ctx: dict) -> bool:
    """Dispatch a Data Type check for one field of one product."""
    if not data_type:
        raise ValueError("Data type is required")

    dtype = data_type.strip().lower()
    value = product.get(field_name)

    if dtype == "unique":
        return check_unique(value, field_name, reference_source, ctx)

    checker = _CHECKERS.get(dtype)
    if checker is None:
        raise ValueError(f"Unknown data type: {data_type}")

    return checker(value, expected_or_constraint)
