"""
=====================================================
validation_engine: /loader.py
=====================================================
Reads the table-driven rule definitions (val_rules,
val_rule_conditions, val_checks, val_logic) out of SQLite.

No caching on purpose: these tables are small and meant to be
edited by users, so every payload should see the latest rules
without a restart.
"""

import pandas as pd

from ..database import get_connection


def _is_nan(value) -> bool:
    return isinstance(value, float) and value != value  # NaN != NaN


def _clean_row(row: dict) -> dict:
    """pandas turns SQL NULL into NaN (even on object/string-dtype
    columns, in pandas 3.x), and NaN is truthy in Python
    (bool(float('nan')) is True) — every `if row.get('x')` check in
    this engine would then misfire on an empty column. Normalize NaN
    to real None right after reading, per-value, since DataFrame-level
    .where()/.fillna() don't reliably do this across pandas versions
    with the newer string dtype."""
    return {k: (None if _is_nan(v) else v) for k, v in row.items()}


def _clean_records(df: pd.DataFrame) -> list:
    return [_clean_row(r) for r in df.to_dict(orient="records")]


def get_active_rules() -> list:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM val_rules WHERE active = 'Y'",
            conn,
        )
        return _clean_records(df)
    finally:
        conn.close()


def get_conditions(rule_id: str) -> list:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM val_rule_conditions WHERE rule_id = ?",
            conn,
            params=(rule_id,),
        )
        return _clean_records(df)
    finally:
        conn.close()


def get_check(check_id: str) -> dict:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM val_checks WHERE check_id = ?",
            conn,
            params=(check_id,),
        )
        if df.empty:
            return None
        return _clean_row(df.iloc[0].to_dict())
    finally:
        conn.close()


def get_logic(logic_id: str) -> dict:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM val_logic WHERE logic_id = ?",
            conn,
            params=(logic_id,),
        )
        if df.empty:
            return None
        return _clean_row(df.iloc[0].to_dict())
    finally:
        conn.close()


def insert_validation_result(entity_id: int, entity_hash: str, rule_id: str,
                              passed: bool, severity: str, status,
                              description: str) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO validation_results
            (entity_hash, entity_id, rule_id, passed, severity, status, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_hash, entity_id, rule_id, passed, severity, status, description),
        )
        conn.commit()
    finally:
        conn.close()
