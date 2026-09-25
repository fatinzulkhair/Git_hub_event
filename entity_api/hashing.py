"""
=====================================================
HASHING UTILITIES
=====================================================
Small pure function, easy to unit-test in isolation from
the database/API logic.
"""

import hashlib
import json

# The fields that identify an entity. Everything else in the payload
# (EOID, FID, UKEOID, ...) is descriptive data and does not affect the hash.
IDENTITY_FIELDS = ("systemCode", "businessEntityCode")


def generate_entity_hash(entity: dict) -> str:
    """Deterministically generate a SHA256 hash that identifies an entity.

    Only systemCode and businessEntityCode are hashed, so two payloads for
    the same (systemCode, businessEntityCode) share a hash even when their
    other fields differ.
    """
    identity = {field: entity.get(field) for field in IDENTITY_FIELDS}
    identity_string = json.dumps(identity, sort_keys=True)
    return hashlib.sha256(identity_string.encode("utf-8")).hexdigest()
