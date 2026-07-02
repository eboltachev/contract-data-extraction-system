from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.documents import DocumentFragment, ParsedDocument


class WikiSourceRef(BaseModel):
    section: str | None = None
    clause: str | None = None
    text: str

    @classmethod
    def from_fragment(cls, fragment: DocumentFragment) -> "WikiSourceRef":
        return cls(section=fragment.section, clause=fragment.clause, text=fragment.text)

    def to_fragment(self, section: str | None = None) -> DocumentFragment:
        return DocumentFragment(
            section=section or self.section,
            clause=self.clause,
            text=self.text,
        )


class WikiFact(BaseModel):
    key: str
    label: str
    value: str
    normalized_value: str | None = None
    confidence: float = 0.0
    page_name: str | None = None
    source_refs: list[WikiSourceRef] = Field(default_factory=list)


class WikiPage(BaseModel):
    name: str
    title: str
    content: str
    facts: list[WikiFact] = Field(default_factory=list)
    source_refs: list[WikiSourceRef] = Field(default_factory=list)


class ContractWiki(BaseModel):
    pages: list[WikiPage] = Field(default_factory=list)
    facts: dict[str, WikiFact] = Field(default_factory=dict)

    def page(self, name: str) -> WikiPage | None:
        return next((page for page in self.pages if page.name == name), None)

    def to_parsed_document(self) -> ParsedDocument:
        fragments: list[DocumentFragment] = []
        tables: list[list[list[str]]] = []

        for page in self.pages:
            fragments.append(
                DocumentFragment(
                    section=f"wiki/{page.name}",
                    clause=None,
                    text=page.content,
                )
            )
            for fact in page.facts:
                fragments.append(
                    DocumentFragment(
                        section=f"wiki/{page.name}",
                        clause=fact.key,
                        text=f"{fact.label}: {fact.value}",
                    )
                )
                for source in fact.source_refs[:3]:
                    fragments.append(source.to_fragment(section=f"source/{page.name}"))

        return ParsedDocument(
            text="\n\n".join(fragment.text for fragment in fragments),
            tables=tables,
            fragments=_deduplicate_fragments(fragments),
        )


def _deduplicate_fragments(fragments: list[DocumentFragment]) -> list[DocumentFragment]:
    seen: set[tuple[str | None, str | None, str]] = set()
    result: list[DocumentFragment] = []
    for fragment in fragments:
        key = (fragment.section, fragment.clause, fragment.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(fragment)
    return result
