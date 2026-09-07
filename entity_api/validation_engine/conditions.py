"""
=====================================================
validation_engine: /conditions.py
=====================================================
Most rules in Tabel 1 (Rules) don't reference a Check ID —
instead they're expressed directly as rows in Tabel 2 (Rule
Conditions): a "Trigger" row gates whether the rule applies at
all, and "Target" rows are the actual requirements.

  - No Trigger rows            -> Targets always apply.
  - Trigger rows present       -> all must pass (AND) for the
                                   rule to be "in scope"; if the
                                   trigger fails, the rule is
                                   considered not applicable and
                                   passes automatically.
  - Target rows                -> all must pass (AND) using the
                                   Data Type column.

NOTE: `Logical Group` is currently treated as informational only
(everything within a role is AND'd together) — the sample data
never needs more than one group, so no OR-grouping syntax has
been invented yet. Revisit if/when a rule needs it.
"""

from . import loader
from .operators import evaluate_operator
from .datatypes import evaluate_datatype


def evaluate_rule_conditions(rule_id: str, entity: dict, ctx: dict) -> bool:
    rows = loader.get_conditions(rule_id)

    triggers = [r for r in rows if (r.get("role") or "").strip().lower() == "trigger"]
    targets = [r for r in rows if (r.get("role") or "").strip().lower() == "target"]

    if triggers:
        trigger_pass = all(
            evaluate_operator(entity.get(r.get("field_name")), r.get("operator"), r.get("expected_value"))
            for r in triggers
        )
        if not trigger_pass:
            return True  # rule not applicable to this entity

    if not targets:
        return True

    for row in targets:
        ok = evaluate_datatype(
            entity,
            row.get("field_name"),
            row.get("data_type"),
            row.get("expected_value") or row.get("constraint_expr"),
            row.get("reference_source"),
            ctx,
        )
        if not ok:
            return False

    return True
