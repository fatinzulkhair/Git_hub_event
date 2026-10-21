"""
=====================================================
product_validation_engine: /product_loader.py
=====================================================
Reads the table-driven rule definitions (product_val_rules,
product_val_rule_conditions, product_val_checks, product_val_logic) out of
the database, and writes results back.

The four tables are read together into a ProductRuleSet — one connection,
four queries, per payload — and the engine then resolves Check/Logic ids
against that in-memory snapshot. Rules are still never cached beyond a single
request, so an edit through POST /product-validation-rules applies to the very
next payload without a restart.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..product_database import connect, query_rows
from ..product_sql_server import execute_many, utc_now

DESCRIPTION_LIMIT = 4000  # the description columns are NVARCHAR(4000)


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
    for row in query_rows("SELECT * FROM product_val_rule_conditions ORDER BY id", conn=conn):
        conditions.setdefault(row.get("rule_id"), []).append(row)

    return ProductRuleSet(
        rules=rules,
        conditions=conditions,
        checks={r["check_id"]: r for r in query_rows("SELECT * FROM product_val_checks", conn=conn)},
        logic={r["logic_id"]: r for r in query_rows("SELECT * FROM product_val_logic", conn=conn)},
    )


def insert_product_validation_results(rows: list, product_keys: dict, conn=None) -> None:
    """Write one batch of (product, rule) outcomes.

    `rows` are (product_hash, product_id, rule_id, passed, severity, status,
    description) tuples, in the order they were evaluated — see
    product_rule_engine.run_all_product_rules. `product_keys` maps each
    product_hash to its PrimaryProductKey.

    The full history goes to product_validation_results. The two derived
    tables are then rebuilt for the products in this batch, in the same
    transaction, so they hold exactly this run:
      product_latest_validation_rules  one row per product and rule
      product_validation_summary       one row per product
    A product posted twice in one batch keeps its last outcome.
    """
    rows = [row[:6] + (_clip(row[6]),) for row in rows]
    if not rows:
        return
    validated_at = utc_now()

    latest = {}  # (product_hash, rule_id) -> row; later rows replace earlier ones
    for row in rows:
        latest[(row[0], row[2])] = row
    hashes = sorted({product_hash for product_hash, _rule_id in latest})

    summary = {}  # product_hash -> [rules run, failed rule_ids]
    for (product_hash, rule_id), row in sorted(latest.items()):
        counts = summary.setdefault(product_hash, [0, []])
        counts[0] += 1
        if not row[3]:
            counts[1].append(rule_id)

    with connect(conn) as active:
        execute_many(
            active,
            """
            INSERT INTO product_validation_results
            (product_hash, product_id, rule_id, passed, severity, status,
             description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row + (validated_at,) for row in rows],
        )

        for table in ("product_latest_validation_rules", "product_validation_summary"):
            execute_many(active, f"DELETE FROM {table} WHERE product_hash = ?",
                         [(product_hash,) for product_hash in hashes])

        execute_many(
            active,
            """
            INSERT INTO product_latest_validation_rules
            (product_hash, primary_product_key, rule_id, passed, severity,
             status, description, validated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (product_hash, product_keys.get(product_hash), rule_id, passed,
                 severity, status, description, validated_at)
                for (product_hash, rule_id), (_h, _id, _r, passed, severity, status,
                                              description) in latest.items()
            ],
        )
        execute_many(
            active,
            """
            INSERT INTO product_validation_summary
            (product_hash, primary_product_key, rules_run, rules_failed,
             failed_rules, validated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (product_hash, product_keys.get(product_hash), rules_run, len(failed),
                 _clip(", ".join(failed)) or None, validated_at)
                for product_hash, (rules_run, failed) in summary.items()
            ],
        )


def _clip(text):
    """Text cut to fit an NVARCHAR(4000) column."""
    if text is None or len(text) <= DESCRIPTION_LIMIT:
        return text
    return text[:DESCRIPTION_LIMIT - 3] + "..."
