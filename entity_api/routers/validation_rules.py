"""
=====================================================
ROUTER: /validation-rules
=====================================================
The request body is intentionally permissive, see rule_authoring.py
for every shape/alias that is accepted. In short you may POST:

  * a single rule object                     {"rule_id": "...", ...}
  * a list of rule objects                   [ {...}, {...} ]
  * the seed layout                          {"RULES": [...], "CHECKS": [...]}
  * camelCase keys                           {"ruleId": "...", "checkId": "..."}
  * positional arrays                        {"rules": [["VAL-9", "type", null, ...]]}
"""

from typing import Any, Dict, List, Union

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from .. import rule_authoring

router = APIRouter(prefix="/validation-rules", tags=["Validation Rules"])

_EXAMPLE = {
    "rule_id": "API-ROMANIZE-NAME",
    "validation_type": "Location Master Data - Romanization",
    "description": "Location name must be Latin/ASCII only",
    "severity": "Warning",
    "on_exception_description": "Location name is not romanized",
    "conditions": [{"role": "Target", "field": "Name", "dataType": "Romanize"}],
}


@router.post(
    "",
    summary="Create / replace validation rules",
)
def create_validation_rules(
    body: Union[Dict[str, Any], List[Any]] = Body(..., examples=[_EXAMPLE]),
):
    try:
        payload = rule_authoring.parse_rules_payload(body)
        summary = rule_authoring.save_validation_rules(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(status_code=201, content=summary)


@router.get(
    "",
    summary="List every row in val_rules / val_rule_conditions / val_checks / val_logic",
)
def list_validation_rules():
    return JSONResponse(content=rule_authoring.get_all_rule_definitions())
