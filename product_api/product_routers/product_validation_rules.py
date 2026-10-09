"""
=====================================================
ROUTER: /product-validation-rules
=====================================================
The request body is intentionally permissive, see product_rule_authoring.py
for every shape/alias that is accepted. In short you may POST:

  * a single rule object                     {"rule_id": "...", ...}
  * a list of rule objects                   [ {...}, {...} ]
  * the seed layout                          {"RULES": [...], "CHECKS": [...]}
  * camelCase keys                           {"ruleId": "...", "checkId": "..."}
  * positional arrays                        {"rules": [["PRD-9", "type", null, ...]]}

Field names are paths into the product JSON, e.g. "ProductType.Name" — see
GET /products/lookup -> rule_fields for every path a stored product has.
"""

from typing import Any, Dict, List, Union

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from .. import product_rule_authoring

router = APIRouter(prefix="/product-validation-rules", tags=["Product Validation Rules"])

_EXAMPLE = {
    "rule_id": "PRD-DESCRIPTION-ROMANIZED",
    "validation_type": "Product Master Data - Romanization",
    "description": "Product description must be Latin/ASCII only",
    "severity": "Warning",
    "on_exception_description": "Product description is not romanized",
    "conditions": [
        {"role": "Target", "field": "ProductDefinition.Description", "dataType": "Romanize"}
    ],
}


@router.post(
    "",
    summary="Create / replace product validation rules",
)
def create_validation_rules(
    body: Union[Dict[str, Any], List[Any]] = Body(..., examples=[_EXAMPLE]),
):
    try:
        payload = product_rule_authoring.parse_product_rules_payload(body)
        summary = product_rule_authoring.save_product_validation_rules(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(status_code=201, content=summary)


@router.get(
    "",
    summary="List every row in product_val_rules / _rule_conditions / _checks / _logic",
)
def list_validation_rules():
    return JSONResponse(content=product_rule_authoring.get_all_product_rule_definitions())
