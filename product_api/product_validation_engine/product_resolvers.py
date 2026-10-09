"""
=====================================================
product_validation_engine: /product_resolvers.py
=====================================================
This is the piece that lets product_val_logic chain into itself. An "id"
appearing in an If/Then/Else column, or in a rule's Check ID column, can be:

  - the literal string "True" / "False"
  - a Check ID (found in product_val_checks)   -> evaluate_check
  - a Logic ID (found in product_val_logic)    -> evaluate_logic (recurses)

resolve() figures out which one it is and dispatches. Lookups hit the
payload's in-memory ProductRuleSet, so chaining costs nothing at the database.
"""

from .product_datatypes import evaluate_datatype
from .product_operators import evaluate_operator


def resolve(id_or_literal, product: dict, ctx: dict, ruleset) -> bool:
    if id_or_literal is None:
        return True  # no check configured => nothing to fail on

    token = str(id_or_literal).strip()

    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False

    check_row = ruleset.check(token)
    if check_row is not None:
        return evaluate_check(check_row, product, ctx, ruleset)

    logic_row = ruleset.logic_for(token)
    if logic_row is not None:
        return evaluate_logic(logic_row, product, ctx, ruleset)

    raise ValueError(f"Unresolvable Check ID / Logic ID: '{token}'")


def evaluate_logic(logic_row: dict, product: dict, ctx: dict, ruleset) -> bool:
    if resolve(logic_row.get("if_check_id"), product, ctx, ruleset):
        return resolve(logic_row.get("then_check_id"), product, ctx, ruleset)
    return resolve(logic_row.get("else_check_id"), product, ctx, ruleset)


def evaluate_check(check_row: dict, product: dict, ctx: dict, ruleset) -> bool:
    field_names = [f.strip() for f in str(check_row.get("field_names") or "").split(",") if f.strip()]
    combine = (check_row.get("field_combine") or "AND").strip().upper()
    check_type = (check_row.get("check_type") or "").strip().lower()

    if not field_names:
        raise ValueError(f"Check '{check_row.get('check_id')}' has no field_names")

    if check_type == "operator":
        results = [
            evaluate_operator(
                product.get(field),
                check_row.get("check_value"),
                check_row.get("expected_value"),
            )
            for field in field_names
        ]
    elif check_type == "data type":
        results = [
            evaluate_datatype(
                product,
                field,
                check_row.get("check_value"),
                check_row.get("expected_value"),
                check_row.get("reference_source"),
                ctx,
            )
            for field in field_names
        ]
    else:
        raise ValueError(
            f"Unknown check_type '{check_row.get('check_type')}' "
            f"on check '{check_row.get('check_id')}'"
        )

    return all(results) if combine == "AND" else any(results)
