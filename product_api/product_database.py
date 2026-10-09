"""
=====================================================
DATABASE LAYER
=====================================================
Owns the SQLite connection, the schema, and the small query helpers every
other module goes through. Nothing outside this file calls sqlite3.connect().

Use `connect()` when several statements belong to one unit of work — it
commits on success, rolls back on error, and always closes. Every helper
below takes an optional `conn` so callers can share that one connection
instead of opening one per statement.
"""

import contextlib
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import pandas as pd

from .product_config import PRODUCT_DB_NAME
from .product_parser import product_code_of


# ---------------------------------------------------------------------------
# Every level of a packaging hierarchy (Shipping Case, Bundle, Pack, ...) is a
# product, identified by its PrimaryProductKey — hashed into product_hash.
# Products are kept in a pair of tables with identical columns:
#
#   product_history  every posting, one row each. product_hash is NOT unique.
#   product_latest   the newest posting per product. product_hash is UNIQUE,
#                    so a repeat replaces the row instead of adding one.
#
# The lists inside a product live in PRODUCT_DETAIL_TABLES, keyed by
# product_hash. They hold the newest posting only; earlier versions can still
# be read from product_history.product_json.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductTables:
    """The history/latest table pair for products."""

    latest: str
    history: str
    columns: tuple  # (name, SQL type) in schema order (no id/product_hash/created_at)

    @property
    def column_names(self) -> tuple:
        return tuple(name for name, _sql_type in self.columns)

    @property
    def write_columns(self) -> tuple:
        """Columns a posting supplies; created_at is left to its DEFAULT."""
        return ("product_hash",) + self.column_names

    @property
    def all_columns(self) -> tuple:
        return self.write_columns + ("created_at",)


# Where each column comes from in the product JSON is noted next to it; the
# mapping itself is product_parser._product_record.
PRODUCTS = ProductTables(
    latest="product_latest",
    history="product_history",
    columns=(
        ("primary_product_key", "TEXT"),     # PrimaryProductKey
        ("parent_product_key", "TEXT"),      # PrimaryProductKey of the level above; NULL at the top
        ("level", "INTEGER"),                # 0 = top of the hierarchy, 1 = its children, ...
        ("quantity", "REAL"),                # Quantity: how many of this product one parent holds
        ("product_type", "TEXT"),            # ProductType.Name
        ("customer_code", "TEXT"),           # Customer.Code
        ("customer_name", "TEXT"),           # Customer.Name
        ("customer_owner", "TEXT"),          # Customer.Owner
        ("product_definition_key", "TEXT"),  # ProductDefinition.ProductDefinitionKey
        ("product_code", "TEXT"),            # its last part, e.g. FA003115 (shared by every level)
        ("description", "TEXT"),             # ProductDefinition.Description
        ("product_category", "TEXT"),        # ProductDefinition.ProductCategory.Name
        ("brand_family", "TEXT"),            # ProductDefinition.BrandFamily.Name
        ("parent_brand_family", "TEXT"),     # ProductDefinition.BrandFamily.ParentBrandFamily.Name
        ("child_count", "INTEGER"),          # number of ChildProducts
        ("product_json", "TEXT"),            # the product as posted, without ChildProducts
    ),
)

# One table per list inside a product — see _PRODUCT_DDL.
PRODUCT_DETAIL_TABLES = ("product_alternate_key", "product_unit", "product_property")


def get_connection() -> sqlite3.Connection:
    """Open a new connection to the SQLite database."""
    return sqlite3.connect(PRODUCT_DB_NAME)


@contextlib.contextmanager
def connect(conn: Optional[sqlite3.Connection] = None):
    """Connection for one unit of work: commits on success, rolls back on
    error, always closes.

    Pass an existing `conn` to join the caller's unit of work instead — it is
    then left open and uncommitted for the caller to finish.
    """
    if conn is not None:
        yield conn
        return

    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_rows(sql: str, params: Sequence[Any] = (),
               conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Rows as plain dicts, straight from sqlite3.

    Deliberately not via pandas: SQL NULL arrives as a real None here, whereas
    pandas turns it into NaN — which is truthy, so every `if row.get(...)`
    in the rule engine would misfire on an empty column.
    """
    with connect(conn) as active:
        previous, active.row_factory = active.row_factory, sqlite3.Row
        try:
            return [dict(row) for row in active.execute(sql, params)]
        finally:
            active.row_factory = previous


def query_one(sql: str, params: Sequence[Any] = (),
              conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """First row as a dict, or None."""
    rows = query_rows(sql, params, conn)
    return rows[0] if rows else None


def query_df(sql: str, params: Sequence[Any] = (),
             conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """Rows as a DataFrame — for the listing endpoints, which serialize it."""
    with connect(conn) as active:
        return pd.read_sql_query(sql, active, params=tuple(params))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _create_product_table(conn: sqlite3.Connection, table: str,
                          unique_hash: bool) -> None:
    hash_column = "TEXT NOT NULL UNIQUE" if unique_hash else "TEXT NOT NULL"
    data = ",\n            ".join(f"{name} {sql_type}" for name, sql_type in PRODUCTS.columns)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_hash {hash_column},
            {data},
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
    )


_PRODUCT_DDL = """
    -- Lookups by key, and a product's children.
    CREATE INDEX IF NOT EXISTS idx_product_latest_key
        ON product_latest (primary_product_key);
    CREATE INDEX IF NOT EXISTS idx_product_latest_parent
        ON product_latest (parent_product_key);
    CREATE INDEX IF NOT EXISTS idx_product_latest_code
        ON product_latest (product_code);

    -- =================================================
    -- Who to contact about a product
    -- =================================================

    -- Linked directly to product_latest.product_code: one contact row covers
    -- every level of that product (Shipping Case, Bundle, Pack). It may be
    -- registered before the product is loaded. Codes match case-insensitively.
    CREATE TABLE IF NOT EXISTS product_responsible_contact (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT NOT NULL COLLATE NOCASE,  -- e.g. FA003115
        person_name TEXT NOT NULL,                  -- responsible person
        contact TEXT NOT NULL,                      -- email or Teams account
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (product_code, person_name, contact)
        );

    -- =================================================
    -- The lists inside a product (newest posting only)
    -- =================================================

    -- AlternateProductKeys: [{ProductKey, ProductKeyType, ProductKeyMode}]
    CREATE TABLE IF NOT EXISTS product_alternate_key (
        id INTEGER PRIMARY KEY,
        product_hash TEXT NOT NULL,
        product_key TEXT,
        product_key_type INTEGER,
        product_key_mode INTEGER
        );
    CREATE INDEX IF NOT EXISTS idx_product_alternate_key_hash
        ON product_alternate_key (product_hash);
    CREATE INDEX IF NOT EXISTS idx_product_alternate_key_key
        ON product_alternate_key (product_key);

    -- Units: [{UnitTypeName, Quantity, BaseUnit}]
    CREATE TABLE IF NOT EXISTS product_unit (
        id INTEGER PRIMARY KEY,
        product_hash TEXT NOT NULL,
        unit_type_name TEXT,
        quantity REAL,
        base_unit TEXT
        );
    CREATE INDEX IF NOT EXISTS idx_product_unit_hash
        ON product_unit (product_hash);

    -- ProductProperties ({"TPPN": "...", ...}) and
    -- ProductDefinition.ProductDefinitionProperties ([{Name, Value}]) as
    -- name/value rows; property_group is the JSON key a row came from.
    CREATE TABLE IF NOT EXISTS product_property (
        id INTEGER PRIMARY KEY,
        product_hash TEXT NOT NULL,
        property_group TEXT,
        name TEXT,
        value TEXT
        );
    CREATE INDEX IF NOT EXISTS idx_product_property_hash
        ON product_property (product_hash);

    -- =================================================
    -- Table-driven rule engine (dynamic validation)
    -- =================================================

    CREATE TABLE IF NOT EXISTS product_val_rules (
        rule_id TEXT PRIMARY KEY,
        validation_type TEXT,
        function_name TEXT,
        check_id TEXT,
        description TEXT,
        severity TEXT,
        active TEXT DEFAULT 'Y',
        on_exception_status INTEGER,
        on_exception_description TEXT
        );

    CREATE TABLE IF NOT EXISTS product_val_rule_conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id TEXT,
        role TEXT,
        field_name TEXT,
        operator TEXT,
        expected_value TEXT,
        data_type TEXT,
        constraint_expr TEXT,
        reference_source TEXT,
        logical_group TEXT,
        FOREIGN KEY (rule_id) REFERENCES product_val_rules(rule_id)
        );

    CREATE TABLE IF NOT EXISTS product_val_checks (
        check_id TEXT PRIMARY KEY,
        check_type TEXT,
        field_names TEXT,
        check_value TEXT,
        expected_value TEXT,
        reference_source TEXT,
        field_combine TEXT DEFAULT 'AND'
        );

    CREATE TABLE IF NOT EXISTS product_val_logic (
        logic_id TEXT PRIMARY KEY,
        if_check_id TEXT,
        then_check_id TEXT,
        else_check_id TEXT
        );

    -- One row per (posting, rule). product_id is the product_history row
    -- that was validated.
    CREATE TABLE IF NOT EXISTS product_validation_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_hash TEXT,
        product_id INTEGER,
        rule_id TEXT,
        passed BOOLEAN,
        severity TEXT,
        status INTEGER,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

    -- The outcomes of the most recent run only, one row per product and rule.
    CREATE TABLE IF NOT EXISTS product_latest_validation_rules (
        product_hash TEXT NOT NULL,
        primary_product_key TEXT,
        rule_id TEXT NOT NULL,
        passed BOOLEAN,
        severity TEXT,
        status INTEGER,
        description TEXT,
        validated_at TIMESTAMP,
        PRIMARY KEY (product_hash, rule_id)
        );

    -- product_latest_validation_rules aggregated to one row per product.
    CREATE TABLE IF NOT EXISTS product_validation_summary (
        product_hash TEXT PRIMARY KEY,
        primary_product_key TEXT,
        rules_run INTEGER,
        rules_failed INTEGER,
        failed_rules TEXT,   -- ', '-separated rule_ids; NULL when none failed
        validated_at TIMESTAMP
        );
"""


# ---------------------------------------------------------------------------
# Derived result tables
# ---------------------------------------------------------------------------
# product_validation_results keeps every outcome ever written; the two tables
# below are derived from it and refreshed inside the same transaction as the
# insert, so a reader never sees them lag behind. Both are keyed by
# product_hash, never by a posting id — one product has one row per rule,
# however often it is posted.

_RESULT_RECORD = f"""
    FROM product_validation_results AS vr
    LEFT JOIN {PRODUCTS.history} AS p ON p.id = vr.product_id
"""

_TOUCHED = "(SELECT DISTINCT product_hash FROM product_validation_results WHERE id > ?)"


def _latest_upsert(where: str) -> str:
    """Copy result rows into product_latest_validation_rules, newest last so
    that a product posted twice in one batch ends up with its final outcome."""
    return f"""
        INSERT OR REPLACE INTO product_latest_validation_rules
            (product_hash, primary_product_key, rule_id, passed, severity,
             status, description, validated_at)
        SELECT vr.product_hash, p.primary_product_key, vr.rule_id, vr.passed,
               vr.severity, vr.status, vr.description, vr.created_at
        {_RESULT_RECORD}
        WHERE {where}
        ORDER BY vr.id
    """


def _summary_upsert(where: str) -> str:
    return f"""
        INSERT OR REPLACE INTO product_validation_summary
            (product_hash, primary_product_key, rules_run, rules_failed,
             failed_rules, validated_at)
        SELECT product_hash, primary_product_key,
               COUNT(*),
               SUM(CASE WHEN passed THEN 0 ELSE 1 END),
               GROUP_CONCAT(CASE WHEN passed THEN NULL ELSE rule_id END, ', '),
               MAX(validated_at)
        FROM product_latest_validation_rules
        WHERE {where}
        GROUP BY product_hash, primary_product_key
    """


def refresh_product_validation_tables(conn: sqlite3.Connection, since_id: int) -> None:
    """Re-derive product_latest_validation_rules and product_validation_summary
    for the products one batch touched.

    `since_id` is the highest product_validation_results.id from before the
    batch was written. The touched products' old rows are cleared first, so
    the tables hold that run and nothing else.
    """
    conn.execute(
        f"DELETE FROM product_latest_validation_rules WHERE product_hash IN {_TOUCHED}",
        (since_id,),
    )
    conn.execute(_latest_upsert("vr.id > ?"), (since_id,))
    # Every touched product now has latest rows, so REPLACE covers its summary.
    conn.execute(_summary_upsert(f"product_hash IN {_TOUCHED}"), (since_id,))


def _add_missing_columns(conn: sqlite3.Connection, table: str) -> None:
    """Add the PRODUCTS columns an older database lacks (product_code was
    added after the first version). Existing rows get NULL there."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in PRODUCTS.columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _fill_product_codes(conn: sqlite3.Connection, table: str) -> None:
    """product_code for rows stored before that column existed. Only rows
    that still lack one are read, so after the first run this is a no-op."""
    rows = conn.execute(
        f"SELECT id, product_definition_key FROM {table} "
        "WHERE product_code IS NULL AND product_definition_key IS NOT NULL"
    ).fetchall()
    conn.executemany(
        f"UPDATE {table} SET product_code = ? WHERE id = ?",
        [(product_code_of(key), row_id) for row_id, key in rows],
    )


def initialize_product_database() -> None:
    """Create every table and index that doesn't exist yet, and bring an
    older database up to date (see _add_missing_columns).

    Safe to call on every startup: everything is CREATE ... IF NOT EXISTS,
    and the column update only touches what is missing.
    """
    with connect() as conn:
        for table, unique_hash in ((PRODUCTS.history, False), (PRODUCTS.latest, True)):
            _create_product_table(conn, table, unique_hash=unique_hash)
            _add_missing_columns(conn, table)
            _fill_product_codes(conn, table)
        conn.executescript(_PRODUCT_DDL)
