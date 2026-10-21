"""
=====================================================
DATABASE LAYER
=====================================================
Owns the product tables and the small query helpers every other module goes
through. Connections come from product_sql_server.py (SQL Server on AWS RDS);
nothing else opens one.

Use `connect()` when several statements belong to one unit of work — it
commits on success, rolls back on error, and always closes. Every helper
below takes an optional `conn` so callers can share that one connection
instead of opening one per statement.

Product and entity data share one database, so every table of this program
starts with product_. Tables are created on startup when they are missing;
existing tables are never altered — see
product_sql_server.create_missing_tables.
"""

import contextlib
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import pandas as pd

from .product_sql_server import (
    CASE_INSENSITIVE,
    CREATED_AT,
    HASH,
    ID,
    JSON,
    KEY,
    TEXT,
    ProductTable,
    create_missing_tables,
    get_connection,
    plain,
    verify_tables,
)


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
        """Columns a posting supplies."""
        return ("product_hash",) + self.column_names + ("created_at",)

    @property
    def all_columns(self) -> tuple:
        return self.write_columns


# Where each column comes from in the product JSON is noted next to it; the
# mapping itself is product_parser._product_record.
PRODUCTS = ProductTables(
    latest="product_latest",
    history="product_history",
    columns=(
        ("primary_product_key", KEY),       # PrimaryProductKey
        ("parent_product_key", KEY),        # PrimaryProductKey of the level above; NULL at the top
        ("level", "INT"),                   # 0 = top of the hierarchy, 1 = its children, ...
        ("quantity", "FLOAT"),              # Quantity: how many of this product one parent holds
        ("product_type", KEY),              # ProductType.Name
        ("customer_code", KEY),             # Customer.Code
        ("customer_name", TEXT),            # Customer.Name
        ("customer_owner", TEXT),           # Customer.Owner
        ("product_definition_key", KEY),    # ProductDefinition.ProductDefinitionKey
        ("product_code", f"{KEY} {CASE_INSENSITIVE}"),  # its last part, e.g. FA003115 (shared by every level)
        ("description", TEXT),              # ProductDefinition.Description
        ("product_category", TEXT),         # ProductDefinition.ProductCategory.Name
        ("brand_family", TEXT),             # ProductDefinition.BrandFamily.Name
        ("parent_brand_family", TEXT),      # ProductDefinition.BrandFamily.ParentBrandFamily.Name
        ("child_count", "INT"),             # number of ChildProducts
        ("product_json", JSON),             # the product as posted, without ChildProducts
    ),
)

# One table per list inside a product — see PRODUCT_TABLES.
PRODUCT_DETAIL_TABLES = ("product_alternate_key", "product_unit", "product_property")


def _validation_tables() -> tuple:
    """The rule tables and the result tables."""
    return (
        ProductTable("product_val_rules", (
            ("rule_id", "NVARCHAR(200) NOT NULL PRIMARY KEY"),
            ("validation_type", TEXT),
            ("function_name", KEY),
            ("check_id", KEY),
            ("description", TEXT),
            ("severity", KEY),
            ("active", "NVARCHAR(10) DEFAULT 'Y'"),
            ("on_exception_status", "INT"),
            ("on_exception_description", TEXT),
        )),
        # No natural key: a rule's conditions are replaced as a set. Not a
        # FOREIGN KEY on rule_id, so conditions may be saved for a rule that
        # is posted later.
        ProductTable("product_val_rule_conditions", (
            ("id", ID),
            ("rule_id", "NVARCHAR(200)"),
            ("role", KEY),
            ("field_name", TEXT),
            ("operator", KEY),
            ("expected_value", TEXT),
            ("data_type", KEY),
            ("constraint_expr", TEXT),
            ("reference_source", KEY),
            ("logical_group", KEY),
        ), indexes=(("ix_product_val_rule_conditions_rule", "rule_id"),)),
        ProductTable("product_val_checks", (
            ("check_id", "NVARCHAR(200) NOT NULL PRIMARY KEY"),
            ("check_type", KEY),
            ("field_names", TEXT),
            ("check_value", KEY),
            ("expected_value", TEXT),
            ("reference_source", KEY),
            ("field_combine", "NVARCHAR(10) DEFAULT 'AND'"),
        )),
        ProductTable("product_val_logic", (
            ("logic_id", "NVARCHAR(200) NOT NULL PRIMARY KEY"),
            ("if_check_id", KEY),
            ("then_check_id", KEY),
            ("else_check_id", KEY),
        )),

        # One row per (posting, rule). product_id is the product_history row
        # that was validated.
        ProductTable("product_validation_results", (
            ("id", ID),
            ("product_hash", HASH),
            ("product_id", "BIGINT"),
            ("rule_id", "NVARCHAR(200)"),
            ("passed", "BIT"),
            ("severity", KEY),
            ("status", "INT"),
            ("description", TEXT),
            ("created_at", CREATED_AT),
        ), indexes=(("ix_product_validation_results_hash", "product_hash"),)),

        # The outcomes of the most recent run only, one row per product and
        # rule (see product_loader.insert_product_validation_results).
        ProductTable("product_latest_validation_rules", (
            ("product_hash", f"{HASH} NOT NULL"),
            ("primary_product_key", KEY),
            ("rule_id", "NVARCHAR(200) NOT NULL"),
            ("passed", "BIT"),
            ("severity", KEY),
            ("status", "INT"),
            ("description", TEXT),
            ("validated_at", "DATETIME2(0)"),
        ), constraints=("PRIMARY KEY (product_hash, rule_id)",)),

        # product_latest_validation_rules aggregated to one row per product.
        ProductTable("product_validation_summary", (
            ("product_hash", f"{HASH} NOT NULL PRIMARY KEY"),
            ("primary_product_key", KEY),
            ("rules_run", "INT"),
            ("rules_failed", "INT"),
            ("failed_rules", TEXT),   # ', '-separated rule_ids; NULL when none failed
            ("validated_at", "DATETIME2(0)"),
        )),
    )


# Every table this program uses, in creation order.
PRODUCT_TABLES = (
    ProductTable(PRODUCTS.history,
                 (("id", ID), ("product_hash", f"{HASH} NOT NULL")) + PRODUCTS.columns
                 + (("created_at", CREATED_AT),),
                 indexes=(("ix_product_history_hash", "product_hash, id"),)),
    ProductTable(PRODUCTS.latest,
                 (("id", ID), ("product_hash", f"{HASH} NOT NULL UNIQUE")) + PRODUCTS.columns
                 + (("created_at", CREATED_AT),),
                 indexes=(("ix_product_latest_key", "primary_product_key"),
                          ("ix_product_latest_parent", "parent_product_key"),
                          ("ix_product_latest_code", "product_code"))),

    # ---- The lists inside a product (newest posting only) ----
    # AlternateProductKeys: [{ProductKey, ProductKeyType, ProductKeyMode}]
    ProductTable("product_alternate_key", (
        ("id", ID),
        ("product_hash", f"{HASH} NOT NULL"),
        ("product_key", KEY),
        ("product_key_type", "INT"),
        ("product_key_mode", "INT"),
    ), indexes=(("ix_product_alternate_key_hash", "product_hash"),
                ("ix_product_alternate_key_key", "product_key"))),
    # Units: [{UnitTypeName, Quantity, BaseUnit}]
    ProductTable("product_unit", (
        ("id", ID),
        ("product_hash", f"{HASH} NOT NULL"),
        ("unit_type_name", KEY),
        ("quantity", "FLOAT"),
        ("base_unit", KEY),
    ), indexes=(("ix_product_unit_hash", "product_hash"),)),
    # ProductProperties ({"TPPN": "...", ...}) and
    # ProductDefinition.ProductDefinitionProperties ([{Name, Value}]) as
    # name/value rows; property_group is the JSON key a row came from.
    ProductTable("product_property", (
        ("id", ID),
        ("product_hash", f"{HASH} NOT NULL"),
        ("property_group", KEY),
        ("name", KEY),
        ("value", TEXT),
    ), indexes=(("ix_product_property_hash", "product_hash"),)),

    *_validation_tables(),

    # ---- Who to contact about a product ----
    # Linked directly to product_latest.product_code: one contact row covers
    # every level of that product (Shipping Case, Bundle, Pack). It may be
    # registered before the product is loaded. Codes match case-insensitively
    # (same collation as product_latest.product_code). The three UNIQUE
    # columns together stay under SQL Server's 1700-byte index key limit.
    ProductTable("product_responsible_contact", (
        ("id", ID),
        ("product_code", f"NVARCHAR(200) {CASE_INSENSITIVE} NOT NULL"),  # e.g. FA003115
        ("person_name", "NVARCHAR(200) NOT NULL"),                       # responsible person
        ("contact", "NVARCHAR(400) NOT NULL"),                           # email or Teams account
        ("created_at", CREATED_AT),
    ), constraints=("UNIQUE (product_code, person_name, contact)",)),
)


def table_definition(name: str) -> Optional[ProductTable]:
    """The ProductTable called `name` (any case), or None if it is not ours."""
    return next((t for t in PRODUCT_TABLES if t.name.lower() == str(name).lower()), None)


# ---------------------------------------------------------------------------
# Connections and queries
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def connect(conn=None):
    """Connection for one unit of work: commits on success, rolls back on
    error, always closes (back to the pool).

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


def _fetch(conn, sql: str, params: Sequence[Any]) -> tuple:
    """(column names, rows) of a query, values as the API shows them."""
    cursor = conn.execute(sql, tuple(params))
    names = [column[0] for column in cursor.description]
    return names, [[plain(value) for value in row] for row in cursor.fetchall()]


def query_rows(sql: str, params: Sequence[Any] = (), conn=None) -> list[dict]:
    """Rows as plain dicts.

    Deliberately not via pandas: SQL NULL arrives as a real None here, whereas
    pandas turns it into NaN — which is truthy, so every `if row.get(...)`
    in the rule engine would misfire on an empty column.
    """
    with connect(conn) as active:
        names, rows = _fetch(active, sql, params)
        return [dict(zip(names, row)) for row in rows]


def query_one(sql: str, params: Sequence[Any] = (), conn=None) -> Optional[dict]:
    """First row as a dict, or None."""
    rows = query_rows(sql, params, conn)
    return rows[0] if rows else None


def query_df(sql: str, params: Sequence[Any] = (), conn=None) -> pd.DataFrame:
    """Rows as a DataFrame — for the listing endpoints, which serialize it."""
    with connect(conn) as active:
        names, rows = _fetch(active, sql, params)
        return pd.DataFrame(rows, columns=names)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def initialize_product_database() -> list:
    """Check that the tables which already exist have every column the code
    writes (if not, stop with nothing changed), then create the missing ones.

    Safe to call on every startup. Returns the names of the tables created.
    """
    with connect() as conn:
        verify_tables(conn, PRODUCT_TABLES)
        return create_missing_tables(conn, PRODUCT_TABLES)
