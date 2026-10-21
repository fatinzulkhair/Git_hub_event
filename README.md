# Entity Comparison API

A **FastAPI**-based API for storing JSON entities in **SQL Server (AWS RDS)**, identifying each entity by a **SHA256 hash of `systemCode` + `businessEntityCode`**, and validating every entity through a **dynamic rule engine** whose rules live in the database instead of being hardcoded in Python.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Installation](#installation)
3. [Running the Server](#running-the-server)
4. [API Endpoints](#api-endpoints)
5. [Entity Identity (hash)](#entity-identity-hash)
6. [Failed-Validation Log Files](#failed-validation-log-files)
7. [Dynamic Rule Engine](#dynamic-rule-engine)
8. [Database Schema](#database-schema)
9. [Adding a New Rule (no coding required)](#adding-a-new-rule-no-coding-required)
10. [Notes](#notes)

---

## Project Structure

```
(project root)
├── requirements.txt
├── entities_run.py                 # Entry point: python entities_run.py
├── server_testing/
│   ├── config.example.json         # Template: copy to config.json and fill in the RDS details
│   └── config.json                 # Your SQL Server connection (not in git; shared with the product program)
├── entities_validation_logs/       # Created on the first failed validation: one log file per failed record
├── entities_location_data/         # Sample inputs: sample_locations.json, sample_payload*.json, seed_rules_api_format.txt
├── entities_notebook_testing/
│   ├── seed_validation_rules.py    # Seeds the entities_val_* tables with the example rules
│   ├── reset_db.ipynb              # Clear data tables / rules / log files, re-seed
│   └── test_api.ipynb, test_api2.ipynb, test_create_rule.ipynb
└── entity_api/
    ├── __init__.py
    ├── config.py                   # Global constants (config.json path, API title, version, etc.)
    ├── sql_server.py               # The SQL Server connection: config.json, engine, safety checks
    ├── database.py                 # Table definitions (created when missing) and the query helpers
    ├── hashing.py                  # generate_entity_hash()
    ├── schemas.py                  # Pydantic models for Swagger documentation
    ├── crud.py                     # Insert, location join, payload processing, queries
    ├── validation_logger.py        # One JSON log file per record that fails a rule
    ├── rule_authoring.py           # Parses POST /validation-rules bodies -> the four entities_val_* tables
    ├── main.py                     # App factory: builds FastAPI(), includes routers, startup event
    ├── routers/
    │   ├── __init__.py
    │   ├── entities.py             # All /entities/... endpoints
    │   ├── validation_rules.py     # POST/GET /validation-rules
    │   └── responsible_contacts.py # POST/GET /responsible-contacts
    └── validation_engine/
        ├── __init__.py
        ├── loader.py                # Reads entities_val_rules / entities_val_rule_conditions / entities_val_checks / entities_val_logic
        ├── operators.py             # Implements the 9 Operators (equals, in, regex, etc.)
        ├── datatypes.py             # Implements the 10 Data Types (Exist, Number, Romanize, Unique, etc.)
        ├── resolvers.py             # Resolves a Check ID / Logic ID recursively (IF/THEN/ELSE)
        ├── conditions.py            # Evaluates rules that use Rule Conditions directly (no Check/Logic ID)
        ├── rule_engine.py           # Orchestrator: runs every active rule against one entity
        └── values.py                # Shared value helpers (is_empty, split_csv)
```

| File / Module | Responsibility |
|---|---|
| `config.py` | All global constants in one place |
| `sql_server.py` | Reads `config.json`, builds the connection pool, refuses any other database / schema, and creates missing tables (never alters existing ones) |
| `database.py` | This program's table definitions, the unit-of-work `connect()`, and the shared query helpers |
| `hashing.py` | Pure function that generates a SHA256 hash for an entity |
| `schemas.py` | Pydantic models to keep the Swagger docs clean |
| `crud.py` | Business logic: insert entity, join location master data, process payload, query |
| `validation_logger.py` | Writes `log_<hash>_<date>.json` for every record that fails at least one rule |
| `routers/entities.py` | HTTP endpoint definitions (routing), delegates logic to `crud.py` |
| `main.py` | Wires all modules together into a single FastAPI `app` |
| `validation_engine/loader.py` | Reads the four rule tables into one `RuleSet` per payload, and writes results back |
| `validation_engine/operators.py` | Evaluates Operators (Trigger rows, or a Check of type "Operator") |
| `validation_engine/datatypes.py` | Evaluates Data Types (Target rows, or a Check of type "Data Type") |
| `validation_engine/resolvers.py` | Resolves a Check ID / Logic ID, including Logic → Logic chaining |
| `validation_engine/conditions.py` | Evaluates rules defined directly through Rule Conditions |
| `validation_engine/rule_engine.py` | Loops every active rule for one entity and returns its outcomes |
| `validation_engine/values.py` | `is_empty` / `split_csv`, shared by operators and data types |

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

`sqlalchemy` and `pyodbc` (both in `requirements.txt`) connect to SQL Server, through the
Microsoft **ODBC Driver 17 or 18 for SQL Server**, which must be installed on the machine.

### Connecting to SQL Server

Both programs (entities and products) use **one** database, named in `server_testing/config.json`:

```bash
cp server_testing/config.example.json server_testing/config.json   # then fill it in
```

```json
{
  "database": {
    "driver": "ODBC Driver 17 for SQL Server",
    "server": "your-instance.xxxxxxxx.ap-southeast-1.rds.amazonaws.com,1433",
    "database": "your_application_database",
    "username": "your_app_login",
    "password": "..."
  }
}
```

Optional keys: `schema` (default `dbo`), `encrypt` and `trust_server_certificate` (both default
`yes`, as in the RDS connection script). `config.json` holds a password, so it is in `.gitignore`.
Set `ENTITY_DB_CONFIG` to use another file.

The database must already exist; the application never creates or drops a database. Writes
stay inside that one database:

- every query uses plain table names (no `database.schema.table` names, no `USE`);
- each new connection is checked: `DB_NAME()` and the login's default schema must be the
  configured ones, otherwise the connection is refused with a message saying why;
- a system database (`master`, `msdb`, `rdsadmin`, ...) is refused in `config.json`;
- a `Unique` rule may only compare against this program's own tables and columns;
- on startup, **missing tables are created; existing tables are never altered or dropped.** If an
  existing table lacks a column the code writes (or its `id` is not an `IDENTITY` column), the
  server stops with a list of the differences and changes nothing.

Give the login rights on that database only (e.g. `db_owner`, or `db_datareader` +
`db_datawriter` + `db_ddladmin`) to make the database itself enforce this too.

---

## Running the Server

```bash
python entities_run.py
```

or directly via uvicorn:

```bash
uvicorn entity_api.main:app --reload --reload-dir entity_api
```

Only `entity_api/` is watched for changes; watching the whole project would include `venv/`.

The server runs at `http://localhost:8000`. Interactive API documentation (Swagger UI) is automatically available at:

```
http://localhost:8000/docs
```

On the first start, the server creates every table it needs (see [Database Schema](#database-schema)) in the database named in `server_testing/config.json`.

### Seeding the example rules (one-time)

Before processing data, run this once to load the 8 example rules (VAL-001 through VAL-008) into the database:

```bash
python entities_notebook_testing/seed_validation_rules.py
```

The script is safe to run more than once (idempotent) — it saves the rows exactly as `POST /validation-rules` does, updating the ones that share a `rule_id` / `check_id` / `logic_id`.

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
  "total": 1,
  "inserted": 1,
  "new_records": 1,
  "updated_records": 0,
  "joined": 0,
  "awaiting_counterpart": 1,
  "details": [
    { "status": "inserted", "id": 1, "new_record": true, "entity_hash": "…",
      "systemCode": "SYS1", "businessEntityCode": "BE001", "joined": false }
  ]
}
```

Every posted entity is appended to `historical_entities` (`inserted`, and `id` = its row there) and creates or replaces its row in `entities` (`new_records` / `updated_records`). It is then validated against the join with `entities_location`: `joined` counts the entities whose location already exists, `awaiting_counterpart` those still without one. See [Records and the Join](#records-and-the-join).

### `POST /entities/upload-file`
Upload a `.json` file (multipart/form-data) with the same structure as above.

```bash
curl -X POST http://localhost:8000/entities/upload-file \
  -F "file=@data.json"
```

### `POST /entities/inputlocation`
Send a JSON array of location master data used by rules that need a country reference (e.g. an EU/UK check). Each item needs `systemCode`, `Code` (stored as `businessEntityCode`) and `Country`; `Name`, `Type`, `Alternate_code`, `City`, `Zip` are optional.

Locations are stored exactly like entities — every posting in `entities_historical_location`, the newest in `entities_location` — and posting one **also runs the validation rules**, against the join with `entities`. Order therefore no longer matters: whichever side arrives second re-validates the pair. A location whose entity does not exist yet is reported by the `JOIN-COMPLETENESS` rule, and the response's `awaiting_counterpart` counts them.

### `GET /entities`
Lists the `entities` table — the newest version of each `systemCode` + `businessEntityCode` (summary columns).

```bash
curl http://localhost:8000/entities
```

### `GET /entities/historical`
Lists every posting (`historical_entities`). A row's `id` is the `entity_id` used in `entities_validation_results`.

```bash
curl http://localhost:8000/entities/historical
```

### `GET /entities/locations`
Lists the `entities_location` table — the newest location per `systemCode` + `businessEntityCode`.

```bash
curl http://localhost:8000/entities/locations
```

### `GET /entities/locations/historical`
Lists every location posting (`entities_historical_location`). A row's `id` is the `location_id` used in `entities_validation_results`.

### `GET /entities/joined`
The FULL OUTER JOIN the rules are evaluated against: one row per `systemCode` + `businessEntityCode`, both sides' columns, and a `join_status` of `complete` / `missing_entity` / `missing_location`.

```bash
curl http://localhost:8000/entities/joined
```

### `GET /entities/validation-results`
The full history: every outcome ever written, one row per (record, rule, run), with pass/fail status, severity, and message. A record posted five times appears five times.

```bash
curl http://localhost:8000/entities/validation-results
```

### `GET /entities/validation-results/latest`
The most recent run only — one row per record and rule, from `entities_latest_validation_rules`. `source` says whether that run came from posting the entity or the location; `validated_at` is when it ran.

```bash
curl http://localhost:8000/entities/validation-results/latest
```

### `GET /entities/validation-results/summary`
One row per record, failures first: `rules_run`, `rules_failed`, and `failed_rules` (the failing rule ids, `', '`-separated, `null` when nothing failed).

```bash
curl http://localhost:8000/entities/validation-results/summary
```

```json
[ { "systemCode": "SYS-A", "businessEntityCode": "BE-004", "rules_run": 3,
    "rules_failed": 2, "failed_rules": "JOIN-COMPLETENESS, NB2-LOCATION-COUNTRY",
    "validated_at": "2026-09-25 04:33:02" } ]
```

### `GET /entities/{business_entity_code}`
Looks up an entity by `businessEntityCode`. Returns `404` if not found.

```bash
curl http://localhost:8000/entities/BE001
```

### `POST /validation-rules`
Create (or overwrite) validation rules without editing `seed_validation_rules.py` or restarting. Rows are written straight into `entities_val_rules` / `entities_val_rule_conditions` / `entities_val_checks` / `entities_val_logic` and picked up on the next `/entities/process` call. Returns `201` with a summary (`rules_written`, `checks_written`, `generated_ids`, `warnings`, …).

The body is intentionally permissive — see [Adding a New Rule](#adding-a-new-rule-no-coding-required). Simplest form:

```bash
curl -X POST http://localhost:8000/validation-rules \
  -H "Content-Type: application/json" \
  -d '{
        "rule_id": "API-ROMANIZE-NAME",
        "description": "Location name must be Latin/ASCII only",
        "severity": "Warning",
        "on_exception_description": "Location name is not romanized",
        "conditions": [ { "role": "Target", "field": "Name", "data_type": "Romanize" } ]
      }'
```

Behaviour: upsert (update, or insert if new) per `rule_id` / `check_id` / `logic_id`; for any `rule_id` in the payload, **all** of its `entities_val_rule_conditions` rows are replaced (they have no natural key). Missing `rule_id` / `check_id` / `logic_id` are auto-generated (`api-rule-…` / `api-check-…`) and echoed back in `generated_ids`.

### `GET /validation-rules`
Dumps every row currently in the four rule tables: `{ "rules": [...], "conditions": [...], "checks": [...], "logic": [...] }`.

```bash
curl http://localhost:8000/validation-rules
```

### `POST /responsible-contacts`
Register who is responsible for a system or a country, and how to reach them. Accepts one object or a list; returns `201` with `total` / `inserted` / `skipped` and a per-row `details` list.

| Field | Meaning |
|---|---|
| `related_field` | `system` or `country` (case-insensitive; anything else → `422`) |
| `related_field_name` | the system name or country name |
| `person_name` | responsible person |
| `contact` | email or Teams account (free text) |

camelCase keys (`relatedField`, `relatedFieldName`, `personName`) are accepted too.

```bash
curl -X POST http://localhost:8000/responsible-contacts \
  -H "Content-Type: application/json" \
  -d '[
        { "related_field": "country", "related_field_name": "Malaysia",
          "person_name": "Aisyah Rahman", "contact": "aisyah.rahman@example.com" },
        { "related_field": "system", "related_field_name": "ACDC",
          "person_name": "Budi Santoso", "contact": "teams:budi.santoso" }
      ]'
```

A row identical to an existing one (all four values) is reported as `skipped` instead of being inserted twice. A system or country can still have several different contacts.

### `GET /responsible-contacts`
Lists every row of the `entities_responsible_contact` table.

---

## Records and the Join

An **entity** and a **location** are both records identified by **`systemCode` + `businessEntityCode`**: those two values are serialized to JSON (`sort_keys=True`) and hashed with SHA256 into `entity_hash`. No other field affects the hash, and because both sides hash the same pair, `entity_hash` is also what joins them.

Each kind is kept in two tables with identical columns:

| Kind | Every posting | Newest posting | `entity_hash` |
|---|---|---|---|
| Entity | `historical_entities` | `entities` | not unique in history, `UNIQUE` in the latest table |
| Location | `entities_historical_location` | `entities_location` | same |

A posting is always appended to the history table. In the latest table a new pair is inserted, and a pair already present has its row **replaced** (same `id`, all data columns overwritten, `created_at` refreshed). Older versions stay in the history table.

### Validation runs on both sides

`POST /entities/process` and `POST /entities/inputlocation` both store their postings and then run the rule engine. Rules are evaluated against the **FULL OUTER JOIN** of the two latest rows, as one flat record — entity fields win over location fields on a name clash, and field lookup is case-insensitive, so a rule may say `country`, `Country` or `COUNTRY`.

Either side may be missing, and the engine always emits a built-in result for that:

| `rule_id` | When | `status` | `passed` |
|---|---|---|---|
| `JOIN-COMPLETENESS` | both sides exist | — | 1 |
| `JOIN-COMPLETENESS` | location posted, no entity yet | `-11` | 0 |
| `JOIN-COMPLETENESS` | entity posted, no location yet | `-12` | 0 |

`JOIN-COMPLETENESS` is reserved: an `entities_val_rules` row with that id is ignored. Each `entities_validation_results` row also carries `entity_id`, `location_id` (the two history rows that were joined; `NULL` for a missing side) and `source` (`entity` or `location` — which POST produced the row).

**Moving from SQLite.** The SQLite version's database file and its migration code are gone: the SQL Server tables start empty. Re-load the rules with the seed script (or `POST /validation-rules`) and re-post the data.

---

## Failed-Validation Log Files

Every record that fails **at least one** rule (any severity, including the built-in
`JOIN-COMPLETENESS`) gets one JSON file in `entities_validation_logs/` (set
`ENTITY_LOG_DIR` to change it):

```
log_<first 12 characters of entity_hash>_<YYYYMMDD>.json      e.g. log_28755534b8b0_20261009.json
```

- **One record, one file.** All the rules a record failed in a run are listed in that one file.
- Posting the same record again on the same day **adds** the new run to `validations`; the next day starts a new file.
- The file names the record's **responsible contacts**: the `entities_responsible_contact` rows for its `systemCode` (`system`) and its location's country (`country`), matched case-insensitively.
- The POST response gives each posting's `failed_rules` and `log_file` (`null` when nothing failed).
- Files are written **after** the database commit. If one cannot be written, the request still succeeds: the error goes to the server console and `log_file` is `null`.

```json
{
  "program": "entities",
  "entity_hash": "28755534b8b0ba9405dc6740f02b37abc10a6483124d3a1653ea0deb9749f2e0",
  "date": "2026-10-09",
  "record": { "systemCode": "SYS-A", "businessEntityCode": "BE-002", "country": "GB" },
  "responsible_contacts": [
    { "related_field": "country", "related_field_name": "GB", "person_name": "UK Owner", "contact": "uk@example.com" }
  ],
  "validations": [
    {
      "validated_at": "2026-10-09T14:47:24+07:00",
      "failed_rule_count": 2,
      "source": "location",
      "entity_id": 7,
      "location_id": 7,
      "errors": [
        { "rule_id": "VAL-UK-Field", "severity": "Critical", "status": -1, "description": "Missing UK_TPD/UI_ID/UKSLL for UK location" },
        { "rule_id": "FIELD-EXIST",  "severity": "Critical", "status": -1, "description": "Missing UK_TPD/UI_ID/UKSLL for UK location" }
      ]
    }
  ]
}
```

---

## Dynamic Rule Engine

Previously, adding a validation rule meant writing a new Python function (`eu_detect`, `romanizecheck`) and wiring it manually into `validation_rules_engine`. Now, every rule is stored in **four database tables**, and a generic engine reads and executes them.

### High-level flow

```
crud._process_postings(spec, payloads, ...)      # spec = ENTITIES or LOCATIONS
   ├─ load_ruleset()                             # one snapshot of the four rule tables
   └─ for each posting in the payload:
        store_posting(spec, record)                # -> history always; -> latest: insert or replace
        latest_posting(other_spec, entity_hash)    # the other side of the join (or None)
        run_all_rules({**location, **entity}, ...) # entity fields win on a name clash
             ├─ JOIN-COMPLETENESS                  # built-in: is the other side there?
             └─ for each ACTIVE rule in entities_val_rules:
                  if the rule has a Check ID  -> resolvers.resolve(check_id)
                  otherwise                   -> conditions.evaluate_rule_conditions(rule_id)
        insert_validation_results(rows, ...)       # one batch write per payload, which also
                                                   # rebuilds entities_latest_validation_rules
                                                   # + entities_validation_summary for its records
```

Rules therefore see one flat dict combining the entity and its location. If the other side has never been posted, its fields are simply absent — checks on them fail or fall through their `ELSE` branch, exactly as for any missing field, and `JOIN-COMPLETENESS` says which side is missing.

Field names in rules are matched **case-insensitively** (`country`, `Country` and `COUNTRY` all resolve to the same value), since the entity columns and the joined location columns don't share a casing convention. An exact-case match still wins when both spellings are present.

### 1. `entities_val_rules` table (Rules)

The main rule list. Key columns:

| Column | Meaning |
|---|---|
| `rule_id` | Unique ID, e.g. `VAL-001` |
| `check_id` | (optional) points to one row in `entities_val_checks` **or** `entities_val_logic`. If empty, the rule is evaluated via `entities_val_rule_conditions` |
| `severity` | `Critical` / `Warning`, etc. — free text, only kept for reporting |
| `active` | `Y`/`N` — rules with `N` are ignored entirely by the engine |
| `on_exception_status`, `on_exception_description` | Used to populate `status` & `description` in `entities_validation_results` when the rule fails |

### 2. `entities_val_rule_conditions` table (Rule Conditions)

Used by rules that do **not** set `check_id` (the most common case). One rule can have several rows:

- **Role = Trigger** — the condition for the rule to apply at all. All Trigger rows for one `rule_id` are AND'd together. If the trigger fails, the rule is considered **not applicable** to that entity and automatically passes.
- **Role = Target** — what must hold true once the rule applies. All Target rows are AND'd together, evaluated via the `Data Type` column.

The `Operator` column is used on Trigger rows; the `Data Type` column is used on Target rows.

### 3. `entities_val_checks` table (Checks)

Reusable, ID-addressable checks that can be referenced repeatedly (from `entities_val_rules.check_id` or from `entities_val_logic`). A single Check can inspect several fields at once (`field_names`, comma-separated), combined via `field_combine` (`AND`/`OR`).

`check_type` determines whether `check_value` is read as an **Operator** name or a **Data Type** name.

### 4. `entities_val_logic` table (Logic)

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

Created automatically by `database.initialize_database()` on startup, in the database named
in `server_testing/config.json` (only tables that do not exist yet; see
[Connecting to SQL Server](#connecting-to-sql-server)). Entity and product data share that
database, so every table of this program has `entities` in its name; the product tables start
with `product_`.

Column types: `id` is `BIGINT IDENTITY`, hashes `CHAR(64)`, codes and keys `NVARCHAR(450)`,
other text `NVARCHAR(4000)`, the posted payload (`entity_json`) `NVARCHAR(MAX)`, `passed`
`BIT`, and timestamps `DATETIME2(0)` in UTC. Contact names compare case-insensitively.

**Data tables:**
- `entities` — the newest version of each entity, unique on `entity_hash` (hash of `systemCode` + `businessEntityCode`)
- `historical_entities` — every posted entity (same columns, `entity_hash` not unique); its `id` is `entities_validation_results.entity_id`
- `entities_location` — the newest location per record, unique on `entity_hash`
- `entities_historical_location` — every location posting; its `id` is `entities_validation_results.location_id`
- `entities_responsible_contact` — who to contact per system or country: `related_field` (`system`/`country`), `related_field_name`, `person_name`, `contact` (+ `id`, `created_at`); unique on the four values

**Rule engine tables (dynamic):**
- `entities_val_rules`, `entities_val_rule_conditions`, `entities_val_checks`, `entities_val_logic` — rule definitions
- `entities_validation_results` — the history: one row per (record, rule, run): `entity_hash`, `entity_id`, `location_id`, `source`, `passed`, `severity`, `status`, `description`

**Derived from `entities_validation_results`** (rebuilt in the same transaction as each write, so they never lag):
- `entities_latest_validation_rules` — the latest run only, keyed on (`entity_hash`, `rule_id`), plus `systemCode`, `businessEntityCode`, `source`, `passed`, `severity`, `status`, `description`, `validated_at`
- `entities_validation_summary` — that table aggregated per record, keyed on `entity_hash`: `rules_run`, `rules_failed`, `failed_rules`, `validated_at`

Both are keyed by the record (`entity_hash` = `systemCode` + `businessEntityCode` hashed), never by a posting id, so one record holds one row per rule however often it is posted — and the entity side and the location side of a join share those rows.

A record is rewritten wholesale each time it is validated: its old rows are cleared first, so a rule that has since been deactivated leaves the table on that record's next posting. Records not re-posted keep their last known run.


---

## Adding a New Rule (no coding required)

1. **A simple rule (Exist/Regex/Unique/etc. on one or two fields):** add one row to `entities_val_rules`, then add Trigger (optional) and Target rows to `entities_val_rule_conditions` sharing the same `rule_id`. No need to set `check_id`.
2. **A rule with IF/THEN/ELSE logic, or one you want to reuse in other rules:** create a row in `entities_val_checks` for each atomic check, combine them via `entities_val_logic`, then set `entities_val_rules.check_id` to that Logic ID or Check ID.
3. Set `active = 'Y'`. The rule is active on the very next request — no server restart required.

You can do this three ways: edit `entities_notebook_testing/seed_validation_rules.py`, edit the tables directly (e.g. in SQL Server Management Studio), or **`POST /validation-rules`** (see above).

### Accepted request shapes for `POST /validation-rules`

`entity_api/rule_authoring.py` normalises all of these into the same four-table layout:

| Variation | Example |
|---|---|
| Single rule object | `{ "rule_id": "R1", "field": "Name", "data_type": "Romanize" }` |
| Bare list of rules | `[ { ... }, { ... } ]` |
| Seed layout | `{ "RULES": [...], "CONDITIONS": [...], "CHECKS": [...], "LOGIC": [...] }` |
| Positional arrays | `{ "rules": [["R1", "type", null, "chk1", "desc", "Warning", "Y", -1, "msg"]] }` |
| camelCase keys | `{ "ruleId": "R1", "checkId": "chk1", "onExceptionStatus": -1 }` |
| Nested conditions | `{ "rule_id": "R1", "conditions": [{ "role": "Target", "field": "Name", "dataType": "Romanize" }] }` |
| Flat single-condition shortcut | `{ "rule_id": "R1", "field": "Name", "data_type": "Romanize" }` (no `conditions` block) |
| Inline `check` / `logic` object | `{ "rule_id": "R1", "check": { "type": "Data Type", "fields": ["Name"], "value": "Romanize" } }` |

Field aliases are broad — e.g. `field` / `fieldName` / `field_names` → `field_name`; `value` / `expected` / `expectedValue` → `expected_value`; `type` / `dataType` → `data_type`; `active: true/false` → `'Y'/'N'`. Lists (`"expected_value": ["GB","UK"]`) are joined into the comma string the engine expects.

---

## Notes
- Database: SQL Server, from `server_testing/config.json` (or the file in `ENTITY_DB_CONFIG`; a relative path is resolved against the project root).
- Failed-validation logs: `entities_validation_logs/`, configurable via `ENTITY_LOG_DIR` (also resolved against the project root).
- The full entity payload (`entity_json`) is still stored intact in the database, even though a few fields (`EOID`, `FID`, etc.) are also extracted into their own columns for fast querying.
