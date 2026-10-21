"""
=====================================================
CRUD / BUSINESS LOGIC LAYER
=====================================================
All read/write operations on the product tables, plus the logic for
processing a payload, live here. Endpoints (routers) and the bulk loader
(product_load_data.py) only call these functions and never open a database
connection themselves.

Processing a payload is two steps:
  1. product_parser.parse_products()   the whole payload, no database yet
  2. store_and_validate_products()     ONE transaction for all of it:
       store_product()                   history always; latest insert or replace
       run_all_product_rules()           every active rule (rules loaded once)
       _replace_detail_rows()            alternate keys / units / properties, batched
       insert_product_validation_results()
     then, after the commit, one log file per product that failed a rule
     (product_validation_logger.write_product_failure_log)
"""

import json

import pandas as pd

from .product_database import (
    PRODUCT_DETAIL_TABLES,
    PRODUCTS,
    connect,
    query_df,
    query_rows,
)
from .product_parser import ProductNode, parse_products, product_rule_fields
from .product_sql_server import execute_many, upsert, utc_now
from .product_validation_engine.product_loader import (
    insert_product_validation_results,
    load_product_ruleset,
)
from .product_validation_engine.product_rule_engine import run_all_product_rules
from .product_validation_logger import write_product_failure_log


# ---------------------------------------------------
# STORING A POSTING  (history always, latest replaced)
# ---------------------------------------------------

def store_product(node: ProductNode, conn) -> dict:
    """Append the product to product_history and insert-or-replace it in
    product_latest.

    Returns the new history row's id and whether the product was new to
    product_latest (False = it replaced a row).
    """
    row = {
        "product_hash": node.product_hash,
        **{column: node.record.get(column) for column in PRODUCTS.column_names},
        "created_at": utc_now(),
    }

    history_id = conn.execute(
        f"INSERT INTO {PRODUCTS.history} ({', '.join(row)}) OUTPUT INSERTED.id "
        f"VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    ).fetchone()[0]

    # Replace the latest row of this product, or create it. upsert() locks the
    # key, so two requests posting the same new product cannot both insert it.
    is_new = upsert(conn, PRODUCTS.latest, ("product_hash",), row)

    return {"history_id": history_id, "is_new": is_new}


def _replace_detail_rows(nodes_by_hash: dict, conn) -> None:
    """Rewrite the detail tables for every product in the payload: its old
    rows out, the posted ones in. Three DELETE batches and three INSERT
    batches in total, however many products the payload holds."""
    hashes = [(product_hash,) for product_hash in nodes_by_hash]
    for table in PRODUCT_DETAIL_TABLES:
        execute_many(conn, f"DELETE FROM {table} WHERE product_hash = ?", hashes)

    nodes = nodes_by_hash.items()
    execute_many(
        conn,
        "INSERT INTO product_alternate_key "
        "(product_hash, product_key, product_key_type, product_key_mode) "
        "VALUES (?, ?, ?, ?)",
        [(h, *row) for h, node in nodes for row in node.alternate_keys],
    )
    execute_many(
        conn,
        "INSERT INTO product_unit "
        "(product_hash, unit_type_name, quantity, base_unit) "
        "VALUES (?, ?, ?, ?)",
        [(h, *row) for h, node in nodes for row in node.units],
    )
    execute_many(
        conn,
        "INSERT INTO product_property "
        "(product_hash, property_group, name, value) "
        "VALUES (?, ?, ?, ?)",
        [(h, *row) for h, node in nodes for row in node.properties],
    )


# ---------------------------------------------------
# PROCESSING A PAYLOAD
# ---------------------------------------------------

def process_products(payload, source: str = "payload") -> dict:
    """POST /products/process and /products/upload-file: one product
    hierarchy or a list of them.

    Raises ValueError before anything is written if the payload is
    malformed; `source` names the payload in that message.
    """
    return store_and_validate_products(parse_products(payload, source=source))


# What a failed-validation log says about the product it is for.
_LOG_PRODUCT_COLUMNS = ("primary_product_key", "product_code", "product_type",
                        "level", "parent_product_key", "description")


def _contacts_for(product_code, cache: dict, conn) -> list:
    """product_responsible_contacts_for, asked once per product_code: every
    level of a hierarchy shares its code."""
    if product_code not in cache:
        cache[product_code] = product_responsible_contacts_for(product_code, conn=conn)
    return cache[product_code]


def _failed_outcomes(outcomes: list) -> list:
    """The rule outcomes that failed, as they go into a log file."""
    return [
        {key: value for key, value in outcome.items() if key != "passed"}
        for outcome in outcomes
        if not outcome["passed"]
    ]


def store_and_validate_products(nodes: list) -> dict:
    """Store and validate parsed products in ONE transaction, so a payload is
    written completely or not at all. product_load_data.py calls this
    directly with the products of many files at once."""
    details = []

    # One connection for the whole payload, and one snapshot of the rule
    # tables — the engine then resolves Check/Logic ids in memory.
    with connect() as conn:
        ruleset = load_product_ruleset(conn=conn)

        # Shared across every product in this payload so that Unique(batch)
        # checks see siblings in the same call, and so a database-scoped
        # Unique check reuses this connection.
        ctx = {"conn": conn}
        result_rows = []
        latest = {}  # product_hash -> node; a product posted twice: last one wins
        pending_logs = []  # (detail, log) for each product that failed a rule
        contacts = {}      # product_code -> its contacts: one query per code

        for node in nodes:
            stored = store_product(node, conn)
            latest[node.product_hash] = node

            outcomes, rows = run_all_product_rules(
                node.fields,
                node.product_hash,
                product_id=stored["history_id"],
                ctx=ctx,
                ruleset=ruleset,
            )
            result_rows.extend(rows)
            errors = _failed_outcomes(outcomes)

            detail = {
                "status": "inserted",
                "id": stored["history_id"],
                "new_record": stored["is_new"],
                "product_hash": node.product_hash,
                "primary_product_key": node.record["primary_product_key"],
                "product_code": node.record["product_code"],
                "product_type": node.record["product_type"],
                "level": node.record["level"],
                "failed_rules": [error["rule_id"] for error in errors],
                "log_file": None,
            }
            details.append(detail)

            if errors:
                pending_logs.append((detail, {
                    "product_hash": node.product_hash,
                    "product": {
                        column: node.record[column]
                        for column in _LOG_PRODUCT_COLUMNS
                    },
                    "contacts": _contacts_for(node.record["product_code"], contacts, conn),
                    "run": {"product_id": stored["history_id"], "errors": errors},
                }))

        _replace_detail_rows(latest, conn)
        product_keys = {h: node.record["primary_product_key"] for h, node in latest.items()}
        insert_product_validation_results(result_rows, product_keys, conn=conn)

    # Committed: now one log file per failed product (see
    # product_validation_logger).
    for detail, log in pending_logs:
        detail["log_file"] = write_product_failure_log(**log)

    new_records = sum(1 for d in details if d["new_record"])
    return {
        "total": len(details),
        "hierarchies": sum(1 for d in details if d["level"] == 0),
        "inserted": len(details),
        "new_records": new_records,
        "updated_records": len(details) - new_records,
        "failed_validation": sum(1 for d in details if d["failed_rules"]),
        "details": details,
    }


# ---------------------------------------------------
# RESPONSIBLE CONTACTS (who to contact per product_code)
# ---------------------------------------------------

def insert_product_responsible_contacts(contacts: list) -> dict:
    """Insert ProductResponsibleContactIn records. An identical row (same
    product_code, person_name and contact) already in the table is skipped
    rather than duplicated."""
    details = []
    inserted = 0

    with connect() as conn:
        for contact in contacts:
            row = contact.model_dump()
            values = (row["product_code"], row["person_name"], row["contact"])
            # Insert unless the same three values are there already. The lock
            # hint holds that check until commit, so a parallel request
            # cannot insert the same row in between.
            new_row = conn.execute(
                """
                INSERT INTO product_responsible_contact
                (product_code, person_name, contact)
                OUTPUT INSERTED.id
                SELECT ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM product_responsible_contact WITH (UPDLOCK, HOLDLOCK)
                    WHERE product_code = ? AND person_name = ? AND contact = ?
                )
                """,
                values + values,
            ).fetchone()

            if new_row:
                status, row_id = "inserted", new_row[0]
                inserted += 1
            else:
                status = "skipped"
                row_id = conn.execute(
                    """
                    SELECT id FROM product_responsible_contact
                    WHERE product_code = ? AND person_name = ? AND contact = ?
                    """,
                    values,
                ).fetchone()[0]

            details.append({"status": status, "id": row_id, **row})

    return {
        "total": len(contacts),
        "inserted": inserted,
        "skipped": len(contacts) - inserted,
        "details": details,
    }


def get_all_product_responsible_contacts() -> pd.DataFrame:
    """Every contact, with how many products in product_latest carry its
    product_code — 0 means the product is not loaded yet, or the code has a
    typo."""
    return query_df(
        f"""
        SELECT c.id, c.product_code, c.person_name, c.contact,
               (SELECT COUNT(*) FROM {PRODUCTS.latest} AS p
                WHERE c.product_code = p.product_code) AS matching_products,
               c.created_at
        FROM product_responsible_contact AS c
        ORDER BY c.id
        """
    )


def product_responsible_contacts_for(product_code, conn=None) -> list:
    """Who to contact about one product: the contacts for its product_code
    (matched case-insensitively — see the column's collation)."""
    return query_rows(
        """
        SELECT person_name, contact
        FROM product_responsible_contact
        WHERE product_code = ?
        ORDER BY id
        """,
        (product_code,),
        conn=conn,
    )


# ---------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------

# product_json is left out of the listings: it repeats every column and is
# long. find_products_by_key shows it, flattened, as rule_fields.
_LISTING_COLUMNS = ", ".join(
    column for column in ("id",) + PRODUCTS.all_columns if column != "product_json"
)


def get_all_products() -> pd.DataFrame:
    """`product_latest`: the newest posting per PrimaryProductKey, in posted
    order (each hierarchy top-down)."""
    return query_df(f"SELECT {_LISTING_COLUMNS} FROM {PRODUCTS.latest} ORDER BY id")


def get_all_historical_products() -> pd.DataFrame:
    """`product_history`: every posting. Its id is the product_id used in
    product_validation_results."""
    return query_df(f"SELECT {_LISTING_COLUMNS} FROM {PRODUCTS.history} ORDER BY id")


def find_products_by_key(key: str) -> list:
    """Products whose PrimaryProductKey, or any AlternateProductKey (GTIN,
    MSKU, ...), equals `key`, each with its detail lists, direct children,
    responsible contacts, and the exact fields the rule engine evaluates
    for it."""
    with connect() as conn:
        matches = query_rows(
            f"""
            SELECT 'PrimaryProductKey' AS matched_on, p.*
            FROM {PRODUCTS.latest} AS p
            WHERE p.primary_product_key = ?
            UNION
            SELECT 'AlternateProductKey' AS matched_on, p.*
            FROM {PRODUCTS.latest} AS p
            JOIN product_alternate_key AS k ON k.product_hash = p.product_hash
            WHERE k.product_key = ?
            ORDER BY id
            """,
            (key, key),
            conn=conn,
        )
        return [_product_detail(row, conn) for row in matches]


def _product_detail(row: dict, conn) -> dict:
    product_hash = row["product_hash"]
    posted = json.loads(row.pop("product_json") or "{}")
    return {
        "matched_on": row.pop("matched_on"),
        "product": row,
        "alternate_keys": query_rows(
            "SELECT product_key, product_key_type, product_key_mode "
            "FROM product_alternate_key WHERE product_hash = ? ORDER BY id",
            (product_hash,), conn=conn,
        ),
        "units": query_rows(
            "SELECT unit_type_name, quantity, base_unit "
            "FROM product_unit WHERE product_hash = ? ORDER BY id",
            (product_hash,), conn=conn,
        ),
        "properties": query_rows(
            "SELECT property_group, name, value "
            "FROM product_property WHERE product_hash = ? ORDER BY id",
            (product_hash,), conn=conn,
        ),
        "children": query_rows(
            f"SELECT primary_product_key, product_type, quantity "
            f"FROM {PRODUCTS.latest} WHERE parent_product_key = ? ORDER BY id",
            (row["primary_product_key"],), conn=conn,
        ),
        "responsible_contacts": product_responsible_contacts_for(
            row["product_code"], conn=conn
        ),
        "rule_fields": product_rule_fields(
            posted, row["parent_product_key"], row["level"], row["child_count"]
        ),
    }


def get_product_validation_results() -> pd.DataFrame:
    """`product_validation_results`: every outcome ever written, one row per
    (posting, rule). A product posted five times appears five times."""
    return query_df(
        f"""
        SELECT
            vr.product_hash,
            p.primary_product_key,
            p.product_type,
            vr.product_id,
            vr.rule_id,
            vr.passed,
            vr.severity,
            vr.status,
            vr.description,
            vr.created_at
        FROM product_validation_results AS vr
        LEFT JOIN {PRODUCTS.history} AS p ON p.id = vr.product_id
        ORDER BY vr.id
        """
    )


def get_product_latest_validation() -> pd.DataFrame:
    """`product_latest_validation_rules`: the most recent run per product and
    rule."""
    return query_df(
        f"""
        SELECT v.product_hash, v.primary_product_key, p.product_type, v.rule_id,
               v.passed, v.severity, v.status, v.description, v.validated_at
        FROM product_latest_validation_rules AS v
        LEFT JOIN {PRODUCTS.latest} AS p ON p.product_hash = v.product_hash
        ORDER BY v.primary_product_key, v.rule_id
        """
    )


def get_product_validation_summary() -> pd.DataFrame:
    """`product_validation_summary`: one row per product — how many rules
    ran, how many failed, and which ones. Failures first."""
    return query_df(
        f"""
        SELECT s.product_hash, s.primary_product_key, p.product_type,
               p.description, s.rules_run, s.rules_failed, s.failed_rules,
               s.validated_at
        FROM product_validation_summary AS s
        LEFT JOIN {PRODUCTS.latest} AS p ON p.product_hash = s.product_hash
        ORDER BY s.rules_failed DESC, s.primary_product_key
        """
    )
