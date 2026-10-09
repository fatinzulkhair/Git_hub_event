"""
=====================================================
CONFIGURATION
=====================================================
All global constants/configuration for the app live here,
so if you later move to environment variables / .env you
only need to change one place.
"""

import os

# The project root: the folder holding entities_run.py and entity_api/.
# File paths below are resolved against it, so the server, the seed script
# and the notebooks all use the same files whichever folder they start in.
# An absolute path in an environment variable is used as it is.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_NAME = os.path.join(PROJECT_ROOT, os.environ.get("ENTITY_DB_NAME", "entity_database.db"))

# Where a record that fails validation gets its log file (validation_logger.py).
ENTITY_LOG_DIR = os.path.join(
    PROJECT_ROOT, os.environ.get("ENTITY_LOG_DIR", "entities_validation_logs")
)

APP_TITLE = "Entity Comparison API"
APP_DESCRIPTION = (
    "API to compare and store JSON entities "
    "using SHA256 hashes as duplicate identifiers."
)
APP_VERSION = "1.0.0"
