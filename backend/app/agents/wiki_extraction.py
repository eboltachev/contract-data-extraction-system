from __future__ import annotations

import re

from app.domain.documents import DocumentFragment
from app.domain.extraction import ExtractionResult
from app.domain.wiki import ContractWiki, WikiFact

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."

FIELD_KEY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contract.number", ("номер договор", "номер контракт", "№ договор")),
    ("contract.date", ("дата договор", "дата подпис", "дата заключ")),
    ("contract.work.start", ("начало работ", "дата начала")),
    ("contract.work.end", ("окончание работ", "дата окончания")),
    ("contract.work.deadlines", ("срок работ", "срок исполн", "период выполн")),
    ("contract.price.total", ("стоимость", "цена", "сумма договор")),
    ("contract.vat.amount", ("ндс", "налог на добавлен")),
    ("contract.advances", ("аванс", "предоплат")),
    ("contract.retention.percent", ("гарантийн удержан", "удержан")),
    (
        "contract.acceptance.documents",
        (
            "закрывающ документ",
            "закрывающ",
            "закрытие",
            "закрытия",
            "закрыт",
            "необходим документ",
            "документ для закры",
            "документ прием",
            "документ сдач",
            "кс 2",
            "кс 3",
            "акт прием",
            "счет фактур",
        ),
    ),
    ("contract.warranty.months", ("гарантийн срок", "гарантия")),
    ("contract.penalties", ("штраф", "пеня", "пени", "неустой", "ответствен", "санкц")),
    ("contract.requisites", ("реквиз", "инн", "кпп", "огрн", "бик", "расчетн счет", "р с")),
    ("contract.parties", ("сторон", "контрагент", "подрядчик", "субподрядчик", "заказчик", "исполнитель")),
    ("contract.subject", ("предмет", "вид работ", "комплекс работ", "услуг", "поставка")),
    ("contract.object", ("объект", "адрес объект", "место выполн")),
    ("contract.termination", ("расторж", "прекращ", "односторон")),
    ("contract.edo", ("эдо", "электронн документооборот", "кэп", "сбис")),
    ("contract.notices", ("уведомлен", "сообщен", "переписк", "e mail", "email")),
    ("contract.appendices", ("приложен", "список прилож")),
)


class WikiFactMatcher:
    def __init__(self, wiki: ContractWiki) -> None:
        self.wiki = wiki

    def extract_many(self, criteria: list[str] | tuple[str, ...]) -> dict[str, ExtractionResult]:
        result: dict[str, ExtractionResult] = {}
        for criterion in criteria:
            fact = self.best_fact(criterion)
            if fact is None:
                continue
            result[criterion] = self._to_result(criterion, fact)
        return result

    def best_fact(self, criterion: str) -> WikiFact | None:
        normalized = _normalize(criterion)

        # Важный guardrail: «документы для закрытия/приемки/сдачи» и
        # «приложения к договору» — разные сущности. Без этого fallback
        # может ошибочно выбрать список приложений как перечень закрывающих
        # документов.
        if _is_closing_documents_query(normalized):
            return self.wiki.facts.get("contract.acceptance.documents")

        if _is_appendices_query(normalized):
            return self.wiki.facts.get("contract.appendices")

        for key, markers in FIELD_KEY_MARKERS:
            if key == "contract.appendices" and not _is_appendices_query(normalized):
                continue
            if any(marker in normalized for marker in markers) and key in self.wiki.facts:
                return self.wiki.facts[key]

        scored: list[tuple[float, WikiFact]] = []
        criterion_terms = set(_terms(normalized))
        for fact in self.wiki.facts.values():
            if fact.key == "contract.appendices" and not _is_appendices_query(normalized):
                continue
            haystack = _normalize(" ".join([fact.key, fact.label, fact.value]))
            score = len(criterion_terms & set(_terms(haystack)))
            if score:
                scored.append((score + fact.confidence, fact))

        if not scored:
            return None
        score, fact = max(scored, key=lambda item: item[0])
        return fact if score >= 2.2 else None

    @staticmethod
    def _to_result(criterion: str, fact: WikiFact) -> ExtractionResult:
        fragments = [
            source.to_fragment(section=f"wiki_source/{fact.page_name or fact.key}")
            for source in fact.source_refs[:5]
        ] or [DocumentFragment(section=f"wiki/{fact.page_name or 'facts'}", clause=fact.key, text=fact.value)]
        return ExtractionResult(
            criterion=criterion,
            value=fact.value,
            normalized_value=fact.normalized_value,
            confidence=max(0.0, min(1.0, fact.confidence)),
            source_fragments=fragments,
            reasoning_summary=f"Значение извлечено из contract-wiki fact `{fact.key}`.",
        )



def _is_closing_documents_query(normalized: str) -> bool:
    closing_markers = (
        "закрыт",
        "закрытие",
        "закрытия",
        "закрывающ",
        "кс 2",
        "кс 3",
        "счет фактур",
    )
    document_markers = ("документ", "акт", "счет", "прием", "приемк", "сдач")
    return (
        any(marker in normalized for marker in closing_markers)
        and any(marker in normalized for marker in document_markers)
    ) or (
        "перечень" in normalized
        and "документ" in normalized
        and any(marker in normalized for marker in ("закры", "прием", "сдач"))
    )


def _is_appendices_query(normalized: str) -> bool:
    return "приложен" in normalized or "список прилож" in normalized

def _normalize(value: str) -> str:
    value = value.lower().replace("ё", "е").replace("\xa0", " ")
    value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _terms(value: str) -> list[str]:
    stop = {"договор", "договора", "контракт", "контракта", "сведения", "значение", "указать", "какие", "какой", "есть"}
    return [token for token in re.findall(r"[а-яa-z0-9№%\-/]{3,}", value) if token not in stop]
