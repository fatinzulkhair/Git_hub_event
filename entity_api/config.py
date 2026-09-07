"""
=====================================================
CONFIGURATION
=====================================================
All global constants/configuration for the app live here,
so if you later move to environment variables / .env you
only need to change one place.
"""

import os

DB_NAME = os.environ.get("ENTITY_DB_NAME", "entity_database.db")

APP_TITLE = "Entity Comparison API"
APP_DESCRIPTION = (
    "API to compare and store JSON entities "
    "using SHA256 hashes as duplicate identifiers."
)
APP_VERSION = "1.0.0"
