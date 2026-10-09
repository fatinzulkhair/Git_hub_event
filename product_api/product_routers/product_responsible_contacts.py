"""
=====================================================
ROUTER: /product-responsible-contacts
=====================================================
Create and list rows of the product_responsible_contact table: who is
responsible for a product, and how to reach them (email or Teams account).
A contact is linked directly to a product_code, so it covers every level of
that product.
"""

import json
from typing import List, Union

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from .. import product_crud
from ..product_schemas import (
    ProductResponsibleContactIn,
    ProductResponsibleContactSummary,
)

router = APIRouter(
    prefix="/product-responsible-contacts", tags=["Product Responsible Contacts"]
)

_EXAMPLE = {
    "product_code": "FA003115",
    "person_name": "Aisyah Rahman",
    "contact": "aisyah.rahman@example.com",
}


@router.post(
    "",
    status_code=201,
    response_model=ProductResponsibleContactSummary,
    summary="Create one or more product responsible contacts",
)
def create_product_responsible_contacts(
    body: Union[ProductResponsibleContactIn, List[ProductResponsibleContactIn]] = Body(
        ..., examples=[_EXAMPLE]
    ),
):
    """Send a single object or a list. `product_code` is the last part of a
    product's ProductDefinitionKey (e.g. `FA003115`). A row identical to an
    existing one is reported as `skipped`."""
    contacts = body if isinstance(body, list) else [body]
    return product_crud.insert_product_responsible_contacts(contacts)


@router.get(
    "",
    summary="List all product responsible contacts",
)
def list_product_responsible_contacts():
    """Every contact. `matching_products` counts the products in
    product_latest with that product_code: 0 means the product is not
    loaded yet, or the code has a typo."""
    df = product_crud.get_all_product_responsible_contacts()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))
