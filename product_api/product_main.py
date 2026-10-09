"""
=====================================================
APP FACTORY / ENTRY POINT
=====================================================
This file wires together all the modules (config, database,
routers) into a single FastAPI object. It's the only place
that knows about the "app" as a whole.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .product_config import (
    PRODUCT_APP_DESCRIPTION,
    PRODUCT_APP_TITLE,
    PRODUCT_APP_VERSION,
)
from .product_database import initialize_product_database
from .product_routers import (
    product_responsible_contacts,
    product_validation_rules,
    products,
)


# lifespan is FastAPI's replacement for @app.on_event("startup") (which
# entity_api/main.py uses and which is now deprecated) — same effect.
@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_product_database()
    yield


app = FastAPI(
    title=PRODUCT_APP_TITLE,
    description=PRODUCT_APP_DESCRIPTION,
    version=PRODUCT_APP_VERSION,
    lifespan=lifespan,
)

app.include_router(products.router)
app.include_router(product_validation_rules.router)
app.include_router(product_responsible_contacts.router)
