"""
=====================================================
DATABASE LAYER
=====================================================
Solely responsible for the SQLite connection and table
schema creation. Other modules should never call
sqlite3.connect() directly, just use get_connection().
"""

import sqlite3

from .config import DB_NAME


def get_connection() -> sqlite3.Connection:
    """Open a new connection to the SQLite database."""
    return sqlite3.connect(DB_NAME)


def initialize_database() -> None:
    """Create the 'entities' table if it doesn't exist yet."""
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_hash TEXT UNIQUE,
                systemCode TEXT,
                businessEntityCode TEXT,
                EOID TEXT,
                FID TEXT,
                UKEOID TEXT,
                UKFID TEXT,
                SGLN TEXT,
                entity_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            
            CREATE TABLE IF NOT EXISTS romanize (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_hash TEXT UNIQUE,
                systemCode TEXT,
                romanize_systemCode BOOLEAN,
                businessEntityCode TEXT,
                romanize_businessEntityCode BOOLEAN,
                EOID TEXT,
                romanize_EOID BOOLEAN,
                FID TEXT,
                romanize_FID BOOLEAN,
                UKEOID TEXT,
                romanize_UKEOID BOOLEAN,
                UKFID TEXT,
                romanize_UKFID BOOLEAN,
                SGLN TEXT,
                romanize_SGLN BOOLEAN
                );

            CREATE TABLE IF NOT EXISTS entities_location (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                systemCode TEXT,
                businessEntityCode TEXT,
                name TEXT,
                type TEXT,
                alternate_code TEXT,
                country TEXT,
                city TEXT,
                zip TEXT
                );

            CREATE TABLE IF NOT EXISTS Location_validation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_hash TEXT UNIQUE,
                UKEOID BOOLEAN,
                UKFID BOOLEAN,
                is_eu BOOLEAN,
                status TEXT
                );

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

            CREATE TABLE IF NOT EXISTS validation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_hash TEXT,
                entity_id INTEGER,
                rule_id TEXT,
                passed BOOLEAN,
                severity TEXT,
                status INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
        )
        conn.commit()
    finally:
        conn.close()
