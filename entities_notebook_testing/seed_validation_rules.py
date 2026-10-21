"""
Run once (or after wiping the entities_val_* tables) to load the example
rule set from validation_documentation.md into the database:

    python entities_notebook_testing/seed_validation_rules.py

Works from any folder: the project root is put on the import path, and the
database comes from server_testing/config.json (see entity_api/sql_server.py).

The rows below are saved exactly as a POST /validation-rules body would be
(rule_authoring.save_validation_rules), so the seed and the API write the
tables the same way. Safe to re-run: rules, checks and logic are upserted by
their id, and a seeded rule's conditions are replaced as a set.
"""

import os
import sys

# This script lives in entities_notebook_testing/, one level below the
# project root that holds the entity_api package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entity_api.database import initialize_database  # noqa: E402
from entity_api.rule_authoring import parse_rules_payload, save_validation_rules  # noqa: E402

RULES = [
    # rule_id, validation_type, function_name, check_id, description, severity, active, on_exception_status, on_exception_description
    # ("VAL-001", "Location Master Data – EoId, FID", "EoIDFiDValidation", None,
    #  "If the location address is in the EU/UK country, then this is a mandatory OR if the "
    #  "location would produce for EU/UK Market, then also it is mandatory for this fields to be present",
    #  "Critical", "Y", -1, "Missing EoID/FiD")
    # ,
    # ("VAL-002", "Conditional Field – ACDC UK mapping", None, None,
    #  "UKEOID/UKFID are mandatory when systemCode = ACDC", "Critical", "Y", -1,
    #  "Missing UKEOID/UKFID for ACDC"),
    # ("VAL-003", "SGLN Format Check", None, None,
    #  "SGLN must follow the format urn:epc:id:sgln:...", "Critical", "Y", -1, "SGLN format invalid"),
    # ("VAL-004", "System Code Presence", None, None,
    #  "systemCode is mandatory for every record", "Critical", "Y", -1, "Missing systemCode"),
    # ("VAL-005", "EOID Uniqueness", None, None,
    #  "EOID must not duplicate another already-processed record", "Critical", "Y", -1, "Duplicate EOID"),
    # ("VAL-006", "Location Master Data – EoId, FID", None, None,
    #  "No two locations can have the same FiD", "Critical", "Y", -2, "Duplicate FiD"),
    # ("VAL-007", "Location Master Data – Location Name", None, None,
    #  "Location name must be romanized (Latin/ASCII characters only). NOTE: 'LocationName' is a "
    #  "placeholder field -- adjust to match the actual field name in your data.",
    #  "Warning", "Y", -1, "Location name contains non-Latin characters"),
    (
        "VAL-UK-Field",
        "Location Master Data – UK Field Completeness",
        None,
        "val_logic_1_UK_field",
        "If Country starts with a UK/GB code, then UK_TPD, UI_ID and UKSLL must all exist; "
        "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "Missing UK_TPD/UI_ID/UKSLL for UK location",
    ),
    (
        "FIELD-EXIST",
        "Location Master Data – UK Field Completeness",
        None,
        "val_operator_1",
        "If Country starts with a UK/GB code, then UK_TPD, UI_ID and UKSLL must all exist; "
        "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "Missing UK_TPD/UI_ID/UKSLL for UK location",
    ),
    (
        "FIELD-EOID-Unique",
        "Location Master Data – UK Field Completeness",
        None,
        "val_operation_uniq_1",
        "If Country starts with a UK/GB code, then UK_TPD, UI_ID and UKSLL must all exist; "
        "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "Missing EOID is not unique for UK location",
    ),
    (
        "Country_exist",
        "Location Master Data is exist for this code",
        None,
        "val_operation_country_1",
        "check are the country is exist" "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "country is not exist",
    ),
    (
        "Country_exist",
        "Location Master Data is exist for this code",
        None,
        "val_operation_country_1",
        "check are the country is exist" "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "country is not exist",
    ),
    (
        "city_exist",
        "Location Master Data is exist for this code",
        None,
        "val_operation_city1_1",
        "check are the city is exist" "otherwise the rule passes automatically",
        "Critical",
        "Y",
        -1,
        "city is not exist",
    ),
]

CONDITIONS = [
    # rule_id, role, field_name, operator, expected_value, data_type, constraint_expr, reference_source, logical_group
    # ("VAL-001", "Target", "EOID", None, None, "Exist", None, None, None),
    # ("VAL-001", "Target", "FID", None, None, "Exist", None, None, None),
    # ("VAL-002", "Trigger", "systemCode", "equals", "ACDC", None, None, None, "AND"),
    # ("VAL-002", "Target", "UKEOID", None, None, "Exist", None, None, None),
    # ("VAL-002", "Target", "UKFID", None, None, "Exist", None, None, None),
    # (
    #     "VAL-003",
    #     "Target",
    #     "SGLN",
    #     None,
    #     None,
    #     "Regex",
    #     "^urn:epc:id:sgln:.+$",
    #     None,
    #     None,
    # ),
    # ("VAL-004", "Target", "systemCode", None, None, "Exist", None, None, None),
    # ("VAL-005", "Target", "EOID", None, None, "Unique", None, "batch", None),
    # ("VAL-006", "Target", "FID", None, None, "Unique", None, "batch", None),
    # ("VAL-007", "Target", "LocationName", None, None, "Romanize", None, None, None),
]

CHECKS = [
    # check_id, check_type, field_names, check_value, expected_value, reference_source, field_combine
    ("val_operator_1", "Data Type", "UKEOID,UKFID,EOID", "Exist", None, None, "AND"),
    (
        "val_operator_2",
        "Operator",
        "country",
        "starts_with",
        "AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IE,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE,GB,UK",
        None,
        "AND",
    ),
    ("val_operation_uniq_1", "Data Type", "EOID", "Unique", None, None, "AND"),
    ("val_operation_country_1", "Data Type", "country", "Exist", None, None, "AND"),
    ("val_operation_city1_1", "Data Type", "city", "Exist", None, None, "AND"),
    # ("val_operator_3", "Operator", "Market", "in",
    #  "AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IE,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE,GB,UK",
    #  None, "AND"),
    # ("val_operator_4", "Data Type", "SGLN", "Regex", "^urn:epc:id:sgln:.+$", None, "AND"),
    # ("val_operator_5", "Data Type", "EOID,FID", "Exist", None, None, "AND"),
]

LOGIC = [
    # logic_id, if_check_id, then_check_id, else_check_id
    ("val_logic_1_UK_field", "val_operator_2", "val_operator_1", "True"),
    # ("logic_country_or_market", "val_operator_2", "True", "val_operator_3"),
    # ("logic_sgln_and_ids", "val_operator_4", "val_operator_5", "False"),
    # ("logic_final_eu_check", "logic_country_or_market", "logic_sgln_and_ids", "True"),
]


def seed():
    initialize_database()
    summary = save_validation_rules(parse_rules_payload(
        {"rules": RULES, "conditions": CONDITIONS, "checks": CHECKS, "logic": LOGIC}
    ))
    print(
        f"Seeded {len(summary['rules_written'])} rules, "
        f"{summary['conditions_written']} conditions, "
        f"{len(summary['checks_written'])} checks, "
        f"{len(summary['logic_written'])} logic rows."
    )
    for warning in summary["warnings"]:
        print("  warning:", warning)


if __name__ == "__main__":
    seed()
