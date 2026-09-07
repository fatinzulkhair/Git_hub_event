"""
=====================================================
HASHING UTILITIES
=====================================================
Small pure function, easy to unit-test in isolation from
the database/API logic.
"""

import hashlib
import json


def generate_entity_hash(entity: dict) -> str:
    """Deterministically generate a SHA256 hash for an entity (dict)."""
    entity_string = json.dumps(entity, sort_keys=True)
    return hashlib.sha256(entity_string.encode("utf-8")).hexdigest()
