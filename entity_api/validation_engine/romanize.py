"""
=====================================================
validation_engine: /romanize.py
=====================================================
"""


from anyascii import anyascii
import regex

from ..database import get_connection

def is_latin(text: str) -> bool:
    return bool(
        regex.fullmatch(
            r"[\p{Latin}\p{N}\p{P}\p{Z}]+",
            text
        )
    )

def ensure_latin(text: str):
    if is_latin(text):
        return text
    return anyascii(text)

def romanizecheck(text: str) -> dict:
    result = {}
    for field, value in text.items():
        if isinstance(value, str):
            result[field] = value
            result[f"romanize_{field}"] = is_latin(value)
    return result

def insert_romanize_entity(id: int, entity_hash: str, entity_result: dict):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
                INSERT INTO romanize
                (id,
                entity_hash,
                systemCode,
                romanize_systemCode,
                businessEntityCode,
                romanize_businessEntityCode,
                EOID,
                romanize_EOID,
                FID,
                romanize_FID,
                UKEOID,
                romanize_UKEOID,
                UKFID,
                romanize_UKFID,
                SGLN,
                romanize_SGLN)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                entity_hash,
                entity_result.get("systemCode"),
                entity_result.get("romanize_systemCode"),
                entity_result.get("businessEntityCode"),
                entity_result.get("romanize_businessEntityCode"),
                entity_result.get("EOID"),
                entity_result.get("romanize_EOID"),
                entity_result.get("FID"),
                entity_result.get("romanize_FID"),
                entity_result.get("UKEOID"),
                entity_result.get("romanize_UKEOID"),
                entity_result.get("UKFID"),
                entity_result.get("romanize_UKFID"),
                entity_result.get("SGLN"),
                entity_result.get("romanize_SGLN")
            )
        )
        conn.commit()
    finally:
        conn.close()
