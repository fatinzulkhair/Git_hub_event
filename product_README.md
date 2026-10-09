# Product Master Data API

A standalone **FastAPI** program for the product master data in `product_data/`. It is built the
same way as the entity API (`entity_api/`, see `README.md`): each product is identified by a
SHA256 hash, stored as **every posting + newest posting**, and validated by the same
**table-driven rule engine**. Products stand alone, so there is no location table and no join.

---

## Project Structure

```
product_run.py                      # Entry point: python product_run.py  (port 8001)
product_load_data.py                # Bulk loader: every product_data/*.json in one transaction
product_seed_validation_rules.py    # Loads the example product rules (PRD-*)
product_data/                       # Input JSON — one packaging hierarchy per file
product_validation_logs/            # Created on the first failed validation: one log file per failed product
product_notebook_testing/
├── product_test_api.ipynb          # Manual test: contacts, post files, results, log files, reset
└── product_export_json.ipynb       # Export products back to JSON, one file per product
product_api/
├── product_config.py               # DB name, data folder, API title/version
├── product_database.py             # SQLite connection, schema, derived result tables
├── product_hashing.py              # generate_product_hash()  (SHA256 of PrimaryProductKey)
├── product_parser.py               # NEW: product JSON tree -> rows + the rule engine's flat view
├── product_schemas.py              # Pydantic models for the POST responses (Swagger)
├── product_crud.py                 # Store, validate, query, responsible contacts
├── product_validation_logger.py    # One JSON log file per product that fails a rule
├── product_rule_authoring.py       # POST /product-validation-rules -> the product_val_* tables
├── product_main.py                 # App factory
├── product_routers/
│   ├── products.py                 # /products/...
│   ├── product_validation_rules.py # /product-validation-rules
│   └── product_responsible_contacts.py  # /product-responsible-contacts
└── product_validation_engine/      # Same engine as entity_api, without the location join
    ├── product_loader.py, product_rule_engine.py, product_resolvers.py,
    ├── product_conditions.py, product_operators.py, product_datatypes.py,
    └── product_values.py
```

---

## Running

```bash
python product_seed_validation_rules.py   # once: example rules (safe to re-run)
python product_load_data.py               # bulk-load every product_data/*.json
python product_run.py                     # API + Swagger at http://localhost:8001/docs
```

The entity API keeps running on port 8000 (`python entities_run.py`); the two programs share
nothing. The product database is `product_database.db`, which you can change with the
`PRODUCT_DB_NAME` environment variable. Relative paths (`PRODUCT_DB_NAME`, `PRODUCT_DATA_DIR`,
`PRODUCT_LOG_DIR`) are resolved against the project root, so every script finds the same files
whichever folder it is started from.

---

## From JSON to Tables

A file holds one **packaging hierarchy**: a Shipping Case → its `ChildProducts` (Bundles) → their
`ChildProducts` (Packs). Every level has its own `PrimaryProductKey`, so **every level is a
product** (one row each), linked by `parent_product_key` and `level` (0 = top).

| JSON (per product) | Stored in |
|---|---|
| `PrimaryProductKey` | `primary_product_key`, plus `product_hash` = SHA256 of it |
| `Quantity` (children only: how many in one parent) | `quantity` |
| `Customer.Code` / `.Name` / `.Owner` | `customer_code` / `customer_name` / `customer_owner` |
| `ProductType.Name` | `product_type` |
| `ProductDefinition.ProductDefinitionKey` / `.Description` | `product_definition_key` / `description` |
| the last part of `ProductDefinitionKey` (`…:FA003115` → `FA003115`) | `product_code`, shared by every level of a hierarchy |
| `ProductDefinition.ProductCategory.Name` | `product_category` |
| `ProductDefinition.BrandFamily.Name` / `.ParentBrandFamily.Name` | `brand_family` / `parent_brand_family` |
| `ChildProducts[]` | their own rows; `child_count` on the parent |
| the whole product, minus `ChildProducts` | `product_json` |
| `AlternateProductKeys[]` | `product_alternate_key` (one row per key) |
| `Units[]` | `product_unit` |
| `ProductProperties{}` and `ProductDefinition.ProductDefinitionProperties[]` | `product_property` (`property_group` names which one) |

**Tables**

- `product_history`: every posting (its `id` is `product_id` in the validation results).
- `product_latest`: the newest posting per product. `product_hash` is UNIQUE, so a re-post replaces the row and keeps its `id`.
- `product_alternate_key`, `product_unit`, `product_property`: the lists, keyed by `product_hash`, holding the newest posting only. Older versions remain in `product_history.product_json`.
- `product_val_rules`, `product_val_rule_conditions`, `product_val_checks`, `product_val_logic`: rule definitions.
- `product_validation_results`: every outcome. `product_latest_validation_rules` and `product_validation_summary` are derived from it (latest run, and per product).
- `product_responsible_contact`: who to contact about a product, linked directly to `product_latest.product_code` (see below).

An older `product_database.db` is upgraded on startup: `product_code` is added to
`product_history` / `product_latest` and filled from `product_definition_key`.

### Why ingestion is fast

1. **Parse first, write once.** The whole payload (or, for the loader, every file) is parsed before the database is touched. A bad node rejects everything, and the error says where (`product.json[0].ChildProducts[0].Units: expected a list of objects`).
2. **One transaction** per payload: a single commit instead of one per row. This is the main cost in SQLite.
3. **Rules loaded once** per payload and resolved in memory.
4. **Batched detail tables**: three `DELETE` and three `INSERT` `executemany` calls per payload, whatever its size.
5. `product_load_data.py` skips HTTP entirely and loads all files in one transaction.

---

## API Endpoints

| Method & path | Purpose |
|---|---|
| `POST /products/process` | Body = one product hierarchy, or a list of them |
| `POST /products/upload-file` | Same, as a `.json` file upload |
| `GET /products` | `product_latest` (without `product_json`) |
| `GET /products/historical` | `product_history` |
| `GET /products/lookup?key=…` | Match on `PrimaryProductKey` **or any alternate key** (GTIN, MSKU, …). Returns detail lists, children, and `rule_fields` |
| `GET /products/validation-results` | Every outcome ever written |
| `GET /products/validation-results/latest` | Latest run, one row per product + rule |
| `GET /products/validation-results/summary` | Per product: rules run / failed, failures first |
| `POST /product-validation-rules` | Create or replace rules (same permissive body as the entity API) |
| `GET /product-validation-rules` | Dump the four rule tables |
| `POST /product-responsible-contacts` | Register contacts for a `product_code` (one object or a list) |
| `GET /product-responsible-contacts` | Every contact, with `matching_products` (0 = not loaded yet, or a typo) |

POST responses report, per product, `new_record` (`false` = replaced), `product_code`,
`failed_rules`, and `log_file` (the failed-validation log, `null` when nothing failed).
A lookup also returns the product's `responsible_contacts`.
An MSKU key is shared by every level of a hierarchy, so a lookup can return several products.

---

## Validation Rules

Rules work exactly as in the entity API (Trigger/Target conditions, Checks, IF/THEN/ELSE Logic,
the same operators and data types). The difference is the **field names**: a product is nested,
so rules name fields by their **dotted JSON path**, matched case-insensitively:

| Field name in a rule | Value for the L&M Pack in `product.json` |
|---|---|
| `ProductType.Name` | `Pack` |
| `ProductDefinition.ProductCategory.Name` | `Cigarettes` |
| `ProductProperties.TPPN` | `POM.006546` |
| `ProductDefinition.ProductDefinitionProperties.DesignMarketCode` | `FR04` (a Name/Value list becomes one field per Name) |
| `AlternateProductKeys.ProductKey` | `urn:…:msku:FA003115.00,urn:…:eutpd:…` (other lists are comma-joined, so use `contains` / `regex`) |
| `Units.Quantity` | `20.0` (a one-item list keeps its plain value) |
| `ParentProductKey`, `Level`, `ChildCount` | where the product sits in the hierarchy (added by the parser) |
| `ProductCode` | `FA003115` (added by the parser, = the `product_code` column) |

You don't need to guess paths: `GET /products/lookup?key=…` returns `rule_fields`, the exact
view the engine evaluates for that product.

`Unique` with `referenceSource: "batch"` is tracked **per rule**, so two rules on the same field do
not flag each other. The database form needs a real column, e.g. `database.product_latest.description`.

### Example rule set (`product_seed_validation_rules.py`)

| Rule | Requirement | Built with | On the sample files |
|---|---|---|---|
| `PRD-KEY-FORMAT` | `PrimaryProductKey` matches `^urn:inexto:id:prd:` | Target · Regex | all pass |
| `PRD-REQUIRED-FIELDS` | Customer code, type, definition key, description exist | Check · 4 fields AND | all pass |
| `PRD-TYPE-ENUM` | `ProductType.Name` is Shipping Case / Bundle / Pack | Target · Enum | all pass |
| `PRD-CHILD-QUANTITY` | a child's `Quantity` ≥ 1 | Trigger (`ParentProductKey` exists) · Number | all pass |
| `PRD-PACK-GTIN` | a Pack has a `gtin:` alternate key | Trigger (`= Pack`) · Regex | **fails** `FA003115.00.1` (MSKU and EU-TPD keys only) |
| `PRD-EU-TPD` | EU/UK design market ⇒ `TPPN` and `TPID` exist | Logic IF / THEN / ELSE | all pass (FR04 has both; MK04 and JP04 take ELSE) |
| `PRD-TRACKING-DATE` | `RelevantForTrackingSince` is `YYYY-MM-DD` | Trigger · Date | all pass |
| `PRD-KEY-UNIQUE` | no `PrimaryProductKey` twice in one payload | Target · Unique(batch) | all pass |

`PRODUCT_RULES_PAYLOAD` in the seed script is also a valid body for `POST /product-validation-rules`.

---

## Responsible Contacts

A contact is linked **directly to a `product_code`**, so one row covers every level of that
product (Shipping Case, Bundle and Pack). Contacts may be registered before the product is
loaded, and codes match case-insensitively. An identical row is reported as `skipped`.

```bash
curl -X POST http://localhost:8001/product-responsible-contacts \
  -H "Content-Type: application/json" \
  -d '[ { "product_code": "FA003115", "person_name": "Aisyah Rahman", "contact": "aisyah.rahman@example.com" },
        { "product_code": "FA003115", "person_name": "L&M Brand Team", "contact": "Teams: L&M Brand Team" } ]'
```

camelCase keys (`productCode`, `personName`) are accepted too.

---

## Failed-Validation Log Files

Every product that fails **at least one** rule gets one JSON file in `product_validation_logs/`
(set `PRODUCT_LOG_DIR` to change it):

```
log_<first 12 characters of product_hash>_<YYYYMMDD>.json      e.g. log_e398306b8331_20261009.json
```

- **One product, one file.** All the rules a product failed in a run are listed in that one file.
- Posting the same product again on the same day **adds** the new run to `validations`; the next day starts a new file.
- The file names the product's **responsible contacts** (those registered for its `product_code`).
- Files are written **after** the database commit, by the API and by `product_load_data.py` alike. If one cannot be written, the request still succeeds: the error goes to the console and `log_file` is `null`.

```json
{
  "program": "product",
  "product_hash": "e398306b8331e07c8b0648ca228a681c53bc54d9b9c58f6f637dca45b496ea00",
  "date": "2026-10-09",
  "product": {
    "primary_product_key": "urn:inexto:id:prd:tobacco:pmi:favp:FA003115.00.1",
    "product_code": "FA003115",
    "product_type": "Pack",
    "level": 2,
    "parent_product_key": "urn:inexto:id:prd:tobacco:pmi:favp:FA003115.00.2",
    "description": "L&M LIGHTS KS BOX 20"
  },
  "responsible_contacts": [
    { "person_name": "Aisyah Rahman", "contact": "aisyah.rahman@example.com" }
  ],
  "validations": [
    {
      "validated_at": "2026-10-09T14:47:25+07:00",
      "failed_rule_count": 3,
      "product_id": 25,
      "errors": [
        { "rule_id": "PRD-CHILD-QUANTITY", "severity": "Critical", "status": -1, "description": "Child product Quantity is missing or below 1" },
        { "rule_id": "PRD-PACK-GTIN",      "severity": "Warning",  "status": -1, "description": "Pack has no urn:inexto:id:prd:gtin:<14 digits> alternate key" },
        { "rule_id": "PRD-TRACKING-DATE",  "severity": "Warning",  "status": -1, "description": "RelevantForTrackingSince is not a YYYY-MM-DD date" }
      ]
    }
  ]
}
```

---

## Differences from the Entity API

- **No location:** no location tables or endpoints, no join, no `JOIN-COMPLETENESS`, and no `location_id` / `source` columns.
- **Identity** is `PrimaryProductKey` alone, not `systemCode` + `businessEntityCode`.
- **Nested input:** one row per hierarchy level, three detail tables, and dotted-path rule fields.
- **Separate database and names:** `product_database.db`, `product_*` tables, and port 8001.
- **Responsible contacts** are linked to a `product_code`, not to a system or a location's country.
- **Not copied:** the entity migration code. The product schema has its own small upgrade (adding `product_code`).
- **Small engine fixes:** `Unique(batch)` is tracked per rule, and the app uses `lifespan` instead of the deprecated `@app.on_event`.
