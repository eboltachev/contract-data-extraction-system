from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.agents.base import BaseAgent
from app.agents.generic_extraction import GenericEvidenceExtractor
from app.domain.documents import DocumentFragment, ParsedDocument
from app.domain.extraction import ExtractionResult
from app.domain.wiki import ContractWiki, WikiFact, WikiPage, WikiSourceRef
from app.infrastructure.wiki.wiki_store import write_contract_wiki


NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."

MONTHS: dict[str, str] = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}

PAGE_SPECS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("passport.md", "Паспорт договора", ("contract.number", "contract.date", "contract.place", "contract.parties"), ("шапка", "преамбула")),
    ("parties.md", "Стороны", ("contract.parties", "contract.contractor", "contract.subcontractor"), ("именуем", "стороны", "подрядчик", "субподрядчик")),
    ("subject.md", "Предмет и объект", ("contract.subject", "contract.object", "contract.customer"), ("предмет договора", "объект", "работы")),
    ("price_and_vat.md", "Стоимость, НДС, авансы, удержания", ("contract.price.total", "contract.vat.rate", "contract.vat.amount", "contract.advances", "contract.retention.percent"), ("стоимость договора", "ндс", "аванс", "гарантийное удержание")),
    ("payment_terms.md", "Оплата и приемка", ("contract.payment.terms", "contract.acceptance.documents"), ("порядок оплаты", "приемки работ", "кс-2", "кс-3", "счет-фактура")),
    ("work_schedule.md", "Сроки выполнения работ", ("contract.work.start", "contract.work.end", "contract.work.deadlines"), ("сроки выполнения", "начало работ", "окончание работ")),
    ("acceptance.md", "Документы сдачи-приемки", ("contract.acceptance.documents", "contract.acceptance.final_act"), ("кс-2", "кс-3", "финальный акт", "дефектная ведомость")),
    ("warranty.md", "Гарантии качества", ("contract.warranty.months", "contract.defects.term"), ("гарантии качества", "гарантийный срок", "дефект")),
    ("penalties.md", "Ответственность и штрафы", ("contract.penalties",), ("ответственность", "штраф", "пеня", "неустой")),
    ("termination.md", "Расторжение", ("contract.termination",), ("расторжение", "односторон", "прекращ")),
    ("edo_and_notices.md", "ЭДО и уведомления", ("contract.edo", "contract.notices"), ("эдо", "электрон", "уведомлен", "почт", "e-mail")),
    ("appendices.md", "Приложения", ("contract.appendices",), ("приложение", "список приложений")),
    ("requisites.md", "Реквизиты сторон", ("contract.requisites",), ("реквизиты", "инн", "кпп", "огрн", "бик", "расчетный счет")),
)

CORE_CRITERIA: dict[str, str] = {
    "contract.number": "Номер договора",
    "contract.date": "Дата договора",
    "contract.parties": "Стороны договора / контрагенты",
    "contract.subject": "Предмет договора и работы",
    "contract.object": "Объект и адрес выполнения работ",
    "contract.price.total": "Стоимость договора",
    "contract.vat.amount": "Сумма НДС",
    "contract.advances": "Авансы по договору",
    "contract.retention.percent": "Гарантийное удержание",
    "contract.work.deadlines": "Сроки выполнения работ",
    "contract.acceptance.documents": "Закрывающие документы и документы приемки",
    "contract.warranty.months": "Гарантийный срок",
    "contract.penalties": "Штрафные санкции и ответственность",
    "contract.requisites": "Реквизиты сторон",
    "contract.termination": "Условия расторжения договора",
    "contract.edo": "Электронный документооборот ЭДО",
    "contract.notices": "Порядок уведомлений и переписки",
    "contract.appendices": "Список приложений к договору",
}


class ContractWikiIngestionAgent(BaseAgent):
    name = "ContractWikiIngestionAgent"

    async def run(self, parsed: ParsedDocument, output_dir: Path | None = None) -> ContractWiki:
        wiki = build_contract_wiki(parsed)
        if output_dir is not None:
            write_contract_wiki(wiki, output_dir)
        return wiki


def build_contract_wiki(parsed: ParsedDocument) -> ContractWiki:
    extractor = GenericEvidenceExtractor(parsed=parsed)
    facts = _collect_facts(parsed, extractor)
    pages = _build_pages(parsed, facts)
    index = _build_index_page(pages, facts)
    wiki = ContractWiki(pages=[index, *pages], facts=facts)
    return wiki


def _collect_facts(parsed: ParsedDocument, extractor: GenericEvidenceExtractor) -> dict[str, WikiFact]:
    facts: dict[str, WikiFact] = {}

    for key, criterion in CORE_CRITERIA.items():
        result = extractor.extract_one(criterion)
        if result is None:
            continue
        facts[key] = _fact_from_candidate(key, criterion, result)

    _upsert_regex_facts(parsed, facts)
    return {key: fact for key, fact in facts.items() if fact.value and fact.value != NOT_FOUND}


def _fact_from_candidate(key: str, label: str, candidate: Any) -> WikiFact:
    return WikiFact(
        key=key,
        label=label,
        value=_clean_fact_value(candidate.value),
        normalized_value=getattr(candidate, "normalized_value", None),
        confidence=float(getattr(candidate, "confidence", 0.0) or 0.0),
        source_refs=_source_refs(getattr(candidate, "source_fragments", []) or []),
    )


def _upsert_regex_facts(parsed: ParsedDocument, facts: dict[str, WikiFact]) -> None:
    text = _normalized_text(parsed.text)
    head = text[:40_000]

    number = _extract_contract_number(head)
    if number:
        facts["contract.number"] = _regex_fact(
            "contract.number",
            "Номер договора",
            number,
            number,
            0.995,
            parsed,
            ("№", number),
        )

    date = _extract_contract_date(head)
    if date:
        value, normalized = date
        facts["contract.date"] = _regex_fact(
            "contract.date",
            "Дата договора",
            value,
            normalized,
            0.99,
            parsed,
            tuple(value.split()[:3]),
        )

    price = _extract_price(text)
    if price:
        value, normalized = price
        facts["contract.price.total"] = _regex_fact(
            "contract.price.total",
            "Стоимость договора",
            value,
            normalized,
            0.97,
            parsed,
            ("Стоимость Договора", value.split()[0]),
        )

    vat = _extract_vat(text)
    if vat:
        rate, amount_value, normalized_amount = vat
        facts["contract.vat.rate"] = _regex_fact(
            "contract.vat.rate",
            "Ставка НДС",
            rate,
            rate,
            0.96,
            parsed,
            ("НДС", rate),
        )
        facts["contract.vat.amount"] = _regex_fact(
            "contract.vat.amount",
            "Сумма НДС",
            amount_value,
            normalized_amount,
            0.96,
            parsed,
            ("НДС", amount_value.split()[0]),
        )

    advances = _extract_advances(text)
    if advances:
        facts["contract.advances"] = _regex_fact(
            "contract.advances",
            "Авансы по договору",
            "\n".join(f"- {item}" for item in advances),
            None,
            0.93,
            parsed,
            ("Аванс", "3.1"),
        )

    retention = _extract_retention(text)
    if retention:
        facts["contract.retention.percent"] = _regex_fact(
            "contract.retention.percent",
            "Гарантийное удержание",
            retention,
            retention.replace("%", "").strip(),
            0.96,
            parsed,
            ("Гарантийное удержание", retention),
        )

    start = _extract_date_after(text, r"Начало\s+Работ\s*:")
    end = _extract_date_after(text, r"Окончание\s+Работ\s*:")
    if start:
        facts["contract.work.start"] = _regex_fact(
            "contract.work.start",
            "Дата начала работ",
            start[0],
            start[1],
            0.97,
            parsed,
            ("Начало Работ",),
        )
    if end:
        facts["contract.work.end"] = _regex_fact(
            "contract.work.end",
            "Дата окончания работ",
            end[0],
            end[1],
            0.97,
            parsed,
            ("Окончание Работ",),
        )

    warranty = _extract_warranty(text)
    if warranty:
        facts["contract.warranty.months"] = _regex_fact(
            "contract.warranty.months",
            "Гарантийный срок",
            warranty,
            re.sub(r"\D+", "", warranty) or None,
            0.96,
            parsed,
            ("Гарантийный срок", warranty),
        )

    appendices = _extract_appendices(parsed.fragments)
    if appendices:
        facts["contract.appendices"] = WikiFact(
            key="contract.appendices",
            label="Список приложений к договору",
            value="\n".join(f"- {item}" for item in appendices),
            confidence=0.94,
            source_refs=_source_refs(_find_fragments(parsed.fragments, ("Список приложений", "Приложение №"), 8)),
        )


def _build_pages(parsed: ParsedDocument, facts: dict[str, WikiFact]) -> list[WikiPage]:
    pages: list[WikiPage] = []

    for page_name, title, fact_keys, section_markers in PAGE_SPECS:
        page_facts = [facts[key].model_copy(update={"page_name": page_name}) for key in fact_keys if key in facts]
        for fact in page_facts:
            facts[fact.key] = fact

        source_fragments = _find_fragments(parsed.fragments, section_markers, limit=10)
        content = _render_page(title, page_facts, source_fragments)
        pages.append(
            WikiPage(
                name=page_name,
                title=title,
                content=content,
                facts=page_facts,
                source_refs=_source_refs(source_fragments),
            )
        )

    return pages


def _build_index_page(pages: list[WikiPage], facts: dict[str, WikiFact]) -> WikiPage:
    lines = ["# Contract Wiki", "", "## Страницы"]
    for page in pages:
        lines.append(f"- [{page.title}]({page.name}) — {len(page.facts)} фактов")
    lines.extend(["", "## Индекс фактов"])
    for key, fact in sorted(facts.items()):
        page_name = fact.page_name or ""
        value = _single_line(fact.value, limit=180)
        lines.append(f"- `{key}` ({page_name}): {value}")
    return WikiPage(name="index.md", title="Contract Wiki", content="\n".join(lines), facts=list(facts.values()))


def _render_page(title: str, facts: list[WikiFact], source_fragments: list[DocumentFragment]) -> str:
    lines = [f"# {title}", ""]

    if facts:
        lines.append("## Нормализованные факты")
        for fact in facts:
            lines.append(f"- **{fact.label}** (`{fact.key}`): {fact.value}")
            if fact.normalized_value:
                lines.append(f"  - normalized: `{fact.normalized_value}`")
            if fact.source_refs:
                source = fact.source_refs[0]
                clause = f", п. {source.clause}" if source.clause else ""
                lines.append(f"  - source: {source.section or 'Документ'}{clause}")
        lines.append("")
    else:
        lines.extend(["## Нормализованные факты", "- NOT_FOUND", ""])

    if source_fragments:
        lines.append("## Source snippets")
        for fragment in source_fragments[:8]:
            clause = f" п. {fragment.clause}" if fragment.clause else ""
            lines.append(f"### {fragment.section or 'Документ'}{clause}")
            lines.append(_single_line(fragment.text, limit=1000))
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _regex_fact(
    key: str,
    label: str,
    value: str,
    normalized_value: str | None,
    confidence: float,
    parsed: ParsedDocument,
    source_terms: tuple[str, ...],
) -> WikiFact:
    return WikiFact(
        key=key,
        label=label,
        value=_clean_value(value),
        normalized_value=normalized_value,
        confidence=confidence,
        source_refs=_source_refs(_find_fragments(parsed.fragments, source_terms, limit=5)),
    )


def _extract_contract_number(text: str) -> str | None:
    patterns = (
        r"ДОГОВОР[^\n\r]{0,180}?№\s*([^\n\r]{2,140}?)(?=\s+от\s+[«\"]?\d{1,2}|\n|$)",
        r"№\s*([^\n\r]{2,140}?)(?=\s+от\s+[«\"]?\d{1,2}|\n|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = _clean_value(match.group(1))
        value = re.sub(r"\s+от\s+.*$", "", value, flags=re.I).strip(" .;,:")
        if value and not value.lower().startswith("приложение") and re.search(r"\d", value):
            return value
    return None


def _extract_contract_date(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?:от\s+)?[«\"]?(\d{1,2})[»\"]?\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
        r"(\d{4})\s*(?:г\.|года)?",
        text,
        flags=re.I,
    )
    if match:
        day, month_name, year = match.groups()
        month = month_name.lower()
        return f"{int(day)} {month} {year} г.", f"{year}-{MONTHS[month]}-{int(day):02d}"

    match = re.search(r"(?:от\s+)?(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if match:
        day, month, year = match.groups()
        year = f"20{year}" if len(year) == 2 else year
        return f"{int(day):02d}.{int(month):02d}.{year}", f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _extract_price(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"Стоимость\s+Договора[^.\n]{0,500}?составляет\s+(.{0,260}?руб(?:ля|лей|ль|\.)?\s+\d{1,2}\s+коп(?:еек|ейки|\.)?)",
        text,
        flags=re.I | re.S,
    )
    if not match:
        match = re.search(
            r"(?:цена|стоимость|сумма)[^.\n]{0,240}?(\d[\d\s.,]{3,}\s*руб(?:ля|лей|ль|\.)?(?:\s+\d{1,2}\s+коп(?:еек|ейки|\.))?)",
            text,
            flags=re.I | re.S,
        )
    if not match:
        return None
    value = _clean_value(match.group(1))
    normalized = _money_to_decimal(value)
    return value, normalized or value


def _extract_vat(text: str) -> tuple[str, str, str | None] | None:
    match = re.search(
        r"НДС\s*(\d+(?:[,.]\d+)?)\s*%[^.\n]{0,180}?размере\s+(.{0,180}?руб(?:лей|ля|ль|\.)?\s+\d{1,2}\s+коп(?:еек|ейка|ейки|\.))",
        text,
        flags=re.I | re.S,
    )
    if not match:
        match = re.search(r"НДС\s*(\d+(?:[,.]\d+)?)\s*%[^.\n]{0,240}", text, flags=re.I | re.S)
        if not match:
            return None
        return f"{match.group(1).replace(',', '.')}%", _clean_value(match.group(0)), None
    rate, amount = match.groups()
    amount = _clean_value(amount)
    return f"{rate.replace(',', '.')}%", amount, _money_to_decimal(amount)


def _extract_advances(text: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(
        r"Аванс\s+в\s+размере\s+(.{0,180}?руб(?:лей|ля|ль|\.)?\s+\d{1,2}\s+коп(?:еек|ейка|ейки|\.))[^.\n]{0,260}",
        text,
        flags=re.I | re.S,
    ):
        result.append(_single_line(match.group(0), 500))
    return _deduplicate_strings(result)


def _extract_retention(text: str) -> str | None:
    match = re.search(r"Гарантийное\s+удержание[^.\n]{0,260}?равную\s+(\d+(?:[,.]\d+)?)\s*%", text, flags=re.I | re.S)
    if not match:
        match = re.search(r"(\d+(?:[,.]\d+)?)\s*%[^.\n]{0,180}?Гарантийн[а-я]+\s+удержан", text, flags=re.I | re.S)
    return f"{match.group(1).replace(',', '.')}%" if match else None


def _extract_date_after(text: str, prefix_pattern: str) -> tuple[str, str] | None:
    match = re.search(
        prefix_pattern + r"\s*[«\"]?(\d{1,2})[»\"]?\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
        r"(\d{4})\s*(?:г\.|года)?",
        text,
        flags=re.I,
    )
    if not match:
        return None
    day, month_name, year = match.groups()
    month = month_name.lower()
    return f"{int(day)} {month} {year} г.", f"{year}-{MONTHS[month]}-{int(day):02d}"


def _extract_warranty(text: str) -> str | None:
    match = re.search(
        r"Гарантийный\s+срок[^.\n]{0,260}?на\s+срок\s+(\d+)\s*\([^)]*\)?\s+месяц",
        text,
        flags=re.I | re.S,
    )
    if not match:
        match = re.search(r"Гарантийный\s+срок[^.\n]{0,200}?(\d+)\s+месяц", text, flags=re.I | re.S)
    return f"{match.group(1)} месяцев" if match else None


def _extract_appendices(fragments: list[DocumentFragment]) -> list[str]:
    items: list[str] = []
    in_appendices = False
    for fragment in fragments:
        text = _clean_value(fragment.text)
        normalized = _normalize_key(text)
        if "список приложений" in normalized:
            in_appendices = True
            continue
        if in_appendices and re.match(r"^\d{1,2}\.", text):
            break
        if in_appendices or normalized.startswith("17") or normalized.startswith("приложение №"):
            match = re.search(r"Приложение\s*№\s*\d+[^\n]{0,220}", text, flags=re.I)
            if match:
                items.append(_clean_value(match.group(0)))
    return _deduplicate_strings(items)


def _money_to_decimal(value: str) -> str | None:
    normalized = value.replace("\xa0", " ")
    first_number = re.search(r"\d[\d\s.]{2,}", normalized)
    kop_match = re.search(r"руб[^\d]{0,40}(\d{1,2})\s*коп", normalized, flags=re.I)
    if not first_number:
        return None
    rub = re.sub(r"\D", "", first_number.group(0))
    kop = (kop_match.group(1) if kop_match else "00").zfill(2)[:2]
    try:
        return str(Decimal(f"{rub}.{kop}"))
    except InvalidOperation:
        return None


def _find_fragments(
    fragments: list[DocumentFragment],
    markers: Iterable[str],
    limit: int = 6,
) -> list[DocumentFragment]:
    normalized_markers = [_normalize_key(marker) for marker in markers if marker]
    scored: list[tuple[int, int, DocumentFragment]] = []
    for idx, fragment in enumerate(fragments):
        haystack = _normalize_key(" ".join([fragment.section or "", fragment.clause or "", fragment.text]))
        score = sum(1 for marker in normalized_markers if marker and marker in haystack)
        if score:
            scored.append((score, -idx, fragment))
    return [item[2] for item in sorted(scored, reverse=True)[:limit]]


def _source_refs(fragments: list[DocumentFragment]) -> list[WikiSourceRef]:
    return [WikiSourceRef.from_fragment(fragment) for fragment in fragments[:8]]


def _normalized_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()



def _clean_fact_value(value: Any) -> str:
    raw = str(value).replace("\xa0", " ").replace("\r", "\n")
    lines = [_clean_value(line) for line in raw.split("\n")]
    lines = [line for line in lines if line]
    if len(lines) > 1:
        return "\n".join(lines)
    return _clean_value(raw)

def _clean_value(value: Any) -> str:
    value = str(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    return value.strip(" \n\t;,.:—–")


def _single_line(value: str, limit: int = 220) -> str:
    value = _clean_value(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _normalize_key(value: str) -> str:
    value = value.lower().replace("ё", "е").replace("\xa0", " ")
    value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _deduplicate_strings(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = _clean_value(item)
        key = _normalize_key(value)
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result
