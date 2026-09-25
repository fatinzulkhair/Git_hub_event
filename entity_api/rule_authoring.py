"""
=====================================================
RULE AUTHORING  (POST /validation-rules)
=====================================================
Lets a client create validation rules over the API instead of editing
seed_validation_rules.py.  Whatever shape the request comes in — a single
rule, a bare list, the seed's {"RULES": [...], "CHECKS": [...]} layout,
camelCase keys, or positional arrays — it is normalised here and written
into the same four tables the engine already reads:

    val_rules            (rule_id  PK)
    val_rule_conditions  (no natural key -> replaced per rule_id)
    val_checks           (check_id PK)
    val_logic            (logic_id PK)

Nothing about the rule engine changes; a rule created here is picked up on
the very next /entities/process call (loader has no cache).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .database import connect, query_rows

# ---------------------------------------------------------------------------
# Column order for each table — used to expand positional arrays like
#   ["VAL-007", "Target", "LocationName", null, null, "Romanize", ...]
# into a dict.  Mirrors the header comments in seed_validation_rules.py.
# ---------------------------------------------------------------------------
RULE_COLUMNS = [
    "rule_id", "validation_type", "function_name", "check_id", "description",
    "severity", "active", "on_exception_status", "on_exception_description",
]
CONDITION_COLUMNS = [
    "rule_id", "role", "field_name", "operator", "expected_value", "data_type",
    "constraint_expr", "reference_source", "logical_group",
]
CHECK_COLUMNS = [
    "check_id", "check_type", "field_names", "check_value", "expected_value",
    "reference_source", "field_combine",
]
LOGIC_COLUMNS = ["logic_id", "if_check_id", "then_check_id", "else_check_id"]


def _csv(value: Any) -> Optional[str]:
    """Accept a list *or* a comma string and always store a comma string."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v).strip() for v in value if str(v).strip() != "")
    return str(value)


def _to_yn(value: Any) -> str:
    """Normalise active/enabled into the 'Y' / 'N' the engine expects."""
    if isinstance(value, bool):
        return "Y" if value else "N"
    text = str(value).strip().lower()
    if text in {"y", "yes", "true", "1", "active", "enabled", "on"}:
        return "Y"
    if text in {"n", "no", "false", "0", "inactive", "disabled", "off"}:
        return "N"
    return "Y"


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# DTOs.  Every field lists the aliases we are willing to accept for it so
# snake_case / camelCase / short forms all land in the same place.
# ---------------------------------------------------------------------------
class ConditionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    rule_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("rule_id", "ruleId", "rule"))
    role: str = Field(
        "Target", validation_alias=AliasChoices("role", "kind"))
    field_name: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "field_name", "fieldName", "field", "field_names", "fieldNames"))
    operator: Optional[str] = Field(
        None, validation_alias=AliasChoices("operator", "op"))
    expected_value: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "expected_value", "expectedValue", "expected", "value"))
    data_type: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "data_type", "dataType", "datatype", "type"))
    constraint_expr: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "constraint_expr", "constraintExpr", "constraint", "pattern"))
    reference_source: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "reference_source", "referenceSource", "reference", "source"))
    logical_group: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "logical_group", "logicalGroup", "group", "combine"))

    @field_validator("role", mode="before")
    @classmethod
    def _norm_role(cls, v):
        if v is None:
            return "Target"
        text = str(v).strip().lower()
        if text.startswith("trig"):
            return "Trigger"
        if text.startswith("targ") or text in {"assert", "assertion", "check", "require"}:
            return "Target"
        return str(v).strip().title()

    @field_validator("expected_value", mode="before")
    @classmethod
    def _csv_expected(cls, v):
        return _csv(v)


class CheckIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    check_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("check_id", "checkId", "id", "name"))
    check_type: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "check_type", "checkType", "type", "kind"))
    field_names: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "field_names", "fieldNames", "fields", "field", "field_name"))
    check_value: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "check_value", "checkValue", "value", "operator", "data_type", "dataType"))
    expected_value: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "expected_value", "expectedValue", "expected"))
    reference_source: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "reference_source", "referenceSource", "reference", "source"))
    field_combine: str = Field(
        "AND", validation_alias=AliasChoices(
            "field_combine", "fieldCombine", "combine", "combinator"))

    @field_validator("check_type", mode="before")
    @classmethod
    def _norm_check_type(cls, v):
        if v is None:
            return None
        text = str(v).strip().lower().replace("_", " ").replace("-", " ")
        if text in {"operator", "op", "trigger"}:
            return "Operator"
        if text in {"data type", "datatype", "type", "target"}:
            return "Data Type"
        return str(v).strip()

    @field_validator("field_names", "expected_value", mode="before")
    @classmethod
    def _csv_fields(cls, v):
        return _csv(v)

    @field_validator("field_combine", mode="before")
    @classmethod
    def _norm_combine(cls, v):
        if v is None:
            return "AND"
        return "OR" if str(v).strip().upper() == "OR" else "AND"


class LogicIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    logic_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("logic_id", "logicId", "id", "name"))
    if_check_id: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "if_check_id", "ifCheckId", "if", "if_id", "condition"))
    then_check_id: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "then_check_id", "thenCheckId", "then", "then_id"))
    else_check_id: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "else_check_id", "elseCheckId", "else", "else_id"))


class RuleIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    rule_id: Optional[str] = Field(
        None, validation_alias=AliasChoices("rule_id", "ruleId", "id", "name", "rule"))
    validation_type: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "validation_type", "validationType", "category", "ruleType"))
    function_name: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "function_name", "functionName", "function"))
    check_id: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "check_id", "checkId", "check_ref", "checkRef",
            "logic_id", "logicId", "logic_ref"))
    description: Optional[str] = Field(
        None, validation_alias=AliasChoices("description", "desc", "message"))
    severity: str = Field(
        "Warning", validation_alias=AliasChoices("severity", "level"))
    active: str = Field(
        "Y", validation_alias=AliasChoices(
            "active", "enabled", "is_active", "isActive"))
    on_exception_status: Optional[int] = Field(
        -1, validation_alias=AliasChoices(
            "on_exception_status", "onExceptionStatus", "exception_status",
            "statusCode"))
    on_exception_description: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "on_exception_description", "onExceptionDescription",
            "exception_description", "failure_message", "error"))

    # authoring conveniences -------------------------------------------------
    conditions: List[ConditionIn] = Field(
        default_factory=list, validation_alias=AliasChoices(
            "conditions", "rule_conditions", "ruleConditions"))
    check: Optional[CheckIn] = Field(
        None, validation_alias=AliasChoices("check", "inline_check", "checkDef"))
    logic: Optional[LogicIn] = Field(
        None, validation_alias=AliasChoices("logic", "inline_logic", "logicDef"))

    # flat single-condition shortcut (no explicit conditions/check/logic) ---
    field_name: Optional[str] = Field(
        None, validation_alias=AliasChoices("field_name", "fieldName", "field"))
    role: Optional[str] = Field(None, validation_alias=AliasChoices("role"))
    operator: Optional[str] = Field(
        None, validation_alias=AliasChoices("operator", "op"))
    data_type: Optional[str] = Field(
        None, validation_alias=AliasChoices("data_type", "dataType", "datatype"))
    expected_value: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "expected_value", "expectedValue", "expected"))
    constraint_expr: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "constraint_expr", "constraintExpr", "constraint", "pattern"))
    reference_source: Optional[str] = Field(
        None, validation_alias=AliasChoices(
            "reference_source", "referenceSource"))

    @field_validator("severity", mode="before")
    @classmethod
    def _norm_sev(cls, v):
        return "Warning" if v is None else str(v).strip()

    @field_validator("active", mode="before")
    @classmethod
    def _norm_active(cls, v):
        return _to_yn(v)

    @field_validator("on_exception_status", mode="before")
    @classmethod
    def _norm_status(cls, v):
        return _to_int_or_none(v) if v is not None else -1

    @field_validator("expected_value", mode="before")
    @classmethod
    def _csv_expected(cls, v):
        return _csv(v)


class ValidationRulesPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    rules: List[RuleIn] = Field(
        default_factory=list, validation_alias=AliasChoices(
            "rules", "rule", "RULES", "Rules"))
    conditions: List[ConditionIn] = Field(
        default_factory=list, validation_alias=AliasChoices(
            "conditions", "rule_conditions", "CONDITIONS", "Conditions"))
    checks: List[CheckIn] = Field(
        default_factory=list, validation_alias=AliasChoices(
            "checks", "CHECKS", "Checks"))
    logic: List[LogicIn] = Field(
        default_factory=list, validation_alias=AliasChoices(
            "logic", "logics", "LOGIC", "Logic"))


# ---------------------------------------------------------------------------
# Normalisation:  raw body (dict / list / single rule / positional) -> the
# canonical {"rules": [...], "conditions": [...], ...} dict of plain dicts.
# ---------------------------------------------------------------------------
_SECTION_ALIASES = {
    "rules": {"rules", "rule", "Rules"},
    "conditions": {"conditions", "rule_conditions", "ruleconditions", "Conditions"},
    "checks": {"checks", "Checks"},
    "logic": {"logic", "logics", "Logic"},
}
_SECTION_COLUMNS = {
    "rules": RULE_COLUMNS,
    "conditions": CONDITION_COLUMNS,
    "checks": CHECK_COLUMNS,
    "logic": LOGIC_COLUMNS,
}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _expand_positional(item: Any, columns: List[str]) -> Any:
    """['id', 'Target', ...]  ->  {'rule_id': 'id', 'role': 'Target', ...}"""
    if isinstance(item, (list, tuple)):
        return {columns[i]: v for i, v in enumerate(item) if i < len(columns)}
    return item


def _match_section(key: str) -> Optional[str]:
    low = key.strip().lower()
    for canonical, aliases in _SECTION_ALIASES.items():
        if low in {a.lower() for a in aliases}:
            return canonical
    return None


def normalize_payload(body: Any) -> Dict[str, List[dict]]:
    """Turn any accepted request shape into
    {"rules": [...], "conditions": [...], "checks": [...], "logic": [...]}."""
    # a bare list -> a list of rules
    if isinstance(body, list):
        body = {"rules": body}

    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object or array")

    section_of = {k: _match_section(k) for k in body}
    non_section_keys = [k for k, v in section_of.items() if v is None]
    has_rules_section = any(v == "rules" for v in section_of.values())

    # It's a "container" (rules/conditions/checks/logic arrays) only if it has
    # an explicit rules[] section, or *every* key is a section. Otherwise the
    # object is itself one rule — and a "conditions" key on it belongs to that
    # rule, it is not a top-level section.
    is_container = has_rules_section or (bool(body) and not non_section_keys)

    if not is_container:
        rule_part = {
            k: v for k, v in body.items() if section_of[k] not in ("checks", "logic")
        }
        container: Dict[str, Any] = {"rules": [rule_part]}
        for k, v in body.items():           # keep co-submitted checks/logic arrays
            if section_of[k] in ("checks", "logic"):
                container[section_of[k]] = v
        body = container
        section_of = {k: _match_section(k) for k in body}

    canonical: Dict[str, List[dict]] = {
        "rules": [], "conditions": [], "checks": [], "logic": [],
    }
    for raw_key, canon in section_of.items():
        if canon is None:
            continue
        for item in _as_list(body[raw_key]):
            item = _expand_positional(item, _SECTION_COLUMNS[canon])
            if canon == "rules" and isinstance(item, dict):
                item = dict(item)
                for cond_key in ("conditions", "rule_conditions", "ruleConditions"):
                    if cond_key in item:
                        item[cond_key] = [
                            _expand_positional(c, CONDITION_COLUMNS)
                            for c in _as_list(item[cond_key])
                        ]
            canonical[canon].append(item)
    return canonical


def parse_rules_payload(body: Any) -> ValidationRulesPayload:
    return ValidationRulesPayload.model_validate(normalize_payload(body))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _gen_id(kind: str) -> str:
    return f"api-{kind}-{uuid.uuid4().hex[:8]}"


def save_validation_rules(payload: ValidationRulesPayload) -> dict:
    """Write the parsed payload into the val_* tables. Returns a summary."""
    checks: Dict[str, CheckIn] = {}
    logic: Dict[str, LogicIn] = {}
    rules: List[Dict[str, Any]] = []
    conditions: List[Dict[str, Any]] = []
    generated: List[str] = []
    warnings: List[str] = []

    # top-level checks / logic first
    for chk in payload.checks:
        cid = chk.check_id or _gen_id("check")
        if not chk.check_id:
            generated.append(cid)
        checks[cid] = chk
    for lg in payload.logic:
        lid = lg.logic_id or _gen_id("logic")
        if not lg.logic_id:
            generated.append(lid)
        logic[lid] = lg

    # rules (+ any nested check / logic / conditions / flat shortcut)
    for rule in payload.rules:
        rid = rule.rule_id or _gen_id("rule")
        pointer = rule.check_id

        if rule.check is not None:
            cid = rule.check.check_id or _gen_id("check")
            if not rule.check.check_id:
                generated.append(cid)
            checks[cid] = rule.check
            pointer = pointer or cid
        if rule.logic is not None:
            lid = rule.logic.logic_id or _gen_id("logic")
            if not rule.logic.logic_id:
                generated.append(lid)
            logic[lid] = rule.logic
            pointer = pointer or lid

        rule_conditions = list(rule.conditions)

        # flat shortcut: a bare field_name/data_type/operator on the rule
        if not pointer and not rule_conditions and (
            rule.field_name or rule.data_type or rule.operator
        ):
            rule_conditions.append(ConditionIn(
                role=rule.role or "Target",
                field_name=rule.field_name,
                operator=rule.operator,
                data_type=rule.data_type,
                expected_value=rule.expected_value,
                constraint_expr=rule.constraint_expr,
                reference_source=rule.reference_source,
            ))

        # a completely empty object (e.g. an unrecognised body) — don't
        # materialise a vacuous always-pass rule for it
        if (not rule.rule_id and not pointer and not rule_conditions
                and not rule.description and not rule.validation_type
                and not rule.function_name):
            warnings.append("ignored an empty rule object (no id / check / conditions)")
            continue

        if not rule.rule_id:
            generated.append(rid)

        for cond in rule_conditions:
            row = cond.model_dump()
            row["rule_id"] = rid
            conditions.append(row)

        if not pointer and not rule_conditions:
            warnings.append(
                f"rule '{rid}' has no check_id and no conditions — it will "
                f"always pass"
            )

        rules.append({
            "rule_id": rid,
            "validation_type": rule.validation_type,
            "function_name": rule.function_name,
            "check_id": pointer,
            "description": rule.description,
            "severity": rule.severity,
            "active": rule.active,
            "on_exception_status": rule.on_exception_status,
            "on_exception_description": rule.on_exception_description,
        })

    # top-level conditions: need a rule_id, or exactly one rule to attach to
    lone_rule = rules[0]["rule_id"] if len(rules) == 1 else None
    for cond in payload.conditions:
        row = cond.model_dump()
        row["rule_id"] = row.get("rule_id") or lone_rule
        if not row["rule_id"]:
            warnings.append(
                "a top-level condition was dropped: no rule_id and more than "
                "one rule in the payload"
            )
            continue
        conditions.append(row)

    if not rules and not checks and not logic and not conditions:
        raise ValueError(
            "Nothing to save: the payload had no recognisable rules, checks, "
            "conditions or logic"
        )

    _write(rules, conditions, checks, logic)

    return {
        "rules_written": [r["rule_id"] for r in rules],
        "conditions_written": len(conditions),
        "checks_written": list(checks),
        "logic_written": list(logic),
        "generated_ids": generated,
        "warnings": warnings,
    }


def _write(rules, conditions, checks, logic) -> None:
    with connect() as conn:
        cur = conn.cursor()

        if checks:
            cur.executemany(
                """
                INSERT OR REPLACE INTO val_checks
                (check_id, check_type, field_names, check_value, expected_value,
                 reference_source, field_combine)
                VALUES (:cid, :check_type, :field_names, :check_value,
                        :expected_value, :reference_source, :field_combine)
                """,
                [{"cid": cid, **c.model_dump(exclude={"check_id"})}
                 for cid, c in checks.items()],
            )

        if logic:
            cur.executemany(
                """
                INSERT OR REPLACE INTO val_logic
                (logic_id, if_check_id, then_check_id, else_check_id)
                VALUES (:lid, :if_check_id, :then_check_id, :else_check_id)
                """,
                [{"lid": lid, **row.model_dump(exclude={"logic_id"})}
                 for lid, row in logic.items()],
            )

        if rules:
            cur.executemany(
                """
                INSERT OR REPLACE INTO val_rules
                (rule_id, validation_type, function_name, check_id, description,
                 severity, active, on_exception_status, on_exception_description)
                VALUES (:rule_id, :validation_type, :function_name, :check_id,
                        :description, :severity, :active, :on_exception_status,
                        :on_exception_description)
                """,
                rules,
            )

        if conditions:
            # conditions have no natural key: replace the whole set per rule_id
            touched = sorted({c["rule_id"] for c in conditions})
            cur.executemany(
                "DELETE FROM val_rule_conditions WHERE rule_id = ?",
                [(rid,) for rid in touched],
            )
            cur.executemany(
                """
                INSERT INTO val_rule_conditions
                (rule_id, role, field_name, operator, expected_value, data_type,
                 constraint_expr, reference_source, logical_group)
                VALUES (:rule_id, :role, :field_name, :operator, :expected_value,
                        :data_type, :constraint_expr, :reference_source,
                        :logical_group)
                """,
                conditions,
            )


def get_all_rule_definitions() -> dict:
    """Everything currently in the four rule tables (for GET /validation-rules)."""
    with connect() as conn:
        return {
            "rules": query_rows(
                "SELECT * FROM val_rules ORDER BY rule_id", conn=conn),
            "conditions": query_rows(
                "SELECT * FROM val_rule_conditions ORDER BY rule_id, id", conn=conn),
            "checks": query_rows(
                "SELECT * FROM val_checks ORDER BY check_id", conn=conn),
            "logic": query_rows(
                "SELECT * FROM val_logic ORDER BY logic_id", conn=conn),
        }
