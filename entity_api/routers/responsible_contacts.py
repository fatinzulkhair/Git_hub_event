"""
=====================================================
ROUTER: /responsible-contacts
=====================================================
Create and list rows of the responsible_contact table: who is
responsible for a given system or country, and how to reach them
(email or Teams account).
"""

import json
from typing import List, Union

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from .. import crud
from ..schemas import ResponsibleContactIn, ResponsibleContactSummary

router = APIRouter(prefix="/responsible-contacts", tags=["Responsible Contacts"])

_EXAMPLE = {
    "related_field": "country",
    "related_field_name": "Malaysia",
    "person_name": "Aisyah Rahman",
    "contact": "aisyah.rahman@example.com",
}


@router.post(
    "",
    status_code=201,
    response_model=ResponsibleContactSummary,
    summary="Create one or more responsible contacts",
)
def create_responsible_contacts(
    body: Union[ResponsibleContactIn, List[ResponsibleContactIn]] = Body(
        ..., examples=[_EXAMPLE]
    ),
):
    """Send a single object or a list. `related_field` must be `system` or
    `country`. A row identical to an existing one is reported as `skipped`."""
    contacts = body if isinstance(body, list) else [body]
    return crud.insert_responsible_contacts(contacts)


@router.get(
    "",
    summary="List all responsible contacts",
)
def list_responsible_contacts():
    df = crud.get_all_responsible_contacts()
    return JSONResponse(content=json.loads(df.to_json(orient="records")))
