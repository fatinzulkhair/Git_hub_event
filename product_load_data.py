"""
Bulk-load product JSON files straight into the product database:

    python product_load_data.py                  # every *.json in product_data/
    python product_load_data.py some/folder      # every *.json in that folder
    python product_load_data.py a.json b.json    # just these files

The fastest way in — no HTTP, and one transaction for everything:

  1. Every file is read and parsed first. A malformed file stops the run
     before the database is touched, and the message names the file and node.
  2. All products are then stored and validated together, by the same code
     as POST /products/process: one commit, one snapshot of the rules, and
     the detail tables (alternate keys / units / properties) written in
     batches.

Writes go to the SQL Server database in server_testing/config.json. The API
server may stay running: it sees the new rows on its next request.
"""

import json
import sys
import time
from pathlib import Path

from product_api.product_config import PRODUCT_DATA_DIR, PRODUCT_LOG_DIR
from product_api.product_crud import store_and_validate_products
from product_api.product_database import initialize_product_database
from product_api.product_parser import parse_products


def collect_files(args: list) -> list:
    """The .json files named on the command line — files, or folders whose
    *.json are taken — or every *.json in PRODUCT_DATA_DIR if none are."""
    files = []
    for target in [Path(arg) for arg in args] or [Path(PRODUCT_DATA_DIR)]:
        files.extend(sorted(target.glob("*.json")) if target.is_dir() else [target])
    return files


def parse_files(files: list) -> list:
    """Every product in every file, parents before their children. Exits on
    the first problem, naming the file (and node)."""
    nodes = []
    for path in files:
        try:
            payload = json.loads(path.read_bytes())  # bytes: a UTF-8 BOM is fine too
        except (OSError, ValueError) as exc:
            sys.exit(f"Nothing loaded - cannot read {path}: {exc}")
        try:
            nodes.extend(parse_products(payload, source=str(path)))
        except ValueError as exc:
            sys.exit(f"Nothing loaded - {exc}")
    return nodes


def main(args: list) -> None:
    files = collect_files(args)
    if not files:
        sys.exit("No .json files found.")

    nodes = parse_files(files)

    initialize_product_database()
    started = time.perf_counter()
    summary = store_and_validate_products(nodes)
    elapsed = time.perf_counter() - started

    print(
        f"Loaded {len(files)} file(s): {summary['hierarchies']} hierarchies, "
        f"{summary['total']} products ({summary['new_records']} new, "
        f"{summary['updated_records']} updated) in {elapsed:.2f}s"
    )
    failed = [detail for detail in summary["details"] if detail["failed_rules"]]
    print(f"Validation: {len(failed)} product(s) failed at least one rule")
    for detail in failed:
        print(
            f"  {detail['primary_product_key']} ({detail['product_type']}): "
            f"{', '.join(detail['failed_rules'])}"
        )
        print(f"    log: {detail['log_file'] or 'NOT WRITTEN - see the error above'}")
    if failed:
        print(f"Log files are in {PRODUCT_LOG_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
