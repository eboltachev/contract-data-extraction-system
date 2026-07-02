from __future__ import annotations

from pydantic import BaseModel, Field


class Criterion(BaseModel):
    row: int
    name: str
    sheet_name: str | None = None
    criterion_col: int | None = None
    value_col: int | None = None


class ExtractionPlan(BaseModel):
    criterion: str
    strategy: str = "hybrid"
    target_sections: list[str] = Field(default_factory=list)
    expected_type: str = "text"
    requires_calculation: bool = False
    query_terms: list[str] = Field(default_factory=list)
    answer_format: str = "short_text"
    allow_multiple: bool = False
    field_key: str | None = None
    allowed_wiki_pages: list[str] = Field(default_factory=list)
