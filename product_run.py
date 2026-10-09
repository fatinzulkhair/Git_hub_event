"""
Run with: python product_run.py
(equivalent to "uvicorn product_api.product_main:app --reload --port 8001
 --reload-dir product_api")

Port 8001, so it can run next to the entity API (entities_run.py, port 8000).
Only product_api/ is watched for changes: watching the whole project would
include venv/, and every touched package file would restart the server.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "product_api.product_main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        reload_dirs=["product_api"],
    )
