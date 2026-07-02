from __future__ import annotations

from pathlib import Path

from app.domain.documents import DocumentFragment, ParsedDocument
from app.infrastructure.parsers.docx_parser import parse_docx


def parse_with_docling(path: Path) -> ParsedDocument:
    native = _parse_native_docx(path)

    try:
        from docling.document_converter import DocumentConverter

        result = DocumentConverter().convert(str(path))
        markdown = result.document.export_to_markdown()
        docling_parsed = _parsed_from_markdown(markdown)
    except Exception:
        if native is not None:
            return native
        raise

    if native is None:
        return docling_parsed

    return _merge_parsed(primary=native, secondary=docling_parsed)


def _parse_native_docx(path: Path) -> ParsedDocument | None:
    if path.suffix.lower() != ".docx":
        return None

    try:
        return parse_docx(path)
    except Exception:
        return None


def _parsed_from_markdown(markdown: str) -> ParsedDocument:
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


def _merge_parsed(primary: ParsedDocument, secondary: ParsedDocument) -> ParsedDocument:
    seen: set[tuple[str | None, str | None, str]] = set()
    fragments: list[DocumentFragment] = []

    for fragment in [*primary.fragments, *secondary.fragments]:
        key = (fragment.section, fragment.clause, fragment.text)
        if key in seen:
            continue

        seen.add(key)
        fragments.append(fragment)

    text = "\n\n".join(
        part for part in [primary.text.strip(), secondary.text.strip()] if part
    )

    return ParsedDocument(
        text=text,
        tables=primary.tables or secondary.tables,
        fragments=fragments,
    )
