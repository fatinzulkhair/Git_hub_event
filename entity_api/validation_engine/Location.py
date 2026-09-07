"""
=====================================================
validation_engine: /Location.py
=====================================================
"""

from anyascii import anyascii
import regex
import pandas as pd

from ..database import get_connection

def check_country(businessEntityCode:str, systemCode:str):
    conn = get_connection()
    country_query_str = f"""
                        SELECT * FROM entities_location
                        where businessEntityCode = '{businessEntityCode}' and systemCode = '{systemCode}'
                        """
    country_exist = pd.read_sql_query(
            country_query_str,
            conn
        )
    if len(country_exist) >0:
        return country_exist.iloc[0]["country"]
    else :
        return None

def eu_detect(entity: str):
    EU_COUNTRIES = {
                "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE",
                "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
                "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
                "SI", "ES", "SE"
            }

    country_value = check_country(entity.get("businessEntityCode"), entity.get("systemCode"))

    required_fields = [
                "UKEOID",
                "UKFID"
            ]

    result = {}

    for field in required_fields:
        result[field] = not (
                pd.isna(entity[field])
                or str(entity[field]).strip() == ""
            )
    
    if country_value is not None:
        country_code = country_value.split(" - ")[0].strip()
        if country_code in EU_COUNTRIES:
            result["is_eu"] = True
            result["status"] = "complete" 

            return result

        result["is_eu"] = False
        result["status"] = "not_eu" 

        return result
    else :  
        result["is_eu"] = False
        result["status"] = "Location not exist" 
        return result

def insert_location_validation(id: int, entity_hash: str, entity_result: dict):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
                INSERT INTO Location_validation
                (
                    id,
                    entity_hash,
                    UKEOID,
                    UKFID,
                    is_eu,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                id,
                entity_hash,
                entity_result.get("UKEOID"),
                entity_result.get("UKFID"),
                entity_result.get("is_eu"),
                entity_result.get("status")
            )
        )
        conn.commit()
    finally:
        conn.close()
