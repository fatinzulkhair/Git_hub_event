"""
=====================================================
DATABASE LAYER
=====================================================
Owns this program's tables and the small query helpers every other module
goes through. Connections come from sql_server.py (SQL Server on AWS RDS);
nothing else opens one.

Use `connect()` when several statements belong to one unit of work — it
commits on success, rolls back on error, and always closes. Every helper
below takes an optional `conn` so callers can share that one connection
instead of opening one per statement.

Entity and product data share one database, so every table of this program
has "entities" in its name (the product program's tables start with
product_). Tables are created on startup when they are missing; existing
tables are never altered — see sql_server.create_missing_tables.
"""

import contextlib
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import pandas as pd

from .sql_server import (
    CASE_INSENSITIVE,
    CREATED_AT,
    HASH,
    ID,
    JSON,
    KEY,
    TEXT,
    Table,
    create_missing_tables,
    get_connection,
    plain,
    verify_tables,
)


# ---------------------------------------------------------------------------
# Entities and locations are both records identified by
# (systemCode, businessEntityCode) — hashed into entity_hash, which is also
# what joins the two. Each is kept in a pair of tables with identical columns:
#
#   <history>  every posting, one row each. entity_hash is NOT unique.
#   <latest>   the newest posting per record. entity_hash is UNIQUE, so a
#              repeat replaces the row instead of adding one.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PairTables:
    """The history/latest table pair for one kind of record."""

    latest: str
    history: str
    columns: tuple  # (name, SQL type) in schema order (no id/entity_hash/created_at)

    @property
    def column_names(self) -> tuple:
        return tuple(name for name, _sql_type in self.columns)

    @property
    def write_columns(self) -> tuple:
        """Columns a posting supplies."""
        return ("entity_hash",) + self.column_names + ("created_at",)

    @property
    def all_columns(self) -> tuple:
        return self.write_columns


ENTITIES = PairTables(
    latest="entities",
    history="historical_entities",
    columns=(
        ("systemCode", KEY),
        ("businessEntityCode", KEY),
        ("EOID", KEY),
        ("FID", KEY),
        ("UKEOID", KEY),
        ("UKFID", KEY),
        ("SGLN", KEY),
        ("entity_json", JSON),  # the payload as posted
    ),
)

LOCATIONS = PairTables(
    latest="entities_location",
    history="entities_historical_location",
    columns=(
        ("systemCode", KEY),
        ("businessEntityCode", KEY),
        ("name", TEXT),
        ("type", KEY),
        ("alternate_code", KEY),
        ("country", KEY),
        ("city", TEXT),
        ("zip", KEY),
    ),
)

PAIR_TABLES = (ENTITIES, LOCATIONS)


def _pair(spec: PairTables) -> tuple:
    """The two Table definitions of a history/latest pair."""
    data = spec.columns + (("created_at", CREATED_AT),)
    return (
        Table(spec.history, (("id", ID), ("entity_hash", f"{HASH} NOT NULL")) + data,
              indexes=((f"ix_{spec.history}_hash", "entity_hash, id"),)),
        Table(spec.latest, (("id", ID), ("entity_hash", f"{HASH} NOT NULL UNIQUE")) + data),
    )


# Every table this program uses, in creation order.
TABLES = (
    *_pair(ENTITIES),
    *_pair(LOCATIONS),

    # ---- Table-driven rule engine (dynamic validation) ----
    Table("entities_val_rules", (
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
    # FOREIGN KEY on rule_id, so (as before) conditions may be saved for a
    # rule that is posted later.
    Table("entities_val_rule_conditions", (
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
    ), indexes=(("ix_entities_val_rule_conditions_rule", "rule_id"),)),
    Table("entities_val_checks", (
        ("check_id", "NVARCHAR(200) NOT NULL PRIMARY KEY"),
        ("check_type", KEY),
        ("field_names", TEXT),
        ("check_value", KEY),
        ("expected_value", TEXT),
        ("reference_source", KEY),
        ("field_combine", "NVARCHAR(10) DEFAULT 'AND'"),
    )),
    Table("entities_val_logic", (
        ("logic_id", "NVARCHAR(200) NOT NULL PRIMARY KEY"),
        ("if_check_id", KEY),
        ("then_check_id", KEY),
        ("else_check_id", KEY),
    )),

    # One row per (record, rule, run). entity_id / location_id point at the
    # two postings that were joined; either is NULL when that side does not
    # exist yet, which is what JOIN-COMPLETENESS reports on.
    Table("entities_validation_results", (
        ("id", ID),
        ("entity_hash", HASH),
        ("entity_id", "BIGINT"),
        ("location_id", "BIGINT"),
        ("source", KEY),
        ("rule_id", "NVARCHAR(200)"),
        ("passed", "BIT"),
        ("severity", KEY),
        ("status", "INT"),
        ("description", TEXT),
        ("created_at", CREATED_AT),
    ), indexes=(("ix_entities_validation_results_hash", "entity_hash"),)),

    # The outcomes of the most recent run only, one row per record and rule
    # (see validation_engine.loader.insert_validation_results).
    Table("entities_latest_validation_rules", (
        ("entity_hash", f"{HASH} NOT NULL"),
        ("systemCode", KEY),
        ("businessEntityCode", KEY),
        ("rule_id", "NVARCHAR(200) NOT NULL"),
        ("source", KEY),
        ("passed", "BIT"),
        ("severity", KEY),
        ("status", "INT"),
        ("description", TEXT),
        ("validated_at", "DATETIME2(0)"),
    ), constraints=("PRIMARY KEY (entity_hash, rule_id)",)),

    # entities_latest_validation_rules aggregated to one row per record.
    Table("entities_validation_summary", (
        ("entity_hash", f"{HASH} NOT NULL PRIMARY KEY"),
        ("systemCode", KEY),
        ("businessEntityCode", KEY),
        ("rules_run", "INT"),
        ("rules_failed", "INT"),
        ("failed_rules", TEXT),   # ', '-separated rule_ids; NULL when none failed
        ("validated_at", "DATETIME2(0)"),
    )),

    # ---- Who to contact about a system / country ----
    # Names match case-insensitively. The four UNIQUE columns together stay
    # under SQL Server's 1700-byte index key limit.
    Table("entities_responsible_contact", (
        ("id", ID),
        ("related_field", "NVARCHAR(20) NOT NULL"),                       # 'system' or 'country'
        ("related_field_name", f"NVARCHAR(200) {CASE_INSENSITIVE} NOT NULL"),  # system or country name
        ("person_name", "NVARCHAR(200) NOT NULL"),                        # responsible person
        ("contact", "NVARCHAR(400) NOT NULL"),                            # email or Teams account
        ("created_at", CREATED_AT),
    ), constraints=("UNIQUE (related_field, related_field_name, person_name, contact)",)),
)


def table_definition(name: str) -> Optional[Table]:
    """The Table called `name` (any case), or None if it is not one of ours."""
    return next((t for t in TABLES if t.name.lower() == str(name).lower()), None)


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
def initialize_database() -> list:
    """Check that the tables which already exist have every column the code
    writes (if not, stop with nothing changed), then create the missing ones.

    Safe to call on every startup. Returns the names of the tables created.
    """
    with connect() as conn:
        verify_tables(conn, TABLES)
        return create_missing_tables(conn, TABLES)
