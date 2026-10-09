"""
=====================================================
ROUTER: /products
=====================================================
Contains only the endpoint definitions (routing + request/
response validation). The actual logic is delegated to the
product_crud.py module.
"""

import json
from typing import Any, Dict, List, Union

import pandas as pd
from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .. import product_crud
from ..product_schemas import ProductProcessSummary

router = APIRouter(prefix="/products", tags=["Products"])


def _table(df: pd.DataFrame) -> JSONResponse:
    """A DataFrame as a JSON array of row objects."""
    return JSONResponse(content=json.loads(df.to_json(orient="records")))


def _process(payload, source: str) -> dict:
    """Store and validate a payload. A malformed one is a 400, and nothing
    is written — the message names the offending node."""
    try:
        return product_crud.process_products(payload, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------
# Posting products (every level is stored and validated)
# ---------------------------------------------------

@router.post(
    "/upload-file",
    response_model=ProductProcessSummary,
    summary="Upload a product JSON file (multipart/form-data)",
)
async def upload_json_file(file: UploadFile = File(...)):
    """
    Accept an uploaded .json file (e.g. product_data/product.json) holding one
    product hierarchy or a list of them, then store and validate every level.
    """
    if not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be in .json format")

    content = await file.read()

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file content")

    return _process(payload, source=file.filename)


@router.post(
    "/process",
    response_model=ProductProcessSummary,
    summary="Send product JSON directly (one hierarchy or a list)",
)
async def process_json_body(
    payload: Union[Dict[str, Any], List[Dict[str, Any]]] = Body(...),
):
    """
    Accept product JSON directly in the request body — the content of one
    product_data/*.json file, or a list of them:

        { "PrimaryProductKey": "...", "ProductType": {...}, ..., "ChildProducts": [...] }

    Every level of the hierarchy is stored as a product of its own and
    validated. A product anywhere in the payload without a PrimaryProductKey
    rejects the whole payload (400).
    """
    return _process(payload, source="payload")


# ---------------------------------------------------
# Reading products
# ---------------------------------------------------

@router.get(
    "",
    summary="Display all products (one per PrimaryProductKey)",
)
def list_products():
    return _table(product_crud.get_all_products())


@router.get(
    "/historical",
    summary="Display every posted product (product_history)",
)
def list_historical_products():
    return _table(product_crud.get_all_historical_products())


@router.get(
    "/lookup",
    summary="Find products by PrimaryProductKey or any AlternateProductKey (e.g. a GTIN)",
)
def lookup_products(
    key: str = Query(
        ...,
        description="A PrimaryProductKey, or an AlternateProductKey such as "
                    "urn:inexto:id:prd:gtin:05410706508796",
    ),
):
    """
    Every product whose PrimaryProductKey, or one of whose AlternateProductKeys,
    equals `key`. An MSKU key is shared by every level of a hierarchy, so it
    can match several products.

    Each match carries its detail lists, its direct children, and
    `rule_fields`: the exact field names and values validation rules see for
    it — copy field names from there when writing a rule.
    """
    matches = product_crud.find_products_by_key(key)

    if not matches:
        raise HTTPException(status_code=404, detail="Product not found")

    return JSONResponse(content=matches)


@router.get(
    "/validation-results",
    summary="Every validation outcome ever written (full history)",
)
def list_validation_results():
    return _table(product_crud.get_product_validation_results())


@router.get(
    "/validation-results/latest",
    summary="The latest run only, one row per product + rule",
)
def list_latest_validation():
    return _table(product_crud.get_product_latest_validation())


@router.get(
    "/validation-results/summary",
    summary="Per product: how many rules failed, and which ones",
)
def list_validation_summary():
    return _table(product_crud.get_product_validation_summary())
