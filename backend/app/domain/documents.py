from pydantic import BaseModel
class DocumentFragment(BaseModel):
    section: str | None = None
    clause: str | None = None
    text: str
class ParsedDocument(BaseModel):
    text: str
    tables: list[list[list[str]]] = []
    fragments: list[DocumentFragment] = []
