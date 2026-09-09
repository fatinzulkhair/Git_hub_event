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


class _CIEntity(dict):
    """A dict whose ``get`` / ``in`` / ``[]`` also resolve case-insensitively.

    Each entity is validated as its ``entity_json`` merged with the matching
    ``entities_location`` row (see ``crud.process_payload``). Those two
    sources use different column casing — ``EOID`` / ``SGLN`` from the entity,
    ``country`` / ``city`` from the location table — so a rule must be able to
    name a field however it reads naturally. Field lookups fold case, e.g.
    ``country``, ``Country`` and ``COUNTRY`` all hit the same value. An
    exact-case key always wins over a folded match.
    """

    def __init__(self, data):
        super().__init__(data)
        self._folded = {}
        for key in self:
            if isinstance(key, str):
                self._folded.setdefault(key.casefold(), key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        return isinstance(key, str) and key.casefold() in self._folded

    def __getitem__(self, key):
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str):
            real = self._folded.get(key.casefold())
            if real is not None:
                return super().__getitem__(real)
        raise KeyError(key)


def run_all_rules(entity: dict, id_entity: int, entity_hash: str, ctx: dict = None) -> list:
    """Run every active rule against one entity.

    `ctx` should be the SAME dict across all entities in one payload
    if you want batch-scoped Unique checks to see each other — see
    crud.process_payload for how it's threaded through.
    """
    if ctx is None:
        ctx = {}
    ctx["current_entity_hash"] = entity_hash

    # Rules address fields case-insensitively (entity + joined location
    # columns use mixed casing) — see _CIEntity.
    entity = _CIEntity(entity)

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
