from pathlib import Path
from app.agents.base import BaseAgent
from app.infrastructure.parsers.doc_converter import convert_with_libreoffice
from app.infrastructure.parsers.docx_parser import parse_docx
class DocumentParsingAgent(BaseAgent):
    name="DocumentParsingAgent"
    async def run(self, path: Path, work: Path):
        if path.suffix.lower()==".doc": path=await convert_with_libreoffice(path, work, ".docx")
        return parse_docx(path)
