"""
=====================================================
FAILED-VALIDATION LOG FILES
=====================================================
Whenever a product fails at least one validation rule, it gets a JSON log
file in PRODUCT_LOG_DIR (product_validation_logs/ by default):

    log_<product_hash, first 12 characters>_<YYYYMMDD>.json
    e.g. log_3f9a1c2b7d4e_20261009.json

One product, one file: every rule the product failed in a validation run is
listed in that one file. Posting the same product again on the same day adds
the new run to the same file (under "validations"); the next day starts a
new file. The full product_hash is inside the file.

product_crud.store_and_validate_products calls write_product_failure_log()
after the database commit, so a log never describes a posting that was
rolled back. A log that cannot be written never fails the request: the error
is printed to the server console and the posting's log_file is null.
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

from .product_config import PRODUCT_LOG_DIR

HASH_LENGTH = 12  # like a short git hash: easy to read, and still unique

_lock = threading.Lock()  # a log file is read, extended, then rewritten
_console = logging.getLogger(__name__)


def product_log_file_name(product_hash: str, failed_at: datetime) -> str:
    return f"log_{product_hash[:HASH_LENGTH]}_{failed_at:%Y%m%d}.json"


def write_product_failure_log(product_hash: str, product: dict, contacts: list,
                              run: dict) -> Optional[str]:
    """Add one failed validation run of one product to today's log file for
    that product, creating the file if needed.

    product   which product it is: key, product_code, type, level, ...
    contacts  the product_responsible_contact rows for its product_code
    run       this run: product_id and `errors`, the outcome of every rule
              that failed

    Returns the log file's name, or None if it could not be written.
    """
    failed_at = datetime.now().astimezone()  # local time, with its UTC offset
    path = os.path.join(PRODUCT_LOG_DIR, product_log_file_name(product_hash, failed_at))
    try:
        with _lock:
            log = _read(path) or {
                "program": "product",
                "product_hash": product_hash,
                "date": failed_at.date().isoformat(),
                "product": None,
                "responsible_contacts": None,
                "validations": [],
            }
            # The product and its contacts as they are now; every run is kept.
            log["product"] = product
            log["responsible_contacts"] = contacts
            log["validations"].append({
                "validated_at": failed_at.isoformat(timespec="seconds"),
                "failed_rule_count": len(run["errors"]),
                **run,
            })
            _write(path, log)
        return os.path.basename(path)
    except (OSError, ValueError):
        _console.exception("Could not write the validation log %s", path)
        return None


def _read(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, log: dict) -> None:
    """Write via a temporary file, so a reader never sees half a log."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)
