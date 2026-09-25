"""
=====================================================
validation_engine: /conditions.py
=====================================================
Most rules in Tabel 1 (Rules) don't reference a Check ID — instead they're
expressed directly as rows in Tabel 2 (Rule Conditions): a "Trigger" row
gates whether the rule applies at all, and "Target" rows are the actual
requirements.

  - No Trigger rows            -> Targets always apply.
  - Trigger rows present       -> all must pass (AND) for the rule to be
                                   "in scope"; if the trigger fails, the rule
                                   is considered not applicable and passes
                                   automatically.
  - Target rows                -> all must pass (AND) using the Data Type
                                   column.

NOTE: `Logical Group` is currently treated as informational only (everything
within a role is AND'd together) — the sample data never needs more than one
group, so no OR-grouping syntax has been invented yet. Revisit if/when a rule
needs it.
"""

from .datatypes import evaluate_datatype
from .operators import evaluate_operator


def _role(row: dict) -> str:
    return (row.get("role") or "").strip().lower()


def evaluate_rule_conditions(rule_id: str, entity: dict, ctx: dict, ruleset) -> bool:
    rows = ruleset.conditions_for(rule_id)

    triggers = [r for r in rows if _role(r) == "trigger"]
    targets = [r for r in rows if _role(r) == "target"]

    in_scope = all(
        evaluate_operator(
            entity.get(row.get("field_name")),
            row.get("operator"),
            row.get("expected_value"),
        )
        for row in triggers
    )
    if not in_scope:
        return True  # rule not applicable to this entity

    return all(
        evaluate_datatype(
            entity,
            row.get("field_name"),
            row.get("data_type"),
            row.get("expected_value") or row.get("constraint_expr"),
            row.get("reference_source"),
            ctx,
        )
        for row in targets
    )
