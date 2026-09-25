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

from .config import DB_NAME
from .hashing import generate_entity_hash


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
    columns: tuple  # data columns, in schema order (no id/entity_hash/created_at)

    @property
    def write_columns(self) -> tuple:
        """Columns a posting supplies; created_at is left to its DEFAULT."""
        return ("entity_hash",) + self.columns

    @property
    def all_columns(self) -> tuple:
        return self.write_columns + ("created_at",)


ENTITIES = PairTables(
    latest="entities",
    history="historical_entities",
    columns=("systemCode", "businessEntityCode", "EOID", "FID",
             "UKEOID", "UKFID", "SGLN", "entity_json"),
)

LOCATIONS = PairTables(
    latest="location",
    history="historical_location",
    columns=("systemCode", "businessEntityCode", "name", "type",
             "alternate_code", "country", "city", "zip"),
)

PAIR_TABLES = (ENTITIES, LOCATIONS)

# PRAGMA user_version tracks migrations that must run exactly once. Everything
# else below is written to be idempotent and simply runs on every startup.
#   2  `entities` holds the NEWEST posting per record (older: the first one)
#   3  the dead `romanize` / `Location_validation` tables are dropped
#   4  `entities_location` is split into historical_location + location
#   5  latest_validation_rules / validation_summary backfilled from history
_SCHEMA_VERSION = 5

# Superseded by the user-driven rule engine (val_* tables). They were written
# by the old hardcoded romanizecheck()/eu_detect() pair, which is gone.
_LEGACY_TABLES = ("romanize", "Location_validation")


def get_connection() -> sqlite3.Connection:
    """Open a new connection to the SQLite database."""
    return sqlite3.connect(DB_NAME)


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


def execute(sql: str, params: Sequence[Any] = (),
            conn: Optional[sqlite3.Connection] = None) -> sqlite3.Cursor:
    with connect(conn) as active:
        return active.execute(sql, params)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _create_pair_table(conn: sqlite3.Connection, spec: PairTables,
                       table: str, unique_hash: bool) -> None:
    hash_column = "TEXT NOT NULL UNIQUE" if unique_hash else "TEXT"
    data = ",\n            ".join(f"{column} TEXT" for column in spec.columns)
    conn.execute(
        f"""
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_hash {hash_column},
            {data},
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
    )


def _sync_latest_from_history(conn: sqlite3.Connection, spec: PairTables) -> None:
    """Make `spec.latest` hold the newest posting of every record: existing
    rows are updated in place (ids kept), missing ones inserted."""
    columns = ", ".join(spec.all_columns)
    newest = conn.execute(
        f"SELECT {columns} FROM {spec.history} "
        "WHERE entity_hash IS NOT NULL "
        f"AND id IN (SELECT MAX(id) FROM {spec.history} "
        "           WHERE entity_hash IS NOT NULL GROUP BY entity_hash) "
        "ORDER BY id"
    ).fetchall()

    assignments = ", ".join(f"{c} = ?" for c in spec.all_columns[1:])
    placeholders = ", ".join("?" * len(spec.all_columns))
    for entity_hash, *rest in newest:
        updated = conn.execute(
            f"UPDATE {spec.latest} SET {assignments} WHERE entity_hash = ?",
            (*rest, entity_hash),
        )
        if updated.rowcount == 0:
            conn.execute(
                f"INSERT INTO {spec.latest} ({columns}) VALUES ({placeholders})",
                (entity_hash, *rest),
            )


def _entity_hash_is_unique(conn: sqlite3.Connection) -> bool:
    """True if `entities` still has the old UNIQUE constraint on entity_hash."""
    for _seq, name, unique, *_ in conn.execute("PRAGMA index_list(entities)").fetchall():
        if unique:
            columns = [row[2] for row in conn.execute(f"PRAGMA index_info({name})")]
            if columns == ["entity_hash"]:
                return True
    return False


def _migrate_entities_drop_unique_hash(conn: sqlite3.Connection) -> None:
    """Oldest databases have a single `entities` table with
    `entity_hash TEXT UNIQUE` over the whole payload. Before that table becomes
    the history table it must lose the constraint (repeat postings of the same
    (systemCode, businessEntityCode) are all kept there). SQLite cannot drop a
    constraint in place, so rebuild the table: ids and data are kept, each row's
    hash is re-derived from its own systemCode/businessEntityCode, and
    validation_results follows. Only called while the tables are not split yet.
    """
    if not _table_exists(conn, "entities") or not _entity_hash_is_unique(conn):
        return

    rows = conn.execute(
        "SELECT id, systemCode, businessEntityCode FROM entities"
    ).fetchall()
    new_hashes = [
        (generate_entity_hash({"systemCode": system, "businessEntityCode": code}), row_id)
        for row_id, system, code in rows
    ]

    columns = ", ".join(("id",) + ENTITIES.all_columns)
    conn.execute("BEGIN")
    try:
        _create_pair_table(conn, ENTITIES, "entities_rebuild", unique_hash=False)
        conn.execute(
            f"INSERT INTO entities_rebuild ({columns}) SELECT {columns} FROM entities"
        )
        conn.execute("DROP TABLE entities")
        conn.execute("ALTER TABLE entities_rebuild RENAME TO entities")
        conn.executemany("UPDATE entities SET entity_hash = ? WHERE id = ?", new_hashes)
        if _table_exists(conn, "validation_results"):
            conn.execute(
                """
                UPDATE validation_results
                SET entity_hash = (
                    SELECT e.entity_hash FROM entities AS e
                    WHERE e.id = validation_results.entity_id
                )
                WHERE entity_id IN (SELECT id FROM entities)
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _ensure_entity_tables(conn: sqlite3.Connection) -> None:
    """Make sure historical_entities and entities exist, splitting an older
    single `entities` table if that is what the database still has."""
    if _table_exists(conn, ENTITIES.history):
        if not _table_exists(conn, ENTITIES.latest):
            _create_pair_table(conn, ENTITIES, ENTITIES.latest, unique_hash=True)
            conn.commit()
        return

    if not _table_exists(conn, ENTITIES.latest):
        _create_pair_table(conn, ENTITIES, ENTITIES.history, unique_hash=False)
        _create_pair_table(conn, ENTITIES, ENTITIES.latest, unique_hash=True)
        conn.commit()
        return

    # Oldest database: the single `entities` table holds every posting, so it
    # becomes historical_entities as-is (ids, and therefore
    # validation_results.entity_id, stay valid). The new `entities` is then
    # filled with the newest posting of each (systemCode, businessEntityCode).
    _migrate_entities_drop_unique_hash(conn)
    conn.execute("BEGIN")
    try:
        conn.execute(f"ALTER TABLE entities RENAME TO {ENTITIES.history}")
        _create_pair_table(conn, ENTITIES, ENTITIES.latest, unique_hash=True)
        _sync_latest_from_history(conn, ENTITIES)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _ensure_location_tables(conn: sqlite3.Connection) -> None:
    """Make sure historical_location and location exist.

    An older database keeps every location posting in `entities_location`,
    which has no entity_hash. That table becomes historical_location (ids and
    data kept, each row's hash derived from its own systemCode /
    businessEntityCode), and `location` is filled with the newest posting per
    record. The original insert time was never stored, so migrated rows get
    the migration timestamp; their order is still the original id order.
    """
    if _table_exists(conn, LOCATIONS.history):
        if not _table_exists(conn, LOCATIONS.latest):
            _create_pair_table(conn, LOCATIONS, LOCATIONS.latest, unique_hash=True)
            _sync_latest_from_history(conn, LOCATIONS)
            conn.commit()
        return

    _create_pair_table(conn, LOCATIONS, LOCATIONS.history, unique_hash=False)

    if _table_exists(conn, "entities_location"):
        old = ", ".join(LOCATIONS.columns)
        rows = conn.execute(
            f"SELECT id, {old} FROM entities_location ORDER BY id"
        ).fetchall()
        columns = ", ".join(("id",) + LOCATIONS.write_columns)
        placeholders = ", ".join("?" * (len(LOCATIONS.write_columns) + 1))
        conn.executemany(
            f"INSERT INTO {LOCATIONS.history} ({columns}) VALUES ({placeholders})",
            [
                (row[0],
                 generate_entity_hash({"systemCode": row[1], "businessEntityCode": row[2]}),
                 *row[1:])
                for row in rows
            ],
        )
        conn.execute("DROP TABLE entities_location")

    _create_pair_table(conn, LOCATIONS, LOCATIONS.latest, unique_hash=True)
    _sync_latest_from_history(conn, LOCATIONS)
    conn.commit()


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _drop_legacy_tables(conn: sqlite3.Connection) -> None:
    """Remove the tables the old hardcoded validation wrote to. Their job is
    done by user-defined rules in val_* now, and nothing reads them."""
    for table in _LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


_STATIC_DDL = """
    -- =================================================
    -- Table-driven rule engine (dynamic validation)
    -- =================================================

    CREATE TABLE IF NOT EXISTS val_rules (
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

    CREATE TABLE IF NOT EXISTS val_rule_conditions (
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
        FOREIGN KEY (rule_id) REFERENCES val_rules(rule_id)
        );

    CREATE TABLE IF NOT EXISTS val_checks (
        check_id TEXT PRIMARY KEY,
        check_type TEXT,
        field_names TEXT,
        check_value TEXT,
        expected_value TEXT,
        reference_source TEXT,
        field_combine TEXT DEFAULT 'AND'
        );

    CREATE TABLE IF NOT EXISTS val_logic (
        logic_id TEXT PRIMARY KEY,
        if_check_id TEXT,
        then_check_id TEXT,
        else_check_id TEXT
        );

    -- One row per (record, rule). entity_id / location_id point at the two
    -- postings that were joined; either is NULL when that side does not exist
    -- yet, which is what JOIN-COMPLETENESS reports on.
    CREATE TABLE IF NOT EXISTS validation_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_hash TEXT,
        entity_id INTEGER,
        location_id INTEGER,
        source TEXT,
        rule_id TEXT,
        passed BOOLEAN,
        severity TEXT,
        status INTEGER,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

    -- The outcomes of the most recent run only, one row per record and rule.
    -- A record is its entity_hash = (systemCode, businessEntityCode) hashed,
    -- so the same row covers the entity and the location side of the join.
    CREATE TABLE IF NOT EXISTS latest_validation_rules (
        entity_hash TEXT NOT NULL,
        systemCode TEXT,
        businessEntityCode TEXT,
        rule_id TEXT NOT NULL,
        source TEXT,
        passed BOOLEAN,
        severity TEXT,
        status INTEGER,
        description TEXT,
        validated_at TIMESTAMP,
        PRIMARY KEY (entity_hash, rule_id)
        );

    -- latest_validation_rules aggregated to one row per record.
    CREATE TABLE IF NOT EXISTS validation_summary (
        entity_hash TEXT PRIMARY KEY,
        systemCode TEXT,
        businessEntityCode TEXT,
        rules_run INTEGER,
        rules_failed INTEGER,
        failed_rules TEXT,   -- ', '-separated rule_ids; NULL when none failed
        validated_at TIMESTAMP
        );

    -- =================================================
    -- Who to contact about a system / country
    -- =================================================

    CREATE TABLE IF NOT EXISTS responsible_contact (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        related_field TEXT NOT NULL,       -- 'system' or 'country'
        related_field_name TEXT NOT NULL,  -- system name or country name
        person_name TEXT NOT NULL,         -- responsible person
        contact TEXT NOT NULL,             -- email or Teams account
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (related_field, related_field_name, person_name, contact)
        )
"""


# ---------------------------------------------------------------------------
# Derived result tables
# ---------------------------------------------------------------------------
# validation_results keeps every outcome ever written; the two tables below are
# derived from it and refreshed inside the same transaction as the insert, so a
# reader never sees them lag behind. Both are keyed by entity_hash, never by a
# posting id — one record has one row per rule, however often it is posted.

# The record a result belongs to. Its codes come from whichever side of the
# join existed at the time, exactly as in crud.get_dynamic_validation.
_RESULT_RECORD = f"""
    FROM validation_results AS vr
    LEFT JOIN {ENTITIES.history}  AS e ON e.id = vr.entity_id
    LEFT JOIN {LOCATIONS.history} AS l ON l.id = vr.location_id
"""

_TOUCHED = "(SELECT DISTINCT entity_hash FROM validation_results WHERE id > ?)"


def _latest_upsert(where: str) -> str:
    """Copy result rows into latest_validation_rules, newest last so that a
    record posted twice in one batch ends up with its final outcome."""
    return f"""
        INSERT OR REPLACE INTO latest_validation_rules
            (entity_hash, systemCode, businessEntityCode, rule_id, source,
             passed, severity, status, description, validated_at)
        SELECT vr.entity_hash,
               COALESCE(e.systemCode, l.systemCode),
               COALESCE(e.businessEntityCode, l.businessEntityCode),
               vr.rule_id, vr.source, vr.passed, vr.severity, vr.status,
               vr.description, vr.created_at
        {_RESULT_RECORD}
        WHERE {where}
        ORDER BY vr.id
    """


def _summary_upsert(where: str) -> str:
    return f"""
        INSERT OR REPLACE INTO validation_summary
            (entity_hash, systemCode, businessEntityCode, rules_run,
             rules_failed, failed_rules, validated_at)
        SELECT entity_hash, systemCode, businessEntityCode,
               COUNT(*),
               SUM(CASE WHEN passed THEN 0 ELSE 1 END),
               GROUP_CONCAT(CASE WHEN passed THEN NULL ELSE rule_id END, ', '),
               MAX(validated_at)
        FROM latest_validation_rules
        WHERE {where}
        GROUP BY entity_hash, systemCode, businessEntityCode
    """


def refresh_validation_tables(conn: sqlite3.Connection,
                              since_id: Optional[int] = None) -> None:
    """Re-derive latest_validation_rules and validation_summary.

    `since_id` is the highest validation_results.id from before a batch was
    written: only the records that batch touched are rebuilt, and their old
    rows are cleared first so the tables hold that run and nothing else.

    Passing None rebuilds both tables from scratch, taking the newest result
    per (record, rule) — used once to backfill a database whose results
    predate these tables.
    """
    if since_id is None:
        conn.execute("DELETE FROM latest_validation_rules")
        conn.execute("DELETE FROM validation_summary")
        conn.execute(_latest_upsert(
            "vr.id IN (SELECT MAX(id) FROM validation_results "
            "GROUP BY entity_hash, rule_id)"
        ))
        conn.execute(_summary_upsert("1 = 1"))
        return

    conn.execute(
        f"DELETE FROM latest_validation_rules WHERE entity_hash IN {_TOUCHED}",
        (since_id,),
    )
    conn.execute(_latest_upsert("vr.id > ?"), (since_id,))
    # Every touched record now has latest rows, so REPLACE covers its summary.
    conn.execute(_summary_upsert(f"entity_hash IN {_TOUCHED}"), (since_id,))


def initialize_database() -> None:
    """Create every table that doesn't exist yet and apply small migrations.

    Safe to call on every startup: each step is either guarded by
    IF NOT EXISTS / a table-exists check, or gated on PRAGMA user_version.
    """
    conn = get_connection()
    try:
        _ensure_entity_tables(conn)
        conn.executescript(_STATIC_DDL)

        # validation_results predates the entity/location join.
        _add_column(conn, "validation_results", "location_id", "INTEGER")
        _add_column(conn, "validation_results", "source", "TEXT")

        _ensure_location_tables(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            if version < 2:
                # The first entity split kept the FIRST posting per record.
                _sync_latest_from_history(conn, ENTITIES)
            if version < 3:
                _drop_legacy_tables(conn)
            if version < 5:
                # Results written before the derived tables existed.
                refresh_validation_tables(conn)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

        conn.commit()
    finally:
        conn.close()
