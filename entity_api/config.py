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

# SQL Server (AWS RDS) connection details — see sql_server.py. The product
# program reads the same file by default: both live in one database.
DB_CONFIG_PATH = os.path.join(
    PROJECT_ROOT,
    os.environ.get("ENTITY_DB_CONFIG", os.path.join("server_testing", "config.json")),
)

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
