from pathlib import Path
from app.agents.base import BaseAgent
from app.infrastructure.parsers.doc_converter import convert_with_libreoffice
from app.infrastructure.parsers.docling_parser import parse_with_docling
class DocumentParsingAgent(BaseAgent):
    name="DocumentParsingAgent"
    async def run(self, path: Path, work: Path):
        if path.suffix.lower()==".doc": path=await convert_with_libreoffice(path, work, ".docx")
        return parse_with_docling(path)
