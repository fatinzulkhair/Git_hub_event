"""
=====================================================
validation_engine: /operators.py
=====================================================
Implements the "Daftar Operator" section of the rule-engine
documentation. Every operator except `in` / `not_in` treats a
comma-separated Expected Value as an OR across each value, per
the documented rule:

    "kalau Expected Value berisi lebih dari 1 nilai (dipisah
    koma), operator selain in/not_in otomatis dicek dengan
    logika OR terhadap tiap nilai."
"""

import regex

from .values import is_empty, split_csv

_OR_ACROSS_VALUES = {"equals", "not_equals", "contains", "starts_with", "ends_with", "regex"}
_WHOLE_LIST = {"in", "not_in"}


def _single_op(operator: str, value, expected: str) -> bool:
    value_str = "" if value is None else str(value)

    if operator == "equals":
        return value_str == expected
    if operator == "not_equals":
        return value_str != expected
    if operator == "contains":
        return expected in value_str
    if operator == "starts_with":
        return value_str.startswith(expected)
    if operator == "ends_with":
        return value_str.endswith(expected)
    if operator == "regex":
        return bool(regex.search(expected, value_str))

    raise ValueError(f"Unknown operator: {operator}")


def evaluate_operator(value, operator: str, expected_value) -> bool:
    """Evaluate a single field value against an operator + Expected Value.

    `expected_value` is the raw string from the Rule Conditions / Checks
    table and may contain a comma-separated list.
    """
    if operator is None:
        raise ValueError("Operator is required")

    op = operator.strip().lower()

    if op == "exists":
        return not is_empty(value)

    values = split_csv(expected_value)

    if op == "in":
        return str(value) in values
    if op == "not_in":
        return str(value) not in values

    if op not in _OR_ACROSS_VALUES:
        raise ValueError(f"Unknown operator: {operator}")

    if not values:
        return False

    return any(_single_op(op, value, v) for v in values)
