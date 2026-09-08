"""
=====================================================
ROUTER: /entities
=====================================================
Contains only the endpoint definitions (routing + request/
response validation). The actual logic is delegated to the
crud.py module.
"""

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import crud

router = APIRouter(prefix="/entities", tags=["Entities"])


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

    summary = crud.process_payload(payload)
    return JSONResponse(content=summary)


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
    """
    if "mappingsInformation" in payload or "systemCode" in payload:
        summary = crud.process_payload(payload)
        return JSONResponse(content=summary)

    raise HTTPException(
        status_code=400,
        detail="Field 'mappingsInformation' was not found in the payload",
    )

@router.post(
    "/inputlocation",
    summary="Send JSON body directly (raw JSON payload)",
)
async def process_json_body_location(payload: Dict[str, Any] | List[Dict[str, Any]] = Body(...)):
    """
    Accept a JSON payload directly in the request body, e.g.:
    """
    if isinstance(payload, list):
        summarys = []
        for payload_location in payload:
            if "Country" in payload_location:
                summary = crud.process_payload_location(payload_location)
            else: 
                raise HTTPException(
                    status_code=400,
                    detail="Field 'Country' was not found in the payload",
                )
            summarys.append(summary)
        return JSONResponse(content=summarys)
    



@router.get(
    "",
    summary="Display all entities in the database",
)
def list_entities():
    df = crud.get_all_entities()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))

@router.get(
    "/validation",
    summary="Display all validation in the database",
)
def list_validation():
    df = crud.get_validation()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))

@router.get(
    "/validation-results",
    summary="Display results from the dynamic (table-driven) rule engine",
)
def list_dynamic_validation():
    df = crud.get_dynamic_validation()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))

@router.get(
    "/locations",
    summary="Display all location master data (entities_location)",
)
def list_locations():
    df = crud.get_all_locations()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))

@router.get(
    "/{business_entity_code}",
    summary="Find an entity by businessEntityCode",
)
def get_entity(business_entity_code: str):
    df = crud.find_entity_by_code(business_entity_code)

    if df.empty:
        raise HTTPException(status_code=404, detail="Entity not found")

    return JSONResponse(content=json.loads(df.to_json(orient="records")))
