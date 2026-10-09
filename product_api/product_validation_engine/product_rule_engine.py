"""
=====================================================
product_validation_engine: /product_rule_engine.py
=====================================================
Entry point for the rule engine: runs every active rule against one product.
All rule logic is user-defined and lives in the product_val_* tables — there
is no hardcoded validation in Python.

A product is validated on its own; there is no second table to join it with.
Rules see its flattened JSON — dotted paths such as `ProductType.Name` — plus
`ParentProductKey`, `Level` and `ChildCount`, through one case-insensitive
view (see product_parser.product_rule_fields).

For each active row in product_val_rules:
  - if it has a Check ID  -> resolve() it (Check or Logic, and Logic can
    chain into more Logic)
  - otherwise             -> evaluate its Rule Conditions rows

Outcomes are returned as product_validation_results rows for the caller to
write in one batch (see product_crud.store_and_validate_products).
"""

from .product_conditions import evaluate_rule_conditions
from .product_loader import ProductRuleSet, load_product_ruleset
from .product_resolvers import resolve
from .product_values import ProductCaseInsensitiveRecord


def run_all_product_rules(fields: dict, product_hash: str, product_id=None,
                          ctx: dict = None, ruleset: ProductRuleSet = None) -> tuple:
    """Run every active rule against one product's fields.

    Returns (results, rows): `results` describes each outcome, `rows` are the
    product_validation_results tuples to insert.

    `ctx` should be the SAME dict across all products in one payload so that
    batch-scoped Unique checks see each other — see
    product_crud.store_and_validate_products for how it, and the shared
    `ruleset`, are threaded through. Omitting `ruleset` loads one for this
    product alone.
    """
    if ctx is None:
        ctx = {}
    ctx["current_product_hash"] = product_hash

    if ruleset is None:
        ruleset = load_product_ruleset()

    product = ProductCaseInsensitiveRecord(fields)
    results, rows = [], []

    for rule in ruleset.rules:
        rule_id = rule.get("rule_id")
        ctx["current_rule_id"] = rule_id  # Unique(batch) is tracked per rule

        check_id = rule.get("check_id")
        try:
            if check_id:
                passed = resolve(check_id, product, ctx, ruleset)
            else:
                passed = evaluate_rule_conditions(rule_id, product, ctx, ruleset)
            error = None
        except Exception as exc:  # a misconfigured rule shouldn't crash the batch
            passed = False
            error = str(exc)

        if passed:
            status = description = None
        else:
            status = rule.get("on_exception_status")
            description = (
                error
                or rule.get("on_exception_description")
                or rule.get("description")
            )

        rows.append((product_hash, product_id, rule_id, passed,
                     rule.get("severity"), status, description))
        results.append({
            "rule_id": rule_id,
            "passed": passed,
            "severity": rule.get("severity"),
            "status": status,
            "description": description,
        })

    return results, rows
