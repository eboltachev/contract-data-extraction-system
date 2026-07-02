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
        field_key = _field_key(normalized)
        allowed_wiki_pages = _allowed_wiki_pages(field_key, normalized)
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
            field_key=field_key,
            allowed_wiki_pages=allowed_wiki_pages,
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



def _field_key(normalized: str) -> str | None:
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("contract.number", ("номер договор", "номер контракт", "№ договор")),
        ("contract.date", ("дата договор", "дата подпис", "дата заключ")),
        ("contract.price.total", ("стоимость", "цена", "сумма договор")),
        ("contract.vat.amount", ("ндс", "налог на добавлен")),
        ("contract.advances", ("аванс", "предоплат")),
        ("contract.retention.percent", ("гарантийн удержан", "удержан")),
        ("contract.work.start", ("начало работ", "дата начала")),
        ("contract.work.end", ("окончание работ", "дата окончания")),
        ("contract.work.deadlines", ("срок работ", "срок исполн", "период выполн")),
        (
            "contract.acceptance.documents",
            (
                "закрыва",
                "закрыт",
                "закрытие",
                "закрытия",
                "закрывающ",
                "необходим документ",
                "перечень необходим",
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
        ("contract.subject", ("предмет", "работ", "услуг", "поставка")),
        ("contract.object", ("объект", "адрес объект", "место выполн")),
        ("contract.termination", ("расторж", "прекращ", "односторон")),
        ("contract.edo", ("эдо", "электронн документооборот", "кэп", "сбис")),
        ("contract.notices", ("уведомлен", "сообщен", "переписк", "e mail", "email")),
        ("contract.appendices", ("приложен", "список прилож")),
    )
    for key, markers in checks:
        if any(marker in normalized for marker in markers):
            return key
    return None


def _allowed_wiki_pages(field_key: str | None, normalized: str) -> list[str]:
    if not field_key:
        return []
    by_prefix = {
        "contract.number": ["index.md", "passport.md"],
        "contract.date": ["index.md", "passport.md"],
        "contract.parties": ["parties.md", "passport.md", "requisites.md"],
        "contract.subject": ["subject.md", "passport.md"],
        "contract.object": ["subject.md", "passport.md"],
        "contract.price": ["price_and_vat.md"],
        "contract.vat": ["price_and_vat.md"],
        "contract.advances": ["price_and_vat.md", "payment_terms.md"],
        "contract.retention": ["price_and_vat.md", "payment_terms.md"],
        "contract.work": ["work_schedule.md"],
        "contract.acceptance": ["acceptance.md", "payment_terms.md"],
        "contract.warranty": ["warranty.md"],
        "contract.penalties": ["penalties.md"],
        "contract.requisites": ["requisites.md"],
        "contract.termination": ["termination.md"],
        "contract.edo": ["edo_and_notices.md"],
        "contract.notices": ["edo_and_notices.md"],
        "contract.appendices": ["appendices.md"],
    }
    for prefix, pages in by_prefix.items():
        if field_key.startswith(prefix):
            return pages
    return []
