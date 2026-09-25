"""
=====================================================
SCHEMAS (Pydantic models)
=====================================================
Used for validation & documentation of responses in the
Swagger UI. Not strictly enforced on every endpoint, but
provided to keep the response shape consistent and readable.
"""

from typing import Any, List, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class PostedRecord(BaseModel):
    """One posting — an entity or a location — as reported back by the
    POST endpoints."""

    status: str
    id: Optional[int] = None          # row id in the matching history table
    new_record: Optional[bool] = None  # True: created its row in the latest table
    entity_hash: Optional[str] = None  # sha256(systemCode + businessEntityCode)
    systemCode: Optional[str] = None
    businessEntityCode: Optional[str] = None
    joined: Optional[bool] = None      # the other side of the join exists


class ProcessSummary(BaseModel):
    total: int
    inserted: int                # rows added to the history table
    new_records: int             # rows created in the latest table
    updated_records: int         # rows in the latest table replaced by this posting
    joined: int                  # postings whose counterpart already existed
    awaiting_counterpart: int    # postings still missing their entity/location
    details: List[PostedRecord]


class EntityRecord(BaseModel):
    id: int
    businessEntityCode: Optional[str] = None
    EOID: Optional[str] = None
    FID: Optional[str] = None
    UKEOID: Optional[str] = None
    UKFID: Optional[str] = None
    created_at: Optional[str] = None


class ResponsibleContactIn(BaseModel):
    """One row of the responsible_contact table.

    related_field says what related_field_name refers to: a 'system' or a
    'country'. contact is free text — an email address or a Teams account.
    camelCase keys (relatedField, ...) are accepted as well.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    related_field: Literal["system", "country"] = Field(
        validation_alias=AliasChoices("related_field", "relatedField"))
    related_field_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("related_field_name", "relatedFieldName"))
    person_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("person_name", "personName"))
    contact: str = Field(min_length=1)

    @field_validator("related_field", mode="before")
    @classmethod
    def _norm_related_field(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("related_field_name", "person_name", "contact", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class ResponsibleContactResult(BaseModel):
    status: str
    id: int
    related_field: str
    related_field_name: str
    person_name: str
    contact: str


class ResponsibleContactSummary(BaseModel):
    total: int
    inserted: int
    skipped: int
    details: List[ResponsibleContactResult]
