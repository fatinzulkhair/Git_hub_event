"""
=====================================================
SQL SERVER CONNECTION  (AWS RDS)
=====================================================
The only module of the product program that knows how to reach the
database. It reads server_testing/config.json (the same file the entity
program reads: both programs share one database), builds one SQLAlchemy
engine (a connection pool), and hands out pyodbc connections. pyodbc uses
`?` placeholders, so every query in this program is plain SQL with `?`
parameters.

Writes can only reach the database named in config.json:
  - the connection string names that database, and every query uses table
    names without a database prefix (no three-part names, no USE);
  - every new connection is checked: DB_NAME() and SCHEMA_NAME() must be the
    configured database and schema, or the connection is refused;
  - system databases (master, msdb, rdsadmin, ...) are refused outright;
  - tables are only ever created when missing, never altered or dropped
    (see create_missing_tables / verify_tables).

config.json:
    {"database": {"driver": "ODBC Driver 17 for SQL Server",
                  "server": "...rds.amazonaws.com", "database": "...",
                  "username": "...", "password": "..."}}
Optional keys: "schema" (default "dbo"), "encrypt" and
"trust_server_certificate" (both default "yes", as on RDS).
"""

import functools
import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine, event

from .product_config import PRODUCT_DB_CONFIG_PATH

SYSTEM_DATABASES = {"master", "model", "msdb", "tempdb", "rdsadmin", "distribution"}

# Column types shared by every table.
ID = "BIGINT IDENTITY(1,1) PRIMARY KEY"
HASH = "CHAR(64)"                                  # sha256 hex digest
KEY = "NVARCHAR(450)"                              # codes and keys: short enough to index
TEXT = "NVARCHAR(4000)"                            # free text
JSON = "NVARCHAR(MAX)"                             # a whole posted payload
CREATED_AT = "DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()"   # UTC, like SQLite's CURRENT_TIMESTAMP
CASE_INSENSITIVE = "COLLATE SQL_Latin1_General_CP1_CI_AS"


@dataclass(frozen=True)
class ProductDatabaseConfig:
    driver: str
    server: str
    database: str
    username: str
    password: str
    schema: str = "dbo"
    encrypt: str = "yes"
    trust_server_certificate: str = "yes"


def load_db_config(path: str = PRODUCT_DB_CONFIG_PATH) -> ProductDatabaseConfig:
    """The "database" section of config.json, checked. The error messages
    name the file and the missing keys, never the password."""
    if not os.path.exists(path):
        raise RuntimeError(
            f"Database config not found: {path}. Copy "
            "server_testing/config.example.json to server_testing/config.json "
            "and fill in the RDS details."
        )
    try:
        with open(path, encoding="utf-8") as f:
            section = json.load(f).get("database") or {}
    except ValueError as exc:  # the message gives a position, never the content
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from None

    required = ("driver", "server", "database", "username", "password")
    missing = [key for key in required if not section.get(key)]
    if missing:
        raise RuntimeError(f"{path}: 'database' is missing {', '.join(missing)}")

    known = {key: str(value) for key, value in section.items()
             if key in ProductDatabaseConfig.__dataclass_fields__}
    config = ProductDatabaseConfig(**known)
    if config.database.lower() in SYSTEM_DATABASES:
        raise RuntimeError(
            f"{path}: refusing to use the system database '{config.database}'. "
            "Point 'database' at the application database."
        )
    return config


def _odbc_value(value: str) -> str:
    """A connection-string value; braces protect ; { } in e.g. a password."""
    if any(char in value for char in ";{}") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def odbc_connection_string(config: ProductDatabaseConfig) -> str:
    return (
        f"DRIVER={{{config.driver}}};"
        f"SERVER={_odbc_value(config.server)};"
        f"DATABASE={_odbc_value(config.database)};"
        f"UID={_odbc_value(config.username)};"
        f"PWD={_odbc_value(config.password)};"
        f"Encrypt={config.encrypt};"
        f"TrustServerCertificate={config.trust_server_certificate};"
    )


def _check_target(dbapi_connection, config: ProductDatabaseConfig) -> None:
    """Refuse a connection that is not on the configured database and
    schema. Runs once for each new connection in the pool."""
    cursor = dbapi_connection.cursor()
    try:
        database, schema = cursor.execute("SELECT DB_NAME(), SCHEMA_NAME()").fetchone()
        # Any failed statement rolls back the whole transaction on the server,
        # so a unit of work is never half-written.
        cursor.execute("SET XACT_ABORT ON")
    finally:
        cursor.close()
    if (database or "").lower() != config.database.lower():
        raise RuntimeError(f"Connected to database '{database}', expected '{config.database}'")
    if (schema or "").lower() != config.schema.lower():
        raise RuntimeError(
            f"The login's default schema is '{schema}', expected '{config.schema}'. "
            "Tables are created in the default schema, so it must be the configured one."
        )


@functools.lru_cache(maxsize=None)
def get_engine():
    """One engine (connection pool) per process, created on first use."""
    config = load_db_config()
    params = urllib.parse.quote_plus(odbc_connection_string(config))
    # pool_pre_ping replaces a pooled connection RDS has closed (idle
    # timeout, failover) instead of failing the request with it.
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}", pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        _check_target(dbapi_connection, config)

    return engine


def get_connection():
    """A pooled pyodbc connection (autocommit off). close() returns it to
    the pool."""
    return get_engine().raw_connection()


# ---------------------------------------------------------------------------
# Small SQL helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    """Now in UTC, to the second — what the created_at columns hold."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def plain(value):
    """A database value as the API shows it. Timestamps become the text the
    SQLite version returned ("2026-10-21 08:19:26")."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value


def execute_many(conn, sql: str, rows) -> None:
    """executemany, sent in one batch (fast_executemany). An empty `rows` is
    a no-op; pyodbc would raise on it.

    Batched statements must not write NVARCHAR(MAX) columns: pyodbc then
    sizes the buffer from the first row and longer values fail.
    """
    rows = list(rows)
    if not rows:
        return
    cursor = conn.cursor()
    try:
        cursor.fast_executemany = True
        cursor.executemany(sql, rows)
    finally:
        cursor.close()


def upsert(conn, table: str, keys: tuple, row: dict) -> bool:
    """Update the row whose `keys` columns match `row`, or insert `row`.
    Returns True if it was inserted.

    UPDLOCK + SERIALIZABLE lock the key (even when no row exists yet) until
    the transaction ends, so two requests upserting the same key cannot both
    insert it.
    """
    data = [column for column in row if column not in keys]
    assignments = ", ".join(f"{column} = ?" for column in data)
    where = " AND ".join(f"{column} = ?" for column in keys)
    updated = conn.execute(
        f"UPDATE {table} WITH (UPDLOCK, SERIALIZABLE) SET {assignments} WHERE {where}",
        [row[column] for column in data] + [row[column] for column in keys],
    ).rowcount
    if updated:
        return False
    conn.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        list(row.values()),
    )
    return True


# ---------------------------------------------------------------------------
# Tables: created when missing, never altered
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductTable:
    """One table: its columns as (name, SQL type), extra constraint lines
    (PRIMARY KEY / UNIQUE), and indexes as (index name, column list)."""

    name: str
    columns: tuple
    constraints: tuple = ()
    indexes: tuple = ()

    @property
    def column_names(self) -> tuple:
        return tuple(name for name, _sql_type in self.columns)


def create_missing_tables(conn, tables) -> list:
    """CREATE every table in `tables` that does not exist yet, with its
    indexes. Existing tables are left exactly as they are. Returns the names
    of the tables created."""
    created = []
    for table in tables:
        if conn.execute("SELECT OBJECT_ID(?, 'U')", (table.name,)).fetchone()[0] is not None:
            continue
        lines = [f"{name} {sql_type}" for name, sql_type in table.columns]
        lines += list(table.constraints)
        conn.execute(f"CREATE TABLE {table.name} (\n    " + ",\n    ".join(lines) + "\n)")
        for index_name, columns in table.indexes:
            conn.execute(f"CREATE INDEX {index_name} ON {table.name} ({columns})")
        created.append(table.name)
    return created


def verify_tables(conn, tables) -> None:
    """Fail with one clear message if an existing table lacks a column the
    code writes, or its id is not an IDENTITY column. The app never alters
    an existing table, so this is fixed by hand. Tables that do not exist
    yet are skipped (create_missing_tables makes them)."""
    problems = []
    for table in tables:
        if conn.execute("SELECT OBJECT_ID(?, 'U')", (table.name,)).fetchone()[0] is None:
            continue
        actual = {
            name.lower(): bool(is_identity)
            for name, is_identity in conn.execute(
                "SELECT name, is_identity FROM sys.columns "
                "WHERE object_id = OBJECT_ID(?, 'U')",
                (table.name,),
            ).fetchall()
        }
        missing = [name for name in table.column_names if name.lower() not in actual]
        if missing:
            problems.append(f"{table.name}: missing column(s) {', '.join(missing)}")
        expects_identity = any("IDENTITY" in sql_type for _name, sql_type in table.columns)
        if expects_identity and "id" in actual and not actual["id"]:
            problems.append(f"{table.name}: column id is not an IDENTITY column")
    if problems:
        raise RuntimeError(
            "These existing tables do not match what the application writes, "
            "and it never alters an existing table:\n  "
            + "\n  ".join(problems)
            + "\nFix them by hand, or rename them so the application can create "
            "its own."
        )
