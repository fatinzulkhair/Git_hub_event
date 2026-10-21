"""
=====================================================
CONFIGURATION
=====================================================
All global constants/configuration for the product app live
here, so if you later move to environment variables / .env you
only need to change one place.
"""

import os

# The project root: the folder holding product_run.py and product_api/.
# File paths below are resolved against it, so the server, the scripts and
# the notebooks all use the same files whichever folder they start in.
# An absolute path in an environment variable is used as it is.
PRODUCT_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SQL Server (AWS RDS) connection details — see product_sql_server.py. The
# entity program reads the same file by default: both live in one database.
PRODUCT_DB_CONFIG_PATH = os.path.join(
    PRODUCT_PROJECT_ROOT,
    os.environ.get("PRODUCT_DB_CONFIG", os.path.join("server_testing", "config.json")),
)

# Folder product_load_data.py bulk-loads from when given no arguments.
PRODUCT_DATA_DIR = os.path.join(
    PRODUCT_PROJECT_ROOT, os.environ.get("PRODUCT_DATA_DIR", "product_data")
)

# Where a product that fails validation gets its log file
# (product_validation_logger.py).
PRODUCT_LOG_DIR = os.path.join(
    PRODUCT_PROJECT_ROOT, os.environ.get("PRODUCT_LOG_DIR", "product_validation_logs")
)

PRODUCT_APP_TITLE = "Product Master Data API"
PRODUCT_APP_DESCRIPTION = (
    "API to store product master data (one packaging hierarchy per JSON "
    "file) using SHA256 hashes of PrimaryProductKey as identifiers, and to "
    "validate every product with the table-driven rule engine."
)
PRODUCT_APP_VERSION = "1.0.0"
