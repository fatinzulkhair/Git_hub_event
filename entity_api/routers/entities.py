"""
=====================================================
ROUTER: /entities
=====================================================
Contains only the endpoint definitions (routing + request/
response validation). The actual logic is delegated to the
crud.py module.
"""

import json
from typing import Any, Dict, List, Union

import pandas as pd
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import crud

router = APIRouter(prefix="/entities", tags=["Entities"])


def _table(df: pd.DataFrame) -> JSONResponse:
    """A DataFrame as a JSON array of row objects."""
    return JSONResponse(content=json.loads(df.to_json(orient="records")))


# ---------------------------------------------------
# Posting records (both sides run the validation rules)
# ---------------------------------------------------

@router.post(
    "/upload-file",
    summary="Upload JSON entity file (multipart/form-data)",
)
async def upload_json_file(file: UploadFile = File(...)):
    """
    Accept an uploaded .json file (e.g. 'StoreEntityJson testing.json'),
    then process every entity contained within it.
    """
    if not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be in .json format")

    content = await file.read()

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file content")

    return JSONResponse(content=crud.process_payload(payload))


@router.post(
    "/process",
    summary="Send JSON body directly (raw JSON payload)",
)
async def process_json_body(payload: Dict[str, Any] = Body(...)):
    """
    Accept a JSON payload directly in the request body, e.g.:
    {
        "mappingsInformation": [ { ... }, { ... } ]
    }

    Each entity is stored and validated against its matching `location` row.
    An entity with no location yet is reported by the JOIN-COMPLETENESS rule.
    """
    if "mappingsInformation" in payload or "systemCode" in payload:
        return JSONResponse(content=crud.process_payload(payload))

    raise HTTPException(
        status_code=400,
        detail="Field 'mappingsInformation' was not found in the payload",
    )


@router.post(
    "/inputlocation",
    summary="Send location master data (one object or a list)",
)
async def process_json_body_location(
    payload: Union[Dict[str, Any], List[Dict[str, Any]]] = Body(...),
):
    """
    Location master data. Each item needs `systemCode`, `Code` (stored as
    businessEntityCode) and `Country`; `Name`, `Type`, `Alternate_code`,
    `City` and `Zip` are optional.

    Each location is stored and validated against its matching `entities`
    row. A location with no entity yet is reported by the JOIN-COMPLETENESS
    rule.
    """
    records = payload if isinstance(payload, list) else [payload]

    for record in records:
        if "Country" not in record and "country" not in record:
            raise HTTPException(
                status_code=400,
                detail="Field 'Country' was not found in the payload",
            )

    return JSONResponse(content=crud.process_payload_location(records))


# ---------------------------------------------------
# Reading records
# ---------------------------------------------------

@router.get(
    "",
    summary="Display all entities (one per systemCode + businessEntityCode)",
)
def list_entities():
    return _table(crud.get_all_entities())


@router.get(
    "/historical",
    summary="Display every posted entity (historical_entities)",
)
def list_historical_entities():
    return _table(crud.get_all_historical_entities())


@router.get(
    "/locations",
    summary="Display all location master data (one per systemCode + businessEntityCode)",
)
def list_locations():
    return _table(crud.get_all_locations())


@router.get(
    "/locations/historical",
    summary="Display every posted location (historical_location)",
)
def list_historical_locations():
    return _table(crud.get_all_historical_locations())


@router.get(
    "/joined",
    summary="The entity + location FULL OUTER JOIN the rules are evaluated against",
)
def list_joined_records():
    return _table(crud.get_joined_records())


@router.get(
    "/validation-results",
    summary="Every validation outcome ever written (full history)",
)
def list_dynamic_validation():
    return _table(crud.get_dynamic_validation())


@router.get(
    "/validation-results/latest",
    summary="The latest run only, one row per record + rule",
)
def list_latest_validation():
    return _table(crud.get_latest_validation())


@router.get(
    "/validation-results/summary",
    summary="Per record: how many rules failed, and which ones",
)
def list_validation_summary():
    return _table(crud.get_validation_summary())


@router.get(
    "/{business_entity_code}",
    summary="Find an entity by businessEntityCode",
)
def get_entity(business_entity_code: str):
    df = crud.find_entity_by_code(business_entity_code)

    if df.empty:
        raise HTTPException(status_code=404, detail="Entity not found")

    return _table(df)
