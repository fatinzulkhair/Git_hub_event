"""
=====================================================
validation_engine: /loader.py
=====================================================
Reads the table-driven rule definitions (entities_val_rules,
entities_val_rule_conditions, entities_val_checks, entities_val_logic) out of
the database, and writes results back.

The four tables are read together into a RuleSet — one connection, four
queries, per payload — and the engine then resolves Check/Logic ids against
that in-memory snapshot. Rules are still never cached beyond a single
request, so an edit through POST /validation-rules applies to the very next
payload without a restart.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..database import connect, query_rows
from ..sql_server import execute_many, utc_now

DESCRIPTION_LIMIT = 4000  # the description columns are NVARCHAR(4000)


@dataclass
class RuleSet:
    """One payload's snapshot of the rule tables."""

    rules: list = field(default_factory=list)              # active entities_val_rules rows
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
    rules = query_rows(
        "SELECT * FROM entities_val_rules WHERE active = 'Y' ORDER BY rule_id", conn=conn
    )

    conditions: dict = {}
    for row in query_rows(
        "SELECT * FROM entities_val_rule_conditions ORDER BY id", conn=conn
    ):
        conditions.setdefault(row.get("rule_id"), []).append(row)

    return RuleSet(
        rules=rules,
        conditions=conditions,
        checks={r["check_id"]: r for r in query_rows(
            "SELECT * FROM entities_val_checks", conn=conn)},
        logic={r["logic_id"]: r for r in query_rows(
            "SELECT * FROM entities_val_logic", conn=conn)},
    )


def insert_validation_results(rows: list, record_codes: dict, conn=None) -> None:
    """Write one batch of (record, rule) outcomes.

    `rows` are (entity_hash, entity_id, location_id, source, rule_id, passed,
    severity, status, description) tuples, in the order they were evaluated —
    see rule_engine.run_all_rules. `record_codes` maps each entity_hash to its
    (systemCode, businessEntityCode).

    The full history goes to entities_validation_results. The two derived
    tables are then rebuilt for the records in this batch, in the same
    transaction, so they hold exactly this run:
      entities_latest_validation_rules  one row per record and rule
      entities_validation_summary       one row per record
    A record posted twice in one batch keeps its last outcome.
    """
    rows = [row[:8] + (_clip(row[8]),) for row in rows]
    if not rows:
        return
    validated_at = utc_now()

    latest = {}  # (entity_hash, rule_id) -> row; later rows replace earlier ones
    for row in rows:
        latest[(row[0], row[4])] = row
    hashes = sorted({entity_hash for entity_hash, _rule_id in latest})

    summary = {}  # entity_hash -> [rules run, failed rule_ids]
    for (entity_hash, rule_id), row in sorted(latest.items()):
        counts = summary.setdefault(entity_hash, [0, []])
        counts[0] += 1
        if not row[5]:
            counts[1].append(rule_id)

    with connect(conn) as active:
        execute_many(
            active,
            """
            INSERT INTO entities_validation_results
            (entity_hash, entity_id, location_id, source, rule_id,
             passed, severity, status, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row + (validated_at,) for row in rows],
        )

        for table in ("entities_latest_validation_rules", "entities_validation_summary"):
            execute_many(active, f"DELETE FROM {table} WHERE entity_hash = ?",
                         [(entity_hash,) for entity_hash in hashes])

        execute_many(
            active,
            """
            INSERT INTO entities_latest_validation_rules
            (entity_hash, systemCode, businessEntityCode, rule_id, source,
             passed, severity, status, description, validated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (entity_hash, *record_codes.get(entity_hash, (None, None)), rule_id,
                 source, passed, severity, status, description, validated_at)
                for (entity_hash, rule_id), (_h, _e, _l, source, _r, passed, severity,
                                             status, description) in latest.items()
            ],
        )
        execute_many(
            active,
            """
            INSERT INTO entities_validation_summary
            (entity_hash, systemCode, businessEntityCode, rules_run,
             rules_failed, failed_rules, validated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (entity_hash, *record_codes.get(entity_hash, (None, None)), rules_run,
                 len(failed), _clip(", ".join(failed)) or None, validated_at)
                for entity_hash, (rules_run, failed) in summary.items()
            ],
        )


def _clip(text):
    """Text cut to fit an NVARCHAR(4000) column."""
    if text is None or len(text) <= DESCRIPTION_LIMIT:
        return text
    return text[:DESCRIPTION_LIMIT - 3] + "..."
