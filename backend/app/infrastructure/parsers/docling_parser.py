from pathlib import Path

from docling.document_converter import DocumentConverter

from app.domain.documents import DocumentFragment, ParsedDocument


def parse_with_docling(path: Path) -> ParsedDocument:
    """Parse a contract with Docling and preserve document/table structure as fragments."""
    result = DocumentConverter().convert(str(path))
    document = result.document
    markdown = document.export_to_markdown()
    fragments: list[DocumentFragment] = []
    current_section = "Общее"
    for block in markdown.split("\n\n"):
        text = block.strip()
        if not text:
            continue
        if text.startswith("#"):
            current_section = text.lstrip("#").strip() or current_section
            continue
        fragments.append(DocumentFragment(section=current_section, text=text))
    if not fragments and markdown.strip():
        fragments.append(DocumentFragment(section="Общее", text=markdown.strip()))
    return ParsedDocument(text=markdown, tables=[], fragments=fragments)
