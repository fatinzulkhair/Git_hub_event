"""
=====================================================
validation_engine: /rule_engine.py
=====================================================
Entry point for the rule engine: runs every active rule against one record.
All rule logic is user-defined and lives in the val_* tables — there is no
hardcoded validation left in Python.

A record is the FULL OUTER JOIN of an entity and a location on
(systemCode, businessEntityCode); either side may be missing. Rules see one
flat, case-insensitive view of both. On top of the user's rules the engine
always emits JOIN-COMPLETENESS, which reports a side that isn't there yet.

For each active row in val_rules:
  - if it has a Check ID  -> resolve() it (Check or Logic, and Logic can
    chain into more Logic)
  - otherwise             -> evaluate its Rule Conditions rows

Outcomes are returned as validation_results rows for the caller to write in
one batch (see crud._process_postings).
"""

from .conditions import evaluate_rule_conditions
from .loader import RuleSet, load_ruleset
from .resolvers import resolve
from .values import CaseInsensitiveRecord

# Reserved rule_id for the built-in join check. A val_rules row with this id
# is ignored so the built-in stays the single source of the outcome.
JOIN_RULE_ID = "JOIN-COMPLETENESS"
JOIN_SEVERITY = "Warning"
STATUS_MISSING_ENTITY = -11
STATUS_MISSING_LOCATION = -12


def join_outcome(entity_id, location_id) -> tuple:
    """(passed, status, description) for the built-in join check."""
    if entity_id is None:
        return False, STATUS_MISSING_ENTITY, (
            "entity data is missing for this systemCode + businessEntityCode "
            "(location was posted first)"
        )
    if location_id is None:
        return False, STATUS_MISSING_LOCATION, (
            "location data is missing for this systemCode + businessEntityCode "
            "(entity has no location yet)"
        )
    return True, None, None


def run_all_rules(record: dict, entity_hash: str, entity_id=None,
                  location_id=None, source: str = None,
                  ctx: dict = None, ruleset: RuleSet = None) -> tuple:
    """Run the join check plus every active rule against one joined record.

    Returns (results, rows): `results` describes each outcome, `rows` are the
    validation_results tuples to insert.

    `ctx` should be the SAME dict across all records in one payload so that
    batch-scoped Unique checks see each other — see crud._process_postings for
    how it, and the shared `ruleset`, are threaded through. Omitting `ruleset`
    loads one for this record alone.
    """
    if ctx is None:
        ctx = {}
    ctx["current_entity_hash"] = entity_hash

    if ruleset is None:
        ruleset = load_ruleset()

    record = CaseInsensitiveRecord(record)
    results, rows = [], []

    def add(rule_id, passed, severity, status, description):
        rows.append((entity_hash, entity_id, location_id, source, rule_id,
                     passed, severity, status, description))
        results.append({
            "rule_id": rule_id,
            "passed": passed,
            "severity": severity,
            "status": status,
            "description": description,
        })

    joined, join_status, join_description = join_outcome(entity_id, location_id)
    add(JOIN_RULE_ID, joined, JOIN_SEVERITY, join_status, join_description)

    for rule in ruleset.rules:
        rule_id = rule.get("rule_id")
        if rule_id == JOIN_RULE_ID:
            continue  # reserved for the built-in check above

        check_id = rule.get("check_id")
        try:
            if check_id:
                passed = resolve(check_id, record, ctx, ruleset)
            else:
                passed = evaluate_rule_conditions(rule_id, record, ctx, ruleset)
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

        add(rule_id, passed, rule.get("severity"), status, description)

    return results, rows
