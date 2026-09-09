"""
=====================================================
APP FACTORY / ENTRY POINT
=====================================================
This file wires together all the modules (config, database,
routers) into a single FastAPI object. It's the only place
that knows about the "app" as a whole.
"""

from fastapi import FastAPI

from .config import APP_DESCRIPTION, APP_TITLE, APP_VERSION
from .database import initialize_database
from .routers import entities, validation_rules

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)

app.include_router(entities.router)
app.include_router(validation_rules.router)


@app.on_event("startup")
def on_startup():
    initialize_database()
