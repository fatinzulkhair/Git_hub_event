"""
=====================================================
SCHEMAS (Pydantic models)
=====================================================
Used for validation & documentation of responses in the
Swagger UI, so the POST responses keep one consistent,
readable shape.
"""

from typing import List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class ProductPostedRecord(BaseModel):
    """One product — one level of a hierarchy — as reported back by the POST
    endpoints."""

    status: str
    id: int                          # row id in product_history
    new_record: bool                 # True: created its row in product_latest
    product_hash: str                # sha256(PrimaryProductKey)
    primary_product_key: str
    product_code: Optional[str] = None  # e.g. FA003115, shared by every level
    product_type: Optional[str] = None
    level: int                       # 0 = top of its hierarchy
    failed_rules: List[str]          # rule_ids this posting failed
    log_file: Optional[str] = None   # its failed-validation log (product_validation_logger.py)


class ProductProcessSummary(BaseModel):
    total: int                       # products stored (every level counts)
    hierarchies: int                 # top-level products in the payload
    inserted: int                    # rows added to product_history
    new_records: int                 # rows created in product_latest
    updated_records: int             # rows in product_latest replaced by this posting
    failed_validation: int           # products that failed at least one rule
    details: List[ProductPostedRecord]


class ProductResponsibleContactIn(BaseModel):
    """One row of the product_responsible_contact table.

    product_code is the code every level of a product shares — the last part
    of ProductDefinitionKey, e.g. FA003115 (see product_parser.product_code_of).
    contact is free text — an email address or a Teams account. camelCase
    keys (productCode, personName) are accepted as well.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # max_length: the column sizes (a longer value is a 422, not a database error)
    product_code: str = Field(
        min_length=1, max_length=200,
        validation_alias=AliasChoices("product_code", "productCode"))
    person_name: str = Field(
        min_length=1, max_length=200,
        validation_alias=AliasChoices("person_name", "personName"))
    contact: str = Field(min_length=1, max_length=400)

    @field_validator("product_code", "person_name", "contact", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class ProductResponsibleContactResult(BaseModel):
    status: str                      # inserted, or skipped (already there)
    id: int
    product_code: str
    person_name: str
    contact: str


class ProductResponsibleContactSummary(BaseModel):
    total: int
    inserted: int
    skipped: int
    details: List[ProductResponsibleContactResult]
