# Entity Comparison API

A **FastAPI**-based API for storing JSON entities into SQLite, automatically detecting duplicates using a **SHA256 hash**, and validating every entity through a **dynamic rule engine** whose rules live in the database instead of being hardcoded in Python.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Installation](#installation)
3. [Running the Server](#running-the-server)
4. [API Endpoints](#api-endpoints)
5. [Duplicate Detection](#duplicate-detection)
6. [Dynamic Rule Engine](#dynamic-rule-engine)
7. [Database Schema](#database-schema)
8. [Adding a New Rule (no coding required)](#adding-a-new-rule-no-coding-required)
9. [Notes & Legacy Modules](#notes--legacy-modules)

---

## Project Structure

```
entity_api/
├── requirements.txt
├── run.py                          # Entry point: python run.py
├── seed_validation_rules.py        # Initial migration: seeds the val_* tables with the example rules
├── entity_database.db              # Created automatically the first time the server runs
└── entity_api/
    ├── __init__.py
    ├── config.py                   # Global constants (DB name, API title, version, etc.)
    ├── database.py                 # SQLite connection & creation of every table
    ├── hashing.py                  # generate_entity_hash()
    ├── schemas.py                  # Pydantic models for Swagger documentation
    ├── crud.py                     # Insert, duplicate check, payload processing, queries
    ├── validation.py               # Bridge into the dynamic rule engine
    ├── main.py                     # App factory: builds FastAPI(), includes router, startup event
    ├── routers/
    │   ├── __init__.py
    │   └── entities.py             # All /entities/... endpoints
    └── validation_engine/
        ├── __init__.py
        ├── loader.py                # Reads val_rules / val_rule_conditions / val_checks / val_logic
        ├── operators.py             # Implements the 9 Operators (equals, in, regex, etc.)
        ├── datatypes.py             # Implements the 10 Data Types (Exist, Number, Romanize, Unique, etc.)
        ├── resolvers.py             # Resolves a Check ID / Logic ID recursively (IF/THEN/ELSE)
        ├── conditions.py            # Evaluates rules that use Rule Conditions directly (no Check/Logic ID)
        ├── rule_engine.py           # Orchestrator: runs every active rule against one entity
        ├── Location.py              # (legacy, no longer called — see the note below)
        └── romanize.py              # (legacy, no longer called — see the note below)
```

| File / Module | Responsibility |
|---|---|
| `config.py` | All global constants in one place |
| `database.py` | SQLite connection & schema only |
| `hashing.py` | Pure function that generates a SHA256 hash for an entity |
| `schemas.py` | Pydantic models to keep the Swagger docs clean |
| `crud.py` | Business logic: insert entity, detect duplicates, join location master data, process payload, query |
| `validation.py` | Calls `rule_engine.run_all_rules()` for every newly inserted entity |
| `routers/entities.py` | HTTP endpoint definitions (routing), delegates logic to `crud.py` |
| `main.py` | Wires all modules together into a single FastAPI `app` |
| `validation_engine/loader.py` | Reads the four rule tables from the database |
| `validation_engine/operators.py` | Evaluates Operators (Trigger rows, or a Check of type "Operator") |
| `validation_engine/datatypes.py` | Evaluates Data Types (Target rows, or a Check of type "Data Type") |
| `validation_engine/resolvers.py` | Resolves a Check ID / Logic ID, including Logic → Logic chaining |
| `validation_engine/conditions.py` | Evaluates rules defined directly through Rule Conditions |
| `validation_engine/rule_engine.py` | Loops every active rule, writes results to `validation_results` |

---

## Installation

Make sure Python 3.10+ is installed, then:

```bash
# (optional) create a virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# install dependencies
pip install -r requirements.txt
```

`requirements.txt`:
```
fastapi
uvicorn[standard]
pandas
python-multipart
anyascii
regex
sqlalchemy
pyodbc
```

---

## Running the Server

```bash
python run.py
```

or directly via uvicorn:

```bash
uvicorn entity_api.main:app --reload
```

The server runs at `http://localhost:8000`. Interactive API documentation (Swagger UI) is automatically available at:

```
http://localhost:8000/docs
```

The SQLite database (`entity_database.db`) is created automatically in the project root the first time the server starts, including every table used by the rule engine.

### Seeding the example rules (one-time)

Before processing data, run this once to load the 8 example rules (VAL-001 through VAL-008) into the database:

```bash
python seed_validation_rules.py
```

The script is safe to run more than once (idempotent) — it overwrites (`INSERT OR REPLACE`) rows sharing the same `rule_id` / `check_id` / `logic_id`.

---

## API Endpoints

### `POST /entities/process`
Send a JSON payload directly in the request body.

```bash
curl -X POST http://localhost:8000/entities/process \
  -H "Content-Type: application/json" \
  -d '{
        "mappingsInformation": [
          {
            "systemCode": "SYS1",
            "businessEntityCode": "BE001",
            "EOID": "E1",
            "FID": "F1",
            "UKEOID": "UE1",
            "UKFID": "UF1",
            "SGLN": "urn:epc:id:sgln:0614141.00777.0"
          }
        ]
      }'
```

The payload can also be a single entity sent directly (without the `mappingsInformation` wrapper), as long as it has a `systemCode` field.

**Response:**
```json
{
  "total_entities": 1,
  "inserted": 1,
  "skipped": 0,
  "details": [
    { "status": "inserted", "id": 1, "businessEntityCode": "BE001" }
  ]
}
```

If the same entity is sent again:
```json
{ "status": "skipped", "reason": "already_exists", "businessEntityCode": "BE001", "existing_id": 1 }
```

### `POST /entities/upload-file`
Upload a `.json` file (multipart/form-data) with the same structure as above.

```bash
curl -X POST http://localhost:8000/entities/upload-file \
  -F "file=@data.json"
```

### `POST /entities/inputlocation`
Send a JSON array of location master data used by rules that need a country reference (e.g. an EU/UK check). Each item needs `systemCode`, `Code` (stored as `businessEntityCode`) and `Country`; `Name`, `Type`, `Alternate_code`, `City`, `Zip` are optional.

At validation time each entity is **joined** with its `entities_location` row on the composite key `(systemCode, businessEntityCode)`, so rules can reference location fields (`Country`, `City`, …) as if they were part of the entity. Post locations **before** the matching entities — the join runs synchronously while each entity is inserted, and an entity is not re-validated once stored.

### `GET /entities`
Lists all stored entities (summary columns).

```bash
curl http://localhost:8000/entities
```

### `GET /entities/locations`
Lists all rows in `entities_location` (the master data joined into each entity for validation).

```bash
curl http://localhost:8000/entities/locations
```

### `GET /entities/validation-results`
Shows the dynamic rule engine's results — one row per (entity, rule) pair, with pass/fail status, severity, and message.

```bash
curl http://localhost:8000/entities/validation-results
```

### `GET /entities/validation`
The old (legacy) endpoint that joins the `romanize` and `Location_validation` tables. Kept for backward compatibility, but no longer populated by the current validation flow — see [Notes & Legacy Modules](#notes--legacy-modules).

### `GET /entities/{business_entity_code}`
Looks up an entity by `businessEntityCode`. Returns `404` if not found.

```bash
curl http://localhost:8000/entities/BE001
```

---

## Duplicate Detection

Each entity is serialized to JSON with `sort_keys=True`, then hashed with SHA256. That hash is stored as a `UNIQUE` column in the `entities` table. If a new entity produces the exact same hash, it's automatically skipped — not treated as an error, just marked `status: skipped`.

---

## Dynamic Rule Engine

Previously, adding a validation rule meant writing a new Python function (`eu_detect`, `romanizecheck`) and wiring it manually into `validation_rules_engine`. Now, every rule is stored in **four database tables**, and a generic engine reads and executes them.

### High-level flow

```
crud.process_payload()
   └─ for each entity in the batch:
        insert_hystoryentity(entity)               # store + compute hash
        get_location_for(systemCode, businessEntityCode)   # entities_location row (or {})
        entity_joined = {**location, **entity}      # entity_json wins on name clash
        validation_rules_engine(entity_joined, id, hash)   # -> rule_engine.run_all_rules()
             └─ for each ACTIVE rule in val_rules:
                  if the rule has a Check ID  -> resolvers.resolve(check_id)
                  otherwise                   -> conditions.evaluate_rule_conditions(rule_id)
                  the outcome (pass/fail) is written to the validation_results table
```

Rules therefore see one flat dict combining the entity and its location master data. If no `entities_location` row matches, the location fields are simply absent (checks on them fail or fall through their `ELSE` branch, exactly as for any missing field).

Field names in rules are matched **case-insensitively** (`country`, `Country` and `COUNTRY` all resolve to the same value), since the entity columns and the joined location columns don't share a casing convention. An exact-case match still wins when both spellings are present.

### 1. `val_rules` table (Rules)

The main rule list. Key columns:

| Column | Meaning |
|---|---|
| `rule_id` | Unique ID, e.g. `VAL-001` |
| `check_id` | (optional) points to one row in `val_checks` **or** `val_logic`. If empty, the rule is evaluated via `val_rule_conditions` |
| `severity` | `Critical` / `Warning`, etc. — free text, only kept for reporting |
| `active` | `Y`/`N` — rules with `N` are ignored entirely by the engine |
| `on_exception_status`, `on_exception_description` | Used to populate `status` & `description` in `validation_results` when the rule fails |

### 2. `val_rule_conditions` table (Rule Conditions)

Used by rules that do **not** set `check_id` (the most common case). One rule can have several rows:

- **Role = Trigger** — the condition for the rule to apply at all. All Trigger rows for one `rule_id` are AND'd together. If the trigger fails, the rule is considered **not applicable** to that entity and automatically passes.
- **Role = Target** — what must hold true once the rule applies. All Target rows are AND'd together, evaluated via the `Data Type` column.

The `Operator` column is used on Trigger rows; the `Data Type` column is used on Target rows.

### 3. `val_checks` table (Checks)

Reusable, ID-addressable checks that can be referenced repeatedly (from `val_rules.check_id` or from `val_logic`). A single Check can inspect several fields at once (`field_names`, comma-separated), combined via `field_combine` (`AND`/`OR`).

`check_type` determines whether `check_value` is read as an **Operator** name or a **Data Type** name.

### 4. `val_logic` table (Logic)

IF / THEN / ELSE compositions of Check IDs or other Logic IDs (supports **chaining**):

```
val_logic_1_UK_field :  IF val_operator_2  THEN val_operator_1  ELSE True
logic_country_or_market :  IF val_operator_2  THEN True              ELSE val_operator_3   # equivalent to A OR B
logic_sgln_and_ids      :  IF val_operator_4  THEN val_operator_5     ELSE False            # equivalent to A AND B
logic_final_eu_check    :  IF logic_country_or_market  THEN logic_sgln_and_ids  ELSE True   # combining more than one logic
```

`resolvers.resolve(id)` accepts a Check ID, a Logic ID, or the literal `"True"`/`"False"`, and automatically figures out which table to look in — including when If/Then/Else points at another Logic ID (recursively).

### Operator List

| Operator | Meaning |
|---|---|
| `equals` | The field value must exactly match the Expected Value |
| `not_equals` | The field value must differ from the Expected Value |
| `in` | The field value must be present in the (comma-separated) Expected Value list |
| `not_in` | The field value must NOT be present in the Expected Value list |
| `contains` | The Expected Value must appear as a substring of the field value |
| `starts_with` | The field value must start with the Expected Value |
| `ends_with` | The field value must end with the Expected Value |
| `regex` | The field value must match the regex pattern in the Expected Value |
| `exists` | The field must be non-empty/non-null; the Expected Value is ignored |

> If the Expected Value contains more than one value (comma-separated), every operator **except `in`/`not_in`** is automatically checked with **OR** logic across each value. Example: `starts_with` = `"UK,GB"` means *"starts with UK OR starts with GB"*.

### Data Type List

| Data Type | Optional Constraint |
|---|---|
| `Exist` | no constraint needed |
| `Text` | `pattern=alpha_only` |
| `Number` | `type=int|float;min=<n>;max=<n>` |
| `Boolean` | no constraint needed (`true/false/Y/N/1/0` are all accepted) |
| `Date` | `format=%Y-%m-%d` (default `%Y-%m-%d`) |
| `Enum` | comma-separated list of allowed values |
| `Regex` | a regex pattern |
| `Romanize` | no constraint needed (checks that characters are Latin/ASCII) |
| `Length` | `min=<n>;max=<n>` |
| `Unique` | uses the `Reference Source` column: `batch` or `database.<table>.<column>` |

A `Unique` check with `Reference Source = batch` compares values across entities **within the same payload** (shared via one `ctx` dict per batch in `crud.process_payload`), not just against data already in the database.

### Example: the 8 built-in rules (`seed_validation_rules.py`)

| Rule ID | Short description |
|---|---|
| VAL-001 | EOID & FID must be present |
| VAL-002 | If `systemCode = ACDC`, `UKEOID`/`UKFID` must be present |
| VAL-003 | `SGLN` must follow the format `urn:epc:id:sgln:...` |
| VAL-004 | `systemCode` must be present |
| VAL-005 | `EOID` must not duplicate another record in the same batch |
| VAL-006 | `FID` must not duplicate another record in the same batch |
| VAL-007 | The location name must be romanized (Latin/ASCII) |
| VAL-008 | If `Country` starts with `UK`/`GB`, then `UK_TPD`, `UI_ID`, `UKSLL` must all be present (example of Logic ID chaining) |

---

## Database Schema

Created automatically by `database.initialize_database()` on startup.

**Data tables:**
- `entities` — successfully inserted entities (plus a unique `entity_hash`)
- `entities_location` — location master data (used by rules that need a country reference)

**Rule engine tables (dynamic):**
- `val_rules`, `val_rule_conditions`, `val_checks`, `val_logic` — rule definitions
- `validation_results` — the outcome of each (entity, rule) pair: `passed`, `severity`, `status`, `description`

**Legacy tables (see note below):**
- `romanize`, `Location_validation` — no longer written to by the current validation flow

---

## Adding a New Rule (no coding required)

1. **A simple rule (Exist/Regex/Unique/etc. on one or two fields):** add one row to `val_rules`, then add Trigger (optional) and Target rows to `val_rule_conditions` sharing the same `rule_id`. No need to set `check_id`.
2. **A rule with IF/THEN/ELSE logic, or one you want to reuse in other rules:** create a row in `val_checks` for each atomic check, combine them via `val_logic`, then set `val_rules.check_id` to that Logic ID or Check ID.
3. Set `active = 'Y'`. The rule is active on the very next request — no server restart required.

There are no CRUD endpoints for these four tables yet; edit them directly via SQLite or through a migration script like `seed_validation_rules.py`.

---

## Notes & Legacy Modules

- `validation_engine/Location.py` (the `eu_detect` function) and `validation_engine/romanize.py` (the `romanizecheck` function) are the **old** implementation, from before the dynamic rule engine existed. Neither is called anymore by `validation.py` — everything they used to do is now expressed as rules in `val_rules`/`val_checks`/`val_logic` (see VAL-007 for romanize, and `val_logic_1_UK_field` / `logic_final_eu_check` as the equivalent of `eu_detect`).
- Both files, along with the `romanize` and `Location_validation` tables, are still in the codebase as a safety net in case some other part of the system still reads them. If nothing else depends on them, they're safe to delete along with their tables.
- Default database: SQLite (`entity_database.db`), configurable via the `ENTITY_DB_NAME` environment variable.
- The full entity payload (`entity_json`) is still stored intact in the database, even though a few fields (`EOID`, `FID`, etc.) are also extracted into their own columns for fast querying.
