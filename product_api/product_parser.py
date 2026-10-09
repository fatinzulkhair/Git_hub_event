"""
=====================================================
PRODUCT PARSER  (product JSON -> table rows)
=====================================================
A product file holds a packaging hierarchy: a Shipping Case whose
ChildProducts are Bundles, whose ChildProducts are Packs, and so on. Every
level has its own PrimaryProductKey, so every level is a product of its own;
the nesting is kept as parent_product_key + level.

parse_products() turns a payload into a flat list of ProductNode — parents
before their children — each holding everything the database and the rule
engine need. Nothing is written here: a payload is parsed in full first, so
a malformed node anywhere rejects the whole payload (nothing half-written),
and the error names the exact node, e.g.

    product.json[0].ChildProducts[0].Units: expected a list of objects

Pure functions only (no database), so this module is easy to test alone.
"""

import json
from dataclasses import dataclass

from .product_hashing import generate_product_hash

CHILDREN_KEY = "ChildProducts"


@dataclass
class ProductNode:
    """One product (one level of a hierarchy), ready to store and validate."""

    product_hash: str      # sha256(PrimaryProductKey)
    record: dict           # the product_history / product_latest row, by column
    fields: dict           # the flat view the rule engine evaluates
    alternate_keys: list   # (product_key, product_key_type, product_key_mode)
    units: list            # (unit_type_name, quantity, base_unit)
    properties: list       # (property_group, name, value)


# ---------------------------------------------------
# PAYLOAD -> PRODUCT NODES
# ---------------------------------------------------

def parse_products(payload, source: str = "payload") -> list:
    """Every product in `payload` (one hierarchy, or a list of them), parents
    before their children. `source` names the payload in error messages.

    Raises ValueError, naming the node, if a product has no PrimaryProductKey
    or one of its objects/lists has the wrong shape.
    """
    hierarchies = payload if isinstance(payload, list) else [payload]
    nodes = []
    for index, product in enumerate(hierarchies):
        _walk(product, None, 0, f"{source}[{index}]", nodes)
    return nodes


def _walk(product, parent_key, level: int, where: str, nodes: list) -> None:
    """Parse one product, then — depth-first — everything inside it."""
    if not isinstance(product, dict):
        raise ValueError(f"{where}: expected a JSON object")

    key = product.get("PrimaryProductKey")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{where}: PrimaryProductKey must be a non-empty string")

    children = _objects(product.get(CHILDREN_KEY), f"{where}.{CHILDREN_KEY}")
    own = {k: v for k, v in product.items() if k != CHILDREN_KEY}
    fields = product_rule_fields(own, parent_key, level, len(children))

    nodes.append(ProductNode(
        product_hash=generate_product_hash(own),
        record=_product_record(own, fields),
        fields=fields,
        alternate_keys=_alternate_keys(own, where),
        units=_units(own, where),
        properties=_properties(own, where),
    ))

    for index, child in enumerate(children):
        _walk(child, key, level + 1, f"{where}.{CHILDREN_KEY}[{index}]", nodes)


# ---------------------------------------------------
# ONE PRODUCT -> ROWS
# ---------------------------------------------------

def _product_record(own: dict, fields: dict) -> dict:
    """The product_history / product_latest row. Each column is read from the
    flattened JSON, so the path beside it is exactly where it comes from."""
    return {
        "primary_product_key": fields.get("PrimaryProductKey"),
        "parent_product_key": fields.get("ParentProductKey"),
        "level": fields.get("Level"),
        "quantity": fields.get("Quantity"),
        "product_type": fields.get("ProductType.Name"),
        "customer_code": fields.get("Customer.Code"),
        "customer_name": fields.get("Customer.Name"),
        "customer_owner": fields.get("Customer.Owner"),
        "product_definition_key": fields.get("ProductDefinition.ProductDefinitionKey"),
        "product_code": fields.get("ProductCode"),
        "description": fields.get("ProductDefinition.Description"),
        "product_category": fields.get("ProductDefinition.ProductCategory.Name"),
        "brand_family": fields.get("ProductDefinition.BrandFamily.Name"),
        "parent_brand_family": fields.get("ProductDefinition.BrandFamily.ParentBrandFamily.Name"),
        "child_count": fields.get("ChildCount"),
        "product_json": json.dumps(own, ensure_ascii=False),
    }


def _alternate_keys(own: dict, where: str) -> list:
    items = _objects(own.get("AlternateProductKeys"), f"{where}.AlternateProductKeys")
    return _rows(items, ("ProductKey", "ProductKeyType", "ProductKeyMode"))


def _units(own: dict, where: str) -> list:
    items = _objects(own.get("Units"), f"{where}.Units")
    return _rows(items, ("UnitTypeName", "Quantity", "BaseUnit"))


def _properties(own: dict, where: str) -> list:
    """ProductProperties ({name: value}) and the Name/Value list
    ProductDefinition.ProductDefinitionProperties, as one list of
    (property_group, name, value) rows."""
    properties = _object(own.get("ProductProperties"), f"{where}.ProductProperties")
    definition = _object(own.get("ProductDefinition"), f"{where}.ProductDefinition")
    definition_properties = _objects(
        definition.get("ProductDefinitionProperties"),
        f"{where}.ProductDefinition.ProductDefinitionProperties",
    )
    return (
        [("ProductProperties", name, _scalar(value)) for name, value in properties.items()]
        + [("ProductDefinitionProperties", *row)
           for row in _rows(definition_properties, ("Name", "Value"))]
    )


def _rows(items: list, keys: tuple) -> list:
    """One tuple per JSON object: its values for `keys`, in that order."""
    return [tuple(_scalar(item.get(key)) for key in keys) for item in items]


def _object(value, where: str) -> dict:
    """A JSON object; null or missing counts as empty."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected a JSON object")
    return value


def _objects(value, where: str) -> list:
    """A list of JSON objects; null or missing counts as empty."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{where}: expected a list of objects")
    return value


def _scalar(value):
    """A value SQLite can store: nested JSON is kept as its JSON text."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


# ---------------------------------------------------
# THE RULE ENGINE'S VIEW OF A PRODUCT
# ---------------------------------------------------

def product_rule_fields(own: dict, parent_key, level: int, child_count: int) -> dict:
    """What validation rules see for one product: its flattened JSON (see
    flatten_product_fields) plus where it sits in the hierarchy. Rules name
    fields by these keys; the engine matches them case-insensitively."""
    fields = flatten_product_fields(own)
    fields["ParentProductKey"] = parent_key   # None at the top of a hierarchy
    fields["Level"] = level                   # 0 = top, 1 = its children, ...
    fields["ChildCount"] = child_count        # number of ChildProducts
    fields["ProductCode"] = product_code_of(
        fields.get("ProductDefinition.ProductDefinitionKey")
    )
    return fields


def product_code_of(product_definition_key):
    """The product code: the last part of ProductDefinitionKey.

        urn:inexto:id:prdd:tobacco:pmi:FA003115  ->  FA003115

    Every level of one hierarchy (Shipping Case, Bundle, Pack) shares it, so
    a responsible contact registered for FA003115 covers all of them. None
    when the product has no ProductDefinitionKey.
    """
    if not isinstance(product_definition_key, str) or not product_definition_key.strip():
        return None
    return product_definition_key.strip().rsplit(":", 1)[-1]


def flatten_product_fields(data: dict, prefix: str = "") -> dict:
    """Nested JSON -> one flat {dotted.path: value} dict:

        {"Customer": {"Code": "PMI"}}           -> {"Customer.Code": "PMI"}
        {"Props": [{"Name": "A", "Value": 1}]}  -> {"Props.A": 1}      (Name/Value list)
        {"Keys": [{"K": "a"}, {"K": "b"}]}      -> {"Keys.K": "a,b"}   (any other list)
    """
    flat = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten_product_fields(value, f"{path}."))
        elif isinstance(value, list):
            flat.update(_flatten_list(value, path))
        else:
            flat[path] = value
    return flat


def _flatten_list(items: list, path: str) -> dict:
    if items and all(isinstance(i, dict) and set(i) == {"Name", "Value"} for i in items):
        # A Name/Value property list reads as one field per Name.
        return {f"{path}.{item['Name']}": item["Value"] for item in items}

    # Any other list: one field per sub-path, its values comma-joined across
    # the items so operators such as contains / regex can search them. A lone
    # value is kept as it is, so a one-item Units list still holds a number.
    collected = {}
    for item in items:
        flat_item = flatten_product_fields(item) if isinstance(item, dict) else {"": item}
        for sub_path, value in flat_item.items():
            full_path = f"{path}.{sub_path}" if sub_path else path
            collected.setdefault(full_path, []).append(value)

    return {
        full_path: values[0] if len(values) == 1
        else ",".join("" if value is None else str(value) for value in values)
        for full_path, values in collected.items()
    }
