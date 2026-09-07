"""
=====================================================
SCHEMAS (Pydantic models)
=====================================================
Used for validation & documentation of responses in the
Swagger UI. Not strictly enforced on every endpoint, but
provided to keep the response shape consistent and readable.
"""

from typing import Any, List, Optional

from pydantic import BaseModel


class EntityResult(BaseModel):
    status: str
    reason: Optional[str] = None
    id: Optional[int] = None
    existing_id: Optional[int] = None
    businessEntityCode: Optional[str] = None


class ProcessSummary(BaseModel):
    total_entities: int
    inserted: int
    skipped: int
    details: List[EntityResult]


class EntityRecord(BaseModel):
    id: int
    businessEntityCode: Optional[str] = None
    EOID: Optional[str] = None
    FID: Optional[str] = None
    UKEOID: Optional[str] = None
    UKFID: Optional[str] = None
    created_at: Optional[str] = None
