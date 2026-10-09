"""
Run once (or after wiping the product_val_* tables) to load the example
product rule set into the database:

    python product_seed_validation_rules.py

Safe to re-run: the rules are saved by the same code as
POST /product-validation-rules, which does INSERT OR REPLACE keyed on
rule_id / check_id / logic_id and replaces each rule's conditions as a set.
PRODUCT_RULES_PAYLOAD below is therefore also a valid body for that endpoint.

Field names are paths into the product JSON (e.g. "ProductType.Name"); see
GET /products/lookup -> rule_fields for every path a stored product has.
"""

from product_api.product_database import initialize_product_database
from product_api.product_rule_authoring import (
    parse_product_rules_payload,
    save_product_validation_rules,
)

# Design markets in the EU (+ UK), where the TPD identifiers are mandatory.
# A DesignMarketCode starts with the country code, e.g. "FR04".
PRODUCT_EU_UK_MARKETS = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "GB", "UK",
]

PRODUCT_CHECKS = [
    {
        "checkId": "chk_product_required_fields",
        "checkType": "Data Type",
        "fieldNames": [
            "Customer.Code",
            "ProductType.Name",
            "ProductDefinition.ProductDefinitionKey",
            "ProductDefinition.Description",
        ],
        "checkValue": "Exist",
        "fieldCombine": "AND",
    },
    {
        "checkId": "chk_product_eu_uk_market",
        "checkType": "Operator",
        "fieldNames": "ProductDefinition.ProductDefinitionProperties.DesignMarketCode",
        "checkValue": "starts_with",
        "expectedValue": PRODUCT_EU_UK_MARKETS,
    },
    {
        "checkId": "chk_product_tpd_identifiers",
        "checkType": "Data Type",
        "fieldNames": ["ProductProperties.TPPN", "ProductProperties.TPID"],
        "checkValue": "Exist",
        "fieldCombine": "AND",
    },
]

PRODUCT_LOGIC = [
    # IF the design market is in the EU/UK, THEN TPPN + TPID must exist, ELSE pass.
    {
        "logicId": "logic_product_eu_tpd",
        "if": "chk_product_eu_uk_market",
        "then": "chk_product_tpd_identifiers",
        "else": "True",
    },
]

PRODUCT_RULES = [
    {
        "ruleId": "PRD-KEY-FORMAT",
        "validationType": "Product Master Data - Key Format",
        "description": "PrimaryProductKey must be an inexto product URN",
        "severity": "Critical",
        "onExceptionDescription": "PrimaryProductKey is not urn:inexto:id:prd:...",
        "conditions": [
            {"role": "Target", "field": "PrimaryProductKey", "dataType": "Regex",
             "constraint": r"^urn:inexto:id:prd:.+$"},
        ],
    },
    {
        "ruleId": "PRD-REQUIRED-FIELDS",
        "validationType": "Product Master Data - Completeness",
        "description": "Customer code, product type, definition key and description must be present",
        "severity": "Critical",
        "onExceptionDescription": "Missing Customer.Code / ProductType.Name / "
                                  "ProductDefinitionKey / Description",
        "checkId": "chk_product_required_fields",
    },
    {
        "ruleId": "PRD-TYPE-ENUM",
        "validationType": "Product Master Data - Packaging Level",
        "description": "ProductType.Name must be a known packaging level",
        "severity": "Warning",
        "onExceptionDescription": "Unknown ProductType (expected Shipping Case, Bundle or Pack)",
        "conditions": [
            {"role": "Target", "field": "ProductType.Name", "dataType": "Enum",
             "expected": ["Shipping Case", "Bundle", "Pack"]},
        ],
    },
    {
        "ruleId": "PRD-CHILD-QUANTITY",
        "validationType": "Product Master Data - Hierarchy",
        "description": "A child product must say how many of it one parent holds (at least 1)",
        "severity": "Critical",
        "onExceptionDescription": "Child product Quantity is missing or below 1",
        "conditions": [
            {"role": "Trigger", "field": "ParentProductKey", "operator": "exists"},
            {"role": "Target", "field": "Quantity", "dataType": "Number",
             "constraint": "min=1"},
        ],
    },
    {
        "ruleId": "PRD-PACK-GTIN",
        "validationType": "Product Master Data - Identification",
        "description": "A Pack must carry a GTIN alternate key",
        "severity": "Warning",
        "onExceptionDescription": "Pack has no urn:inexto:id:prd:gtin:<14 digits> alternate key",
        "conditions": [
            {"role": "Trigger", "field": "ProductType.Name", "operator": "equals",
             "expected": "Pack"},
            {"role": "Target", "field": "AlternateProductKeys.ProductKey", "dataType": "Regex",
             "constraint": r"urn:inexto:id:prd:gtin:\d{14}"},
        ],
    },
    {
        "ruleId": "PRD-EU-TPD",
        "validationType": "Product Master Data - EU TPD",
        "description": "If the design market is in the EU/UK, TPPN and TPID must both be "
                       "present; otherwise the rule passes automatically",
        "severity": "Critical",
        "onExceptionDescription": "Missing TPPN/TPID for an EU/UK design market",
        "checkId": "logic_product_eu_tpd",
    },
    {
        "ruleId": "PRD-TRACKING-DATE",
        "validationType": "Product Master Data - Dates",
        "description": "RelevantForTrackingSince, when given, must be a YYYY-MM-DD date",
        "severity": "Warning",
        "onExceptionDescription": "RelevantForTrackingSince is not a YYYY-MM-DD date",
        "conditions": [
            {"role": "Trigger", "field": "ProductProperties.RelevantForTrackingSince",
             "operator": "exists"},
            {"role": "Target", "field": "ProductProperties.RelevantForTrackingSince",
             "dataType": "Date", "constraint": "format=%Y-%m-%d"},
        ],
    },
    {
        "ruleId": "PRD-KEY-UNIQUE",
        "validationType": "Product Master Data - Duplicates",
        "description": "The same PrimaryProductKey must not appear twice in one payload",
        "severity": "Critical",
        "onExceptionDescription": "PrimaryProductKey repeated within the payload",
        "conditions": [
            {"role": "Target", "field": "PrimaryProductKey", "dataType": "Unique",
             "referenceSource": "batch"},
        ],
    },
]

PRODUCT_RULES_PAYLOAD = {
    "rules": PRODUCT_RULES,
    "checks": PRODUCT_CHECKS,
    "logic": PRODUCT_LOGIC,
}


def seed():
    initialize_product_database()
    summary = save_product_validation_rules(
        parse_product_rules_payload(PRODUCT_RULES_PAYLOAD)
    )
    print(
        f"Seeded {len(summary['rules_written'])} rules, "
        f"{summary['conditions_written']} conditions, "
        f"{len(summary['checks_written'])} checks, "
        f"{len(summary['logic_written'])} logic rows."
    )
    for warning in summary["warnings"]:
        print("warning:", warning)


if __name__ == "__main__":
    seed()
