"""
=====================================================
CRUD / BUSINESS LOGIC LAYER
=====================================================
All read/write operations on the record tables, plus the logic for
processing a payload, live here. Endpoints (routers) only call these
functions and never touch sqlite3 directly.

Entities and locations are handled by one code path: each is a record
identified by (systemCode, businessEntityCode), stored as "every posting" +
"newest posting", and validated against the FULL OUTER JOIN of the two.
"""

import json

import pandas as pd
from fastapi import HTTPException

from .database import (
    ENTITIES,
    LOCATIONS,
    PairTables,
    connect,
    query_df,
    query_one,
)
from .hashing import generate_entity_hash
from .validation_engine.loader import insert_validation_results, load_ruleset
from .validation_engine.rule_engine import run_all_rules
from .validation_engine.values import CaseInsensitiveRecord


# ---------------------------------------------------
# STORING A POSTING  (history always, latest replaced)
# ---------------------------------------------------

def store_posting(spec: PairTables, record: dict, conn) -> dict:
    """Append `record` to spec.history and insert-or-replace it in spec.latest.

    `record` is keyed by spec.columns. Returns the new history row's id and
    whether the record was new to spec.latest (False = it replaced a row).
    """
    entity_hash = generate_entity_hash(record)
    values = (entity_hash,) + tuple(record.get(column) for column in spec.columns)

    columns = ", ".join(spec.write_columns)
    placeholders = ", ".join("?" * len(spec.write_columns))

    cursor = conn.execute(
        f"INSERT INTO {spec.history} ({columns}) VALUES ({placeholders})", values
    )
    history_id = cursor.lastrowid

    # The write lock is already held (history insert above), so this
    # check-then-write cannot race another request.
    existing = conn.execute(
        f"SELECT id FROM {spec.latest} WHERE entity_hash = ?", (entity_hash,)
    ).fetchone()

    if existing:
        assignments = ", ".join(f"{c} = ?" for c in spec.columns)
        conn.execute(
            f"UPDATE {spec.latest} SET {assignments}, created_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            values[1:] + (existing[0],),
        )
    else:
        conn.execute(
            f"INSERT INTO {spec.latest} ({columns}) VALUES ({placeholders})", values
        )

    return {
        "entity_hash": entity_hash,
        "status": "inserted",
        "history_id": history_id,
        "is_new": not existing,
        "systemCode": record.get("systemCode"),
        "businessEntityCode": record.get("businessEntityCode"),
    }


# ---------------------------------------------------
# THE JOIN  (entity <-> location on entity_hash)
# ---------------------------------------------------

def latest_posting(spec: PairTables, entity_hash: str, conn=None) -> dict:
    """The newest posting of one record, or None. Read from the history table
    so the caller also gets the posting's id; its data matches spec.latest."""
    return query_one(
        f"SELECT * FROM {spec.history} WHERE entity_hash = ? ORDER BY id DESC LIMIT 1",
        (entity_hash,),
        conn=conn,
    )


def _fields_of(spec: PairTables, row: dict) -> dict:
    """The rule-visible fields of a stored posting."""
    if row is None:
        return {}
    if spec is ENTITIES:
        # entity_json is the payload as posted, so rules see every field the
        # client sent, not just the columns broken out for querying.
        try:
            payload = json.loads(row.get("entity_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        if payload:
            return payload
    return {column: row.get(column) for column in spec.columns}


# ---------------------------------------------------
# PROCESSING A PAYLOAD
# ---------------------------------------------------

def _to_entity_record(payload: dict) -> dict:
    fields = CaseInsensitiveRecord(payload)
    return {
        "systemCode": fields.get("systemCode"),
        "businessEntityCode": fields.get("businessEntityCode"),
        "EOID": fields.get("EOID"),
        "FID": fields.get("FID"),
        "UKEOID": fields.get("UKEOID"),
        "UKFID": fields.get("UKFID"),
        "SGLN": fields.get("SGLN"),
        "entity_json": json.dumps(payload),
    }


def _to_location_record(payload: dict) -> dict:
    """The location endpoint documents capitalised keys, with `Code` standing
    in for businessEntityCode; both spellings are accepted."""
    fields = CaseInsensitiveRecord(payload)
    return {
        "systemCode": fields.get("systemCode"),
        "businessEntityCode": fields.first("Code", "businessEntityCode"),
        "name": fields.get("Name"),
        "type": fields.get("Type"),
        "alternate_code": fields.get("Alternate_code"),
        "country": fields.get("Country"),
        "city": fields.get("City"),
        "zip": fields.get("Zip"),
    }


def _process_postings(spec: PairTables, payloads: list, to_record, source: str) -> dict:
    """Store every posting, then validate each one against the join of the
    entity and location tables. Used by both POST endpoints."""
    other = LOCATIONS if spec is ENTITIES else ENTITIES
    results = []

    # One connection for the whole payload, and one snapshot of the rule
    # tables — the engine then resolves Check/Logic ids in memory.
    with connect() as conn:
        ruleset = load_ruleset(conn=conn)

        # Shared across every record in this payload so that Unique(batch)
        # checks see siblings in the same call, and so a database-scoped
        # Unique check reuses this connection.
        ctx = {"conn": conn}
        result_rows = []

        for payload in payloads:
            record = to_record(payload)
            stored = store_posting(spec, record, conn)
            entity_hash = stored["entity_hash"]

            # The other side of the join, as last posted (None if it has
            # never been posted — that is what JOIN-COMPLETENESS reports).
            counterpart = latest_posting(other, entity_hash, conn=conn)
            counterpart_id = counterpart["id"] if counterpart else None

            posted_fields = _fields_of(spec, {**record, **stored})
            other_fields = _fields_of(other, counterpart)
            if spec is ENTITIES:
                joined = {**other_fields, **posted_fields}
                entity_id, location_id = stored["history_id"], counterpart_id
            else:
                joined = {**posted_fields, **other_fields}
                entity_id, location_id = counterpart_id, stored["history_id"]

            _, rows = run_all_rules(
                joined,
                entity_hash,
                entity_id=entity_id,
                location_id=location_id,
                source=source,
                ctx=ctx,
                ruleset=ruleset,
            )
            result_rows.extend(rows)

            results.append({
                "status": "inserted",
                "id": stored["history_id"],
                "new_record": stored["is_new"],
                "entity_hash": entity_hash,
                "systemCode": stored["systemCode"],
                "businessEntityCode": stored["businessEntityCode"],
                "joined": counterpart is not None,
            })

        insert_validation_results(result_rows, conn=conn)

    new_records = sum(1 for r in results if r["new_record"])
    return {
        "total": len(results),
        "inserted": len(results),
        "new_records": new_records,
        "updated_records": len(results) - new_records,
        "joined": sum(1 for r in results if r["joined"]),
        "awaiting_counterpart": sum(1 for r in results if not r["joined"]),
        "details": results,
    }


def process_payload(payload: dict) -> dict:
    """POST /entities/process — entities, validated against their locations."""
    if "mappingsInformation" in payload:
        mappings = payload.get("mappingsInformation", [])
        if not isinstance(mappings, list):
            raise HTTPException(
                status_code=400,
                detail="Field 'mappingsInformation' must be a list/array",
            )
    else:
        mappings = [payload]

    return _process_postings(ENTITIES, mappings, _to_entity_record, "entity")


def process_payload_location(payload) -> dict:
    """POST /entities/inputlocation — locations, validated against their
    entities. Accepts one object or a list."""
    records = payload if isinstance(payload, list) else [payload]
    return _process_postings(LOCATIONS, records, _to_location_record, "location")


# ---------------------------------------------------
# RESPONSIBLE CONTACTS (who to contact per system / country)
# ---------------------------------------------------

def insert_responsible_contacts(contacts: list) -> dict:
    """Insert ResponsibleContactIn records. An identical row (same four
    values) already in the table is skipped rather than duplicated."""
    details = []
    inserted = 0

    with connect() as conn:
        for contact in contacts:
            row = contact.model_dump()
            values = (
                row["related_field"],
                row["related_field_name"],
                row["person_name"],
                row["contact"],
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO responsible_contact
                (related_field, related_field_name, person_name, contact)
                VALUES (?, ?, ?, ?)
                """,
                values,
            )

            if cursor.rowcount == 1:
                status, row_id = "inserted", cursor.lastrowid
                inserted += 1
            else:
                status = "skipped"
                row_id = conn.execute(
                    """
                    SELECT id FROM responsible_contact
                    WHERE related_field = ? AND related_field_name = ?
                      AND person_name = ? AND contact = ?
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


# ---------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------

def _list_table(spec: PairTables, table: str) -> pd.DataFrame:
    columns = ", ".join(("id",) + spec.all_columns)
    return query_df(f"SELECT {columns} FROM {table} ORDER BY id")


def get_all_entities() -> pd.DataFrame:
    """`entities`: the newest posting per (systemCode, businessEntityCode)."""
    return _list_table(ENTITIES, ENTITIES.latest)


def get_all_historical_entities() -> pd.DataFrame:
    """`historical_entities`: every entity posting. Its id is the entity_id
    used in validation_results."""
    return _list_table(ENTITIES, ENTITIES.history)


def get_all_locations() -> pd.DataFrame:
    """`location`: the newest posting per (systemCode, businessEntityCode)."""
    return _list_table(LOCATIONS, LOCATIONS.latest)


def get_all_historical_locations() -> pd.DataFrame:
    """`historical_location`: every location posting. Its id is the
    location_id used in validation_results."""
    return _list_table(LOCATIONS, LOCATIONS.history)


def get_joined_records() -> pd.DataFrame:
    """The FULL OUTER JOIN the rules are evaluated against: one row per
    (systemCode, businessEntityCode), with a `join_status` saying whether
    both sides have been posted."""
    return query_df(
        f"""
        SELECT
            COALESCE(e.entity_hash, l.entity_hash)               AS entity_hash,
            COALESCE(e.systemCode, l.systemCode)                 AS systemCode,
            COALESCE(e.businessEntityCode, l.businessEntityCode) AS businessEntityCode,
            CASE
                WHEN e.entity_hash IS NULL THEN 'missing_entity'
                WHEN l.entity_hash IS NULL THEN 'missing_location'
                ELSE 'complete'
            END                                                  AS join_status,
            e.id AS entity_row_id, e.EOID, e.FID, e.UKEOID, e.UKFID, e.SGLN,
            e.created_at AS entity_updated_at,
            l.id AS location_row_id, l.name, l.type, l.alternate_code,
            l.country, l.city, l.zip,
            l.created_at AS location_updated_at
        FROM {ENTITIES.latest} AS e
        LEFT JOIN {LOCATIONS.latest} AS l ON l.entity_hash = e.entity_hash
        UNION ALL
        SELECT
            l.entity_hash, l.systemCode, l.businessEntityCode, 'missing_entity',
            NULL, NULL, NULL, NULL, NULL, NULL, NULL,
            l.id, l.name, l.type, l.alternate_code, l.country, l.city, l.zip,
            l.created_at
        FROM {LOCATIONS.latest} AS l
        WHERE NOT EXISTS (
            SELECT 1 FROM {ENTITIES.latest} AS e WHERE e.entity_hash = l.entity_hash
        )
        ORDER BY systemCode, businessEntityCode
        """
    )


def get_all_responsible_contacts() -> pd.DataFrame:
    return query_df(
        """
        SELECT id, related_field, related_field_name, person_name,
               contact, created_at
        FROM responsible_contact
        ORDER BY id
        """
    )


def find_entity_by_code(business_entity_code: str) -> pd.DataFrame:
    return query_df(
        f"SELECT * FROM {ENTITIES.latest} WHERE businessEntityCode = ?",
        (business_entity_code,),
    )


def get_latest_validation() -> pd.DataFrame:
    """`latest_validation_rules`: the most recent run per record and rule,
    whether that run came from posting the entity or the location."""
    return query_df(
        """
        SELECT entity_hash, systemCode, businessEntityCode, rule_id, source,
               passed, severity, status, description, validated_at
        FROM latest_validation_rules
        ORDER BY systemCode, businessEntityCode, rule_id
        """
    )


def get_validation_summary() -> pd.DataFrame:
    """`validation_summary`: one row per record — how many rules ran, how many
    failed, and which ones."""
    return query_df(
        """
        SELECT entity_hash, systemCode, businessEntityCode, rules_run,
               rules_failed, failed_rules, validated_at
        FROM validation_summary
        ORDER BY rules_failed DESC, systemCode, businessEntityCode
        """
    )


def get_dynamic_validation() -> pd.DataFrame:
    """Results from the table-driven rule engine (validation_results), one row
    per (record, rule). systemCode / businessEntityCode come from whichever
    side of the join exists."""
    return query_df(
        f"""
        SELECT
            vr.entity_hash,
            COALESCE(e.systemCode, l.systemCode)                 AS systemCode,
            COALESCE(e.businessEntityCode, l.businessEntityCode) AS businessEntityCode,
            vr.source,
            vr.entity_id,
            vr.location_id,
            vr.rule_id,
            vr.passed,
            vr.severity,
            vr.status,
            vr.description,
            vr.created_at
        FROM validation_results AS vr
        LEFT JOIN {ENTITIES.history}  AS e ON e.id = vr.entity_id
        LEFT JOIN {LOCATIONS.history} AS l ON l.id = vr.location_id
        ORDER BY vr.id
        """
    )
