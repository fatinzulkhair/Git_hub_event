"""
=====================================================
validation_engine: /rule_engine.py
=====================================================
Entry point for the dynamic rule engine. This is what
validation.py calls instead of the old hardcoded
eu_detect()/romanizecheck() pair.

For each active row in val_rules:
  - if it has a Check ID  -> resolve() it (Check or Logic, and
    Logic can chain into more Logic)
  - otherwise             -> evaluate its Rule Conditions rows

Every rule's outcome is written to validation_results.
"""

from . import loader
from .resolvers import resolve
from .conditions import evaluate_rule_conditions


def run_all_rules(entity: dict, id_entity: int, entity_hash: str, ctx: dict = None) -> list:
    """Run every active rule against one entity.

    `ctx` should be the SAME dict across all entities in one payload
    if you want batch-scoped Unique checks to see each other — see
    crud.process_payload for how it's threaded through.
    """
    if ctx is None:
        ctx = {}
    ctx["current_entity_hash"] = entity_hash

    rules = loader.get_active_rules()
    results = []

    for rule in rules:
        rule_id = rule.get("rule_id")
        check_id = rule.get("check_id")

        try:
            if check_id:
                passed = resolve(check_id, entity, ctx)
            else:
                passed = evaluate_rule_conditions(rule_id, entity, ctx)
            error = None
        except Exception as exc:  # a misconfigured rule shouldn't crash the batch
            passed = False
            error = str(exc)

        if passed:
            status = None
            description = None
        else:
            status = rule.get("on_exception_status")
            description = error or rule.get("on_exception_description") or rule.get("description")

        loader.insert_validation_result(
            id_entity, entity_hash, rule_id, passed, rule.get("severity"), status, description
        )

        results.append({
            "rule_id": rule_id,
            "passed": passed,
            "severity": rule.get("severity"),
            "status": status,
            "description": description,
        })

    return results
