"""
=====================================================
validation_engine: /loader.py
=====================================================
Reads the table-driven rule definitions (val_rules, val_rule_conditions,
val_checks, val_logic) out of SQLite, and writes results back.

The four tables are read together into a RuleSet — one connection, four
queries, per payload — and the engine then resolves Check/Logic ids against
that in-memory snapshot. Rules are still never cached beyond a single
request, so an edit through POST /validation-rules applies to the very next
payload without a restart.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..database import connect, query_rows, refresh_validation_tables


@dataclass
class RuleSet:
    """One payload's snapshot of the rule tables."""

    rules: list = field(default_factory=list)              # active val_rules rows
    conditions: dict = field(default_factory=dict)         # rule_id  -> [rows]
    checks: dict = field(default_factory=dict)             # check_id -> row
    logic: dict = field(default_factory=dict)              # logic_id -> row

    def conditions_for(self, rule_id: str) -> list:
        return self.conditions.get(rule_id, [])

    def check(self, check_id: str) -> Optional[dict]:
        return self.checks.get(check_id)

    def logic_for(self, logic_id: str) -> Optional[dict]:
        return self.logic.get(logic_id)


def load_ruleset(conn=None) -> RuleSet:
    """Read every active rule and all of its supporting rows."""
    rules = query_rows("SELECT * FROM val_rules WHERE active = 'Y'", conn=conn)

    conditions: dict = {}
    for row in query_rows("SELECT * FROM val_rule_conditions", conn=conn):
        conditions.setdefault(row.get("rule_id"), []).append(row)

    return RuleSet(
        rules=rules,
        conditions=conditions,
        checks={r["check_id"]: r for r in query_rows("SELECT * FROM val_checks", conn=conn)},
        logic={r["logic_id"]: r for r in query_rows("SELECT * FROM val_logic", conn=conn)},
    )


def insert_validation_results(rows: list, conn=None) -> None:
    """Write one batch of (record, rule) outcomes.

    `rows` are (entity_hash, entity_id, location_id, source, rule_id, passed,
    severity, status, description) tuples — see rule_engine.run_all_rules.

    The full history goes to validation_results; latest_validation_rules and
    validation_summary are then re-derived for the records this batch touched,
    in the same transaction.
    """
    rows = list(rows)
    if not rows:
        return

    with connect(conn) as active:
        since_id = active.execute(
            "SELECT COALESCE(MAX(id), 0) FROM validation_results"
        ).fetchone()[0]
        active.executemany(
            """
            INSERT INTO validation_results
            (entity_hash, entity_id, location_id, source, rule_id,
             passed, severity, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        refresh_validation_tables(active, since_id=since_id)
