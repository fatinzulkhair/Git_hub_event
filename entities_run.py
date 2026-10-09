"""
Run with: python entities_run.py
(equivalent to "uvicorn entity_api.main:app --reload --reload-dir entity_api")

Only entity_api/ is watched for changes: watching the whole project would
include venv/, and every touched package file would restart the server.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "entity_api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["entity_api"],
    )
