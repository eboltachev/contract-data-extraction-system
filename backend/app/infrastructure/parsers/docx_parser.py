from pathlib import Path
from docx import Document
from app.domain.documents import ParsedDocument, DocumentFragment

def parse_docx(path: Path) -> ParsedDocument:
    doc=Document(path); fragments=[]; tables=[]; current="header"
    for p in doc.paragraphs:
        text=p.text.strip()
        if not text: continue
        style=(p.style.name or "").lower()
        if "heading" in style or text.isupper(): current=text[:120]
        fragments.append(DocumentFragment(section=current, text=text))
    for t in doc.tables:
        rows=[]
        for r in t.rows:
            cells=[c.text.strip() for c in r.cells]; rows.append(cells)
            line=" | ".join(cells)
            if line.strip(): fragments.append(DocumentFragment(section=current, text=line))
        tables.append(rows)
    return ParsedDocument(text="\n".join(f.text for f in fragments), tables=tables, fragments=fragments)
