from __future__ import annotations

import asyncio
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.agents.base import BaseAgent


class ValidationAgent(BaseAgent):
    name = "ValidationAgent"

    async def validate_one(self, result):
        value = (result.value or "").strip()
        ok = bool(value) and "Не найдено" not in value
        if not ok:
            return result

        warnings: list[str] = []
        normalized_criterion = result.criterion.lower().replace("ё", "е")

        if result.confidence < 0.45:
            warnings.append("низкая уверенность")

        if _looks_like_appendices_for_closing_documents(normalized_criterion, value):
            warnings.append("значение похоже на список приложений, а не на закрывающие документы")

        if "дата" in normalized_criterion and not _has_date(value):
            warnings.append("значение не похоже на дату")

        if any(marker in normalized_criterion for marker in ("инн", "кпп", "огрн", "бик", "реквиз")):
            warnings.extend(_validate_requisites(value))

        if any(marker in normalized_criterion for marker in ("стоимость", "сумма", "цена", "аванс", "ндс")):
            if not _has_money(value):
                warnings.append("значение не содержит денежной суммы")

        if "процент" in normalized_criterion or "%" in normalized_criterion or "удержан" in normalized_criterion:
            percent = _first_percent(value)
            if percent is not None and not Decimal("0") <= percent <= Decimal("100"):
                warnings.append("процент вне диапазона 0..100")

        if warnings and not value.startswith("Требует проверки:"):
            result.value = f"Требует проверки ({'; '.join(warnings)}): {value}"
            result.reasoning_summary = _append_summary(result.reasoning_summary, warnings)

        return result

    async def run(self, results):
        return await asyncio.gather(*(self.validate_one(result) for result in results))



def _looks_like_appendices_for_closing_documents(normalized_criterion: str, value: str) -> bool:
    closing_query = (
        any(marker in normalized_criterion for marker in ("закрыт", "закрытие", "закрытия", "закрыва", "прием", "приемк", "сдач"))
        and "документ" in normalized_criterion
    )
    if not closing_query:
        return False
    normalized_value = value.lower().replace("ё", "е")
    appendix_hits = len(re.findall(r"приложение\s*№", normalized_value))
    has_acceptance_docs = bool(re.search(r"кс\s*-?\s*2|кс\s*-?\s*3|счет[\s-]?фактур|финальн[а-я]+\s+акт", normalized_value))
    return appendix_hits >= 3 and not has_acceptance_docs

def _append_summary(summary: str, warnings: list[str]) -> str:
    suffix = "Validation warnings: " + "; ".join(warnings)
    return f"{summary} {suffix}".strip() if summary else suffix


def _has_date(value: str) -> bool:
    return bool(
        re.search(r"\d{4}-\d{2}-\d{2}", value)
        or re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", value)
        or re.search(
            r"\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}",
            value,
            flags=re.I,
        )
    )


def _has_money(value: str) -> bool:
    return bool(re.search(r"\d", value) and re.search(r"(?:руб|₽)", value, flags=re.I))


def _first_percent(value: str) -> Decimal | None:
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*%", value)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _validate_requisites(value: str) -> list[str]:
    warnings: list[str] = []
    checks = (
        ("ИНН", r"\bИНН\s*:?\s*(\d+)", {10, 12}),
        ("КПП", r"\bКПП\s*:?\s*(\d+)", {9}),
        ("ОГРН", r"\bОГРН(?:ИП)?\s*:?\s*(\d+)", {13, 15}),
        ("БИК", r"\bБИК\s*:?\s*(\d+)", {9}),
        ("расчетный счет", r"(?:р/с|расчетный счет)\s*№?\s*(\d+)", {20}),
        ("корреспондентский счет", r"(?:к/с|корреспондентский счет)\s*№?\s*(\d+)", {20}),
    )
    for label, pattern, lengths in checks:
        for match in re.finditer(pattern, value, flags=re.I):
            if len(match.group(1)) not in lengths:
                warnings.append(f"некорректная длина поля {label}")
    return warnings
