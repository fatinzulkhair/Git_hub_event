"""
=====================================================
validation.py
=====================================================
Dynamic, table-driven replacement for the old hardcoded
romanizecheck()/eu_detect() pair. All rule logic now lives in
the val_rules / val_rule_conditions / val_checks / val_logic
tables — see validation_engine/rule_engine.py.

The legacy Location.py / romanize.py modules are kept in place
(unused by this file) only so the old entities_location /
Location_validation / romanize tables still work if something
else still reads them; new rules should be added via the
val_* tables instead of new Python code.
"""

from .validation_engine.rule_engine import run_all_rules


def validation_rules_engine(entity: dict, id_entity: str, entity_hash: str, ctx: dict = None):
    """Run every active rule (from val_rules) against one entity."""
    return run_all_rules(entity, id_entity, entity_hash, ctx=ctx)
