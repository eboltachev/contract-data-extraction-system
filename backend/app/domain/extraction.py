from pydantic import BaseModel
from app.domain.documents import DocumentFragment
class ExtractionResult(BaseModel):
    criterion: str
    value: str
    normalized_value: str | None = None
    confidence: float = 0.0
    source_fragments: list[DocumentFragment] = []
    reasoning_summary: str = ""
