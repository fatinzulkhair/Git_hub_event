"""
=====================================================
HASHING UTILITIES
=====================================================
Small pure function, easy to unit-test in isolation from
the database/API logic.
"""

import hashlib
import json

# The field that identifies a product. Every level of a packaging hierarchy
# (Shipping Case, Bundle, Pack) has its own PrimaryProductKey, so each level
# is a product of its own. Everything else is descriptive data and does not
# affect the hash.
PRODUCT_IDENTITY_FIELDS = ("PrimaryProductKey",)


def generate_product_hash(product: dict) -> str:
    """Deterministically generate a SHA256 hash that identifies a product.

    Only PrimaryProductKey is hashed, so two postings of the same product
    share a hash even when their other fields differ.
    """
    identity = {field: product.get(field) for field in PRODUCT_IDENTITY_FIELDS}
    identity_string = json.dumps(identity, sort_keys=True)
    return hashlib.sha256(identity_string.encode("utf-8")).hexdigest()
