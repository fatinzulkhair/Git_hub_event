"""
Run with: python run.py
(equivalent to running "uvicorn entity_api.main:app --reload")
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("entity_api.main:app", host="0.0.0.0", port=8000, reload=True)
