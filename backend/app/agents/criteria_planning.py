from __future__ import annotations

import re

from app.agents.base import BaseAgent
from app.domain.criteria import ExtractionPlan


STOP_WORDS = {
    "и",
    "или",
    "по",
    "на",
    "в",
    "во",
    "из",
    "от",
    "до",
    "для",
    "при",
    "об",
    "о",
    "с",
    "со",
    "к",
    "ко",
    "за",
    "над",
    "под",
    "ли",
    "же",
    "это",
    "этот",
    "эта",
    "эти",
    "данные",
    "сведения",
    "информация",
    "указать",
    "укажите",
    "наличие",
    "значение",
    "договора",
    "договор",
}

SECTION_HINTS = {
    "header": (
        "номер",
        "дата",
        "предмет",
        "стороны",
        "контрагент",
        "заказчик",
        "исполнитель",
        "подрядчик",
        "поставщик",
    ),
    "payment": ("цена", "стоимость", "оплата", "аванс", "ндс", "сумма", "платеж", "расчет"),
    "deadlines": ("срок", "период", "дата", "начало", "окончание", "этап", "календар", "рабоч"),
    "liability": ("штраф", "пеня", "пени", "неустой", "ответствен", "санкц", "убыт"),
    "requisites": ("реквиз", "инн", "кпп", "огрн", "бик", "банк", "счет", "адрес", "email", "e-mail"),
    "acceptance": ("акт", "кс", "упд", "счет", "фактур", "закрыва", "прием", "сдач", "документ"),
    "termination": ("расторж", "прекращ", "отказ", "односторон"),
    "guarantee": ("гарант", "качество", "дефект", "недостат"),
}


class CriteriaPlanningAgent(BaseAgent):
    name = "CriteriaPlanningAgent"

    async def run(self, criteria):
        return [self.plan_one(c.name) for c in criteria]

    def plan_one(self, criterion: str) -> ExtractionPlan:
        normalized = _normalize(criterion)
        terms = _terms(criterion)
        expected_type = _expected_type(normalized)
        answer_format = _answer_format(normalized, expected_type)
        target_sections = _target_sections(normalized, terms)
        requires_calculation = any(
            marker in normalized
            for marker in ("рассчитать", "расчет", "сумма", "итого", "общий размер", "процент от")
        )
        allow_multiple = answer_format in {"list", "requisites", "table_summary"}

        return ExtractionPlan(
            criterion=criterion,
            strategy="hybrid_generic",
            target_sections=target_sections,
            expected_type=expected_type,
            requires_calculation=requires_calculation,
            query_terms=terms,
            answer_format=answer_format,
            allow_multiple=allow_multiple,
        )


def _expected_type(normalized: str) -> str:
    if any(marker in normalized for marker in ("дата", "срок подписания", "когда")):
        return "date"
    if any(marker in normalized for marker in ("цена", "стоимость", "сумма", "аванс", "оплата", "руб", "ндс")):
        return "money"
    if any(marker in normalized for marker in ("процент", "%", "ставка", "доля")):
        return "percent"
    if any(marker in normalized for marker in ("инн", "кпп", "огрн", "бик", "р с", "к с", "реквиз")):
        return "requisites"
    if any(marker in normalized for marker in ("номер", "№", "идентификатор")):
        return "identifier"
    if any(marker in normalized for marker in ("есть ли", "наличие", "предусмотрено ли", "да нет")):
        return "boolean"
    if any(marker in normalized for marker in ("перечень", "список", "какие", "документы", "обязанности", "санкции")):
        return "list"
    return "text"


def _answer_format(normalized: str, expected_type: str) -> str:
    if expected_type == "requisites":
        return "requisites"
    if expected_type == "list" or any(marker in normalized for marker in ("перечень", "список", "какие")):
        return "list"
    if "таблиц" in normalized or "таблица" in normalized:
        return "table_summary"
    return "short_text"


def _target_sections(normalized: str, terms: list[str]) -> list[str]:
    targets: list[str] = []
    searchable = " ".join([normalized, *terms])
    for section, markers in SECTION_HINTS.items():
        if any(marker in searchable for marker in markers):
            targets.append(section)
    return targets or ["header", "body", "tables", "tail"]


def _terms(value: str) -> list[str]:
    normalized = _normalize(value)
    result: list[str] = []
    for token in re.findall(r"[а-яa-z0-9№%\-/]{2,}", normalized):
        if len(token) <= 2 and token not in {"№", "%"}:
            continue
        if token in STOP_WORDS:
            continue
        result.append(token)

    for token in list(result):
        for suffix in (
            "ами",
            "ями",
            "ого",
            "ему",
            "ыми",
            "ими",
            "ая",
            "ое",
            "ые",
            "ий",
            "ый",
            "ой",
            "ам",
            "ям",
            "ах",
            "ях",
            "ов",
            "ев",
            "ом",
            "ем",
            "ия",
            "ие",
            "ии",
            "ей",
            "ую",
            "юю",
            "а",
            "я",
            "ы",
            "и",
            "е",
            "у",
        ):
            if len(token) > len(suffix) + 4 and token.endswith(suffix):
                result.append(token[: -len(suffix)])
                break

    return list(dict.fromkeys(result))


def _normalize(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
