"""
=====================================================
product_validation_engine: /product_loader.py
=====================================================
Reads the table-driven rule definitions (product_val_rules,
product_val_rule_conditions, product_val_checks, product_val_logic) out of
SQLite, and writes results back.

The four tables are read together into a ProductRuleSet — one connection,
four queries, per payload — and the engine then resolves Check/Logic ids
against that in-memory snapshot. Rules are still never cached beyond a single
request, so an edit through POST /product-validation-rules applies to the very
next payload without a restart.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..product_database import connect, query_rows, refresh_product_validation_tables


@dataclass
class ProductRuleSet:
    """One payload's snapshot of the rule tables."""

    rules: list = field(default_factory=list)              # active product_val_rules rows
    conditions: dict = field(default_factory=dict)         # rule_id  -> [rows]
    checks: dict = field(default_factory=dict)             # check_id -> row
    logic: dict = field(default_factory=dict)              # logic_id -> row

    def conditions_for(self, rule_id: str) -> list:
        return self.conditions.get(rule_id, [])

    def check(self, check_id: str) -> Optional[dict]:
        return self.checks.get(check_id)

    def logic_for(self, logic_id: str) -> Optional[dict]:
        return self.logic.get(logic_id)


def load_product_ruleset(conn=None) -> ProductRuleSet:
    """Read every active rule and all of its supporting rows."""
    rules = query_rows(
        "SELECT * FROM product_val_rules WHERE active = 'Y' ORDER BY rule_id", conn=conn
    )

    conditions: dict = {}
    for row in query_rows("SELECT * FROM product_val_rule_conditions", conn=conn):
        conditions.setdefault(row.get("rule_id"), []).append(row)

    return ProductRuleSet(
        rules=rules,
        conditions=conditions,
        checks={r["check_id"]: r for r in query_rows("SELECT * FROM product_val_checks", conn=conn)},
        logic={r["logic_id"]: r for r in query_rows("SELECT * FROM product_val_logic", conn=conn)},
    )


def insert_product_validation_results(rows: list, conn=None) -> None:
    """Write one batch of (product, rule) outcomes.

    `rows` are (product_hash, product_id, rule_id, passed, severity, status,
    description) tuples — see product_rule_engine.run_all_product_rules.

    The full history goes to product_validation_results;
    product_latest_validation_rules and product_validation_summary are then
    re-derived for the products this batch touched, in the same transaction.
    """
    rows = list(rows)
    if not rows:
        return

    with connect(conn) as active:
        since_id = active.execute(
            "SELECT COALESCE(MAX(id), 0) FROM product_validation_results"
        ).fetchone()[0]
        active.executemany(
            """
            INSERT INTO product_validation_results
            (product_hash, product_id, rule_id, passed, severity, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        refresh_product_validation_tables(active, since_id=since_id)
