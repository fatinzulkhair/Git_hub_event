"""
=====================================================
CRUD / BUSINESS LOGIC LAYER
=====================================================
All read/write operations on the 'entities' table, plus the
logic for processing a payload (list of entities), live here.
Endpoints (routers) only call these functions and never touch
sqlite3 directly.
"""

import json

import pandas as pd
from fastapi import HTTPException
from .database import get_connection
from .hashing import generate_entity_hash
from .validation import validation_rules_engine

from .validation_engine.romanize import romanizecheck, insert_romanize_entity
from .validation_engine.Location import check_country

# ---------------------------------------------------
# INSERT A SINGLE ENTITY
# ---------------------------------------------------

def insert_hystoryentity(entity: dict) -> dict:
    entity_hash = generate_entity_hash(entity)

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM entities WHERE entity_hash = ?",
            (entity_hash,),
        )
        existing = cursor.fetchone()

        if existing:
            return {
                "entity_hash" : entity_hash,
                "existing" : existing,
                "status": "skipped",
                "reason": "already_exists",
                "businessEntityCode": entity.get("businessEntityCode"),
                "existing_id": existing[0],
            }

        cursor.execute(
            """
            INSERT INTO entities
            (
                entity_hash, systemCode, businessEntityCode,
                EOID, FID, UKEOID, UKFID, SGLN, entity_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_hash,
                entity.get("systemCode"),
                entity.get("businessEntityCode"),
                entity.get("EOID"),
                entity.get("FID"),
                entity.get("UKEOID"),
                entity.get("UKFID"),
                entity.get("SGLN"),
                json.dumps(entity),
            ),
        )

        conn.commit()
        inserted_id = cursor.lastrowid

        return {
            "entity_hash" : entity_hash,
            "existing" : existing,
            "status": "inserted",
            "id": inserted_id,
            "businessEntityCode": entity.get("businessEntityCode"),
        }
    finally:
        conn.close()


# ---------------------------------------------------
# PROCESS PAYLOAD (list of entities inside "mappingsInformation",
# or a single entity sent directly)
# ---------------------------------------------------

def process_payload(payload: dict) -> dict:
    if "mappingsInformation" in payload:
        mappings = payload.get("mappingsInformation", [])

        if not isinstance(mappings, list):
            raise HTTPException(
                status_code=400,
                detail="Field 'mappingsInformation' must be a list/array",
            )
    else:
        mappings = [payload]

    results = []
    inserted_count = 0
    skipped_count = 0

    # Shared across every entity in this payload so that Unique(batch)
    # checks in the rule engine can see siblings in the same call.
    batch_ctx = {}

    for entity in mappings:
        #validation rules
        result = insert_hystoryentity(entity)

        if not result.get("existing"):
            entity_hash = result.get("entity_hash")
            id_entity = result.get("id")

            # ---------------------------------------------------
            # Validation rules
            # ---------------------------------------------------
            validation_rules_engine(entity, id_entity, entity_hash, ctx=batch_ctx)
            # ---------------------------------------------------
            # Validation rules end
            # ---------------------------------------------------

        results.append(result)

        if result["status"] == "inserted":
            inserted_count += 1
        else:
            skipped_count += 1

    return {
        "total_entities": len(mappings),
        "inserted": inserted_count,
        "skipped": skipped_count,
        "details": results,
    }

def process_payload_location(payload: dict) -> dict:
    conn = get_connection()
    data = payload
    try:
        cursor = conn.cursor()
        cursor.execute(
                    """
                    INSERT INTO entities_location
                    (
                            systemCode,
                            businessEntityCode,
                            name,
                            type,
                            alternate_code,
                            country,
                            city,
                            zip
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.get("systemCode"),
                        payload.get("Code"),
                        payload.get("Name"),
                        payload.get("Type"),
                        payload.get("Alternate_code"),
                        payload.get("Country"),
                        payload.get("City"),
                        payload.get("Zip")
            ),
        )

        conn.commit()
        inserted_id = cursor.lastrowid
        return {
            "code" : payload.get("Code"), 
            "country": payload.get("Country")
        }

    finally:
        conn.close()
        

# ---------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------

def get_all_entities() -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT id, businessEntityCode, EOID, FID, UKEOID, UKFID, SGLN, created_at
            FROM entities
            ORDER BY id
            """,
            conn,
        )
        return df
    finally:
        conn.close()


def find_entity_by_code(business_entity_code: str) -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM entities WHERE businessEntityCode = ?",
            conn,
            params=(business_entity_code,),
        )
        return df
    finally:
        conn.close()

def get_dynamic_validation() -> pd.DataFrame:
    """Results from the new table-driven rule engine (validation_results),
    one row per (entity, rule) pair."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT
                vr.entity_hash,
                ent.businessEntityCode,
                vr.rule_id,
                vr.passed,
                vr.severity,
                vr.status,
                vr.description,
                vr.created_at
            FROM validation_results AS vr
            LEFT JOIN entities AS ent
            ON ent.entity_hash = vr.entity_hash
            ORDER BY vr.id
            """,
            conn,
        )
        return df
    finally:
        conn.close()


def get_validation() -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT ent.entity_hash,
            coalesce(romanize_systemCode, romanize_businessEntityCode, romanize_EOID, 
                romanize_FID,
                romanize_UKEOID,
                romanize_UKFID,
                romanize_SGLN)
                as Romanize_validation,
            coalesce(loc.UKEOID, loc.UKFID)
                as Location_validation,
            loc.status as Location_validation_status

            FROM entities as ent
            left join romanize as rom
            on ent.entity_hash = rom.entity_hash
            left join Location_validation as loc
            on ent.entity_hash = loc.entity_hash
            """,
            conn,
        )
        return df
    finally:
        conn.close()
