"""
=====================================================
product_validation_engine: /product_conditions.py
=====================================================
Most rules in product_val_rules don't reference a Check ID — instead they're
expressed directly as rows in product_val_rule_conditions: a "Trigger" row
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
within a role is AND'd together), exactly as in the entity engine.
"""

from .product_datatypes import evaluate_datatype
from .product_operators import evaluate_operator


def _role(row: dict) -> str:
    return (row.get("role") or "").strip().lower()


def evaluate_rule_conditions(rule_id: str, product: dict, ctx: dict, ruleset) -> bool:
    rows = ruleset.conditions_for(rule_id)

    triggers = [r for r in rows if _role(r) == "trigger"]
    targets = [r for r in rows if _role(r) == "target"]

    in_scope = all(
        evaluate_operator(
            product.get(row.get("field_name")),
            row.get("operator"),
            row.get("expected_value"),
        )
        for row in triggers
    )
    if not in_scope:
        return True  # rule not applicable to this product

    return all(
        evaluate_datatype(
            product,
            row.get("field_name"),
            row.get("data_type"),
            row.get("expected_value") or row.get("constraint_expr"),
            row.get("reference_source"),
            ctx,
        )
        for row in targets
    )
