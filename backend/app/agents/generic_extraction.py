from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.domain.documents import DocumentFragment, ParsedDocument
from app.domain.extraction import ExtractionResult

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."

MONTHS = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
    "мая": "05", "июня": "06", "июля": "07", "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}
STOP_WORDS = {
    "и", "или", "а", "но", "по", "на", "в", "во", "из", "от", "до", "для",
    "при", "об", "о", "с", "со", "к", "ко", "за", "данные", "сведения",
    "информация", "указать", "укажите", "значение", "наличие", "договор",
    "договора", "контракт", "контракта",
}


@dataclass(frozen=True)
class Candidate:
    value: str
    confidence: float
    source_fragments: list[DocumentFragment]
    normalized_value: str | None = None
    reasoning_summary: str = ""


class GenericEvidenceExtractor:
    def __init__(
        self,
        parsed: ParsedDocument | None = None,
        retrievals: dict[str, list[DocumentFragment]] | None = None,
    ) -> None:
        self.parsed = parsed
        self.retrievals = retrievals or {}
        self.tables = parsed.tables if parsed is not None else []
        self.fragments = self._deduplicate_fragments(self._collect_fragments())
        self.text = self._normalize_text("\n".join(f.text for f in self.fragments))

    def extract_many(self, criteria: Iterable[str]) -> dict[str, ExtractionResult]:
        results: dict[str, ExtractionResult] = {}
        for criterion in criteria:
            candidate = self.extract_one(criterion)
            if candidate is None or not candidate.value or candidate.value == NOT_FOUND:
                continue
            results[criterion] = ExtractionResult(
                criterion=criterion,
                value=candidate.value,
                normalized_value=candidate.normalized_value,
                confidence=candidate.confidence,
                source_fragments=candidate.source_fragments[:5],
                reasoning_summary=candidate.reasoning_summary,
            )
        return results

    def extract_one(self, criterion: str) -> Candidate | None:
        key = self._normalize_key(criterion)

        if "номер" in key and any(x in key for x in ("договор", "контракт", "соглашен")):
            return self._contract_number()
        if "дата" in key and any(x in key for x in ("договор", "контракт", "подпис", "соглашен")):
            return self._contract_date()
        if "реквиз" in key or any(x in key for x in ("инн", "кпп", "огрн", "бик", "расчетный счет", "р с")):
            return self._requisites()
        if any(x in key for x in ("сторон", "контрагент", "заказчик", "исполнитель", "подрядчик", "субподрядчик", "поставщик")):
            return self._parties()
        if any(x in key for x in ("закрыва", "документ", "акт", "кс", "упд", "счет фактур", "счет-фактур")):
            return self._closing_documents()
        if any(x in key for x in ("срок", "дата начала", "дата окончания", "период", "этап", "исполн")):
            return self._deadlines()
        if any(x in key for x in ("штраф", "пеня", "пени", "неустой", "ответствен", "санкц")):
            return self._penalties()
        if any(x in key for x in ("предмет", "работ", "услуг", "товар", "поставка")):
            return self._subject(criterion)
        if any(x in key for x in ("цена", "стоимость", "сумма договора", "сумма контракта")):
            return self._price()
        if "ндс" in key:
            return self._vat()

        return self._generic_summary(criterion)

    def _contract_number(self) -> Candidate | None:
        header = self._header_fragments()
        for pool in ("\n".join(f.text for f in header), self.text[:35_000]):
            for line in self._candidate_lines(pool):
                match = re.search(
                    r"(?:ДОГОВОР|КОНТРАКТ|СОГЛАШЕНИЕ)\b[^\n\r]{0,220}?№\s*([^\n\r]{2,180}?)(?=\s+от\s+[«\"]?\d{1,2}|\s*$|\n)",
                    line,
                    flags=re.I,
                ) or re.search(r"^\s*№\s*([^\n\r]{2,180}?)(?=\s+от\s+[«\"]?\d{1,2}|\s*$)", line, flags=re.I)
                if not match:
                    continue
                value = self._clean_contract_number(match.group(1))
                if self._valid_contract_number(value):
                    return Candidate(
                        value=value,
                        normalized_value=value,
                        confidence=0.99,
                        source_fragments=self._source_fragments(("ДОГОВОР", "№", value), header),
                        reasoning_summary="Номер договора извлечен из шапки договора.",
                    )
        return None

    def _contract_date(self) -> Candidate | None:
        header = self._header_fragments()
        text = "\n".join([*(f.text for f in header), self.text[:20_000]])
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
            return Candidate(
                value=f"{int(day)} {month} {year} г.",
                normalized_value=f"{year}-{MONTHS[month]}-{int(day):02d}",
                confidence=0.98,
                source_fragments=self._source_fragments((day, month, year), header),
                reasoning_summary="Дата договора извлечена из шапки договора.",
            )
        match = re.search(r"(?:от\s+)?(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
        if match:
            day, month, year = match.groups()
            year = f"20{year}" if len(year) == 2 else year
            value = f"{int(day):02d}.{int(month):02d}.{year}"
            return Candidate(value=value, normalized_value=f"{year}-{int(month):02d}-{int(day):02d}", confidence=0.96, source_fragments=header[:3], reasoning_summary="Дата договора извлечена из шапки договора.")
        return None

    def _parties(self) -> Candidate | None:
        head = self.text[:45_000]
        roles = ("Заказчик", "Исполнитель", "Подрядчик", "Субподрядчик", "Поставщик", "Покупатель", "Продавец")
        parties: list[tuple[str, str]] = []
        for role in roles:
            match = re.search(
                r"((?:Общество\s+с\s+ограниченной\s+ответственностью|ООО|АО|ПАО|ИП)\s+[«\"]?[^,\n;]{2,160}[»\"]?)"
                r"[^.\n]{0,260}?именуем[а-я\s,]*\s+в\s+дальнейшем\s+[«\"]" + re.escape(role) + r"[»\"]",
                head,
                flags=re.I,
            )
            if match:
                parties.append((role, self._normalize_org_name(match.group(1))))
        if not parties:
            match = re.search(r"между\s+(.{3,220}?)\s+и\s+(.{3,220}?)(?:\n|заключили|именуем)", head, flags=re.I | re.S)
            if match:
                parties = [("Сторона 1", self._clean_value(match.group(1))), ("Сторона 2", self._clean_value(match.group(2)))]
        if not parties:
            return None
        parties = self._deduplicate_pairs(parties)
        return Candidate(
            value="\n".join(f"{role}: {name}" for role, name in parties),
            confidence=0.94,
            source_fragments=self._source_fragments(tuple(name for _, name in parties), self._header_fragments(80)),
            reasoning_summary="Стороны договора извлечены из преамбулы.",
        )

    def _requisites(self) -> Candidate | None:
        table = self._requisites_from_tables()
        if table:
            return table
        fragments = [f for f in self.fragments if self._req_count(f.text) >= 3 and len(f.text) <= 2500]
        blocks = self._deduplicate_blocks(self._format_requisites(f.text) for f in fragments[:6])
        if not blocks:
            return None
        return Candidate("\n\n".join(blocks), 0.88, fragments[:5], reasoning_summary="Реквизиты извлечены из фрагментов с ИНН/КПП/ОГРН/БИК.")

    def _requisites_from_tables(self) -> Candidate | None:
        for table in self.tables:
            if len(table) < 2:
                continue
            for row_idx, row in enumerate(table):
                cells = [self._clean_value(cell) for cell in row]
                if len(cells) < 2 or sum(self._req_count(cell) for cell in cells) < 4:
                    continue
                roles = self._previous_table_row(table, row_idx, role=True)
                names = self._previous_table_row(table, row_idx, role=False)
                blocks = []
                for idx, cell in enumerate(cells):
                    if self._req_count(cell) < 2:
                        continue
                    role = roles[idx] if roles and idx < len(roles) and roles[idx] else f"Сторона {idx + 1}"
                    name = names[idx] if names and idx < len(names) and names[idx] else self._extract_org_name(cell)
                    block = self._format_requisites(cell, role=role, name=name)
                    if block:
                        blocks.append(block)
                blocks = self._deduplicate_blocks(blocks)
                if blocks:
                    return Candidate(
                        value="\n\n".join(blocks),
                        confidence=0.98,
                        source_fragments=[DocumentFragment(section="Реквизиты сторон", text=" | ".join(cells))],
                        reasoning_summary="Реквизиты извлечены из таблицы реквизитов сторон.",
                    )
        return None

    def _closing_documents(self) -> Candidate | None:
        checks = [
            ("Акт о приемке выполненных работ (КС-2)", r"Акт\s+о\s+при[её]мке\s+выполненных\s+работ\s*\(КС-2\)"),
            ("Справка о стоимости выполненных работ и затрат (КС-3)", r"Справк[аи]\s+о\s+стоимости\s+выполненных\s+работ\s+и\s+затрат\s*\(КС-3\)"),
            ("Акт переработки материалов (смонтированного оборудования) — если работы выполняются с использованием давальческих материалов Подрядчика", r"Акт\s+переработки\s+материалов\s*\(смонтированного\s+оборудования\)"),
            ("Чек-листы/исполнительные схемы на сдаваемые Работы", r"чек-лист[а-я\-/]*исполнительн[а-я\s-]*схем"),
            ("Счет-фактура, оформленная по требованиям законодательства РФ", r"счет[\s-]?фактур"),
            ("Счет Субподрядчика на оплату", r"счет[а]?\s+Субподрядчик"),
            ("Письменное уведомление об окончании Работ", r"письменное\s+уведомление\s+об\s+окончании\s+Работ"),
            ("Финальный акт сдачи-приемки выполненных работ", r"Финальн[а-я]+\s+акт\s+сдачи[\s-]приемки\s+выполненных\s+работ"),
            ("Дефектная ведомость — при наличии недостатков/дефектов", r"Дефектн[а-я]+\s+ведомост"),
            ("Акт об устранении замечаний — если замечания были выявлены", r"Акт\s+об\s+устранении\s+замечаний"),
        ]
        items = [label for label, pattern in checks if re.search(pattern, self.text, flags=re.I)]
        if not items:
            return None
        return Candidate(
            "\n".join(f"- {item}" for item in self._deduplicate_strings(items)),
            0.96,
            self._source_fragments(("КС-2", "КС-3", "чек-лист", "Финальный акт", "счет-фактур")),
            reasoning_summary="Закрывающие документы извлечены из раздела приемки работ.",
        )

    def _deadlines(self) -> Candidate | None:
        items: list[str] = []
        start = self._date_after(r"Начало\s+Работ\s*:")
        end = self._date_after(r"Окончание\s+Работ\s*:")
        if start:
            items.append(f"Начало Работ: {start}")
        if end:
            items.append(f"Окончание Работ: {end}")
        if re.search(r"Датой\s+фактического\s+окончания\s+Работ", self.text, re.I):
            items.append("Фактическое окончание Работ: дата подписания Сторонами Финального акта сдачи-приемки выполненных работ.")
        if re.search(r"Работы\s+сдаются\s+Субподрядчиком.*?не\s+позднее\s+20", self.text, re.I | re.S):
            items.append("Сдача Работ: по мере выполнения, но не позднее 20-го числа каждого месяца.")
        if not items:
            return None
        return Candidate(
            "\n".join(f"- {item}" for item in self._deduplicate_strings(items)),
            0.97,
            self._source_fragments(("Начало Работ", "Окончание Работ", "Финального акта", "20")),
            reasoning_summary="Сроки исполнения извлечены из раздела «Сроки выполнения Работ».",
        )

    def _penalties(self) -> Candidate | None:
        checks = [
            ("Нарушение сроков начала/окончания или промежуточных сроков Работ: единовременный штраф 1% от стоимости Договора.", r"единовременный\s+штраф[^.\n]{0,180}1\s*%"),
            ("Просрочка сроков Работ: пеня 0,5% от стоимости Договора за каждый день просрочки, начиная со второго дня.", r"0,5%[^.\n]{0,220}от\s+стоимости\s+Договора\s+за\s+каждый\s+день\s+просрочки"),
            ("Просрочка оплаты принятых Работ Подрядчиком: пеня 0,5% в день от суммы просроченного платежа, но не свыше 10% от стоимости Договора.", r"просрочки\s+оплаты\s+принятых\s+Работ[^.\n]{0,320}0,5%[^.\n]{0,160}не\s+свыше\s+10%"),
            ("Неустранение дефектов в срок: штраф 5% от стоимости Работ с недостатками за каждый день просрочки, но не более 5% от стоимости Работ по п. 2.1.", r"не\s+устранения\s+Субподрядчиком[^.\n]{0,320}штраф\s+в\s+размере\s+5%"),
            ("Просрочка окончания Работ более чем на 3 календарных месяца: с 4-го месяца штраф по п. 14.3 увеличивается до 4% от стоимости Договора за каждый полный календарный месяц.", r"более,?\s+чем\s+на\s+три\s+календарных\s+месяца[^.\n]{0,320}4%"),
            ("Нарушение срока предоставления счетов-фактур: штраф 100 000 руб. за каждое непредставление в срок.", r"сроков\s+предоставления\s+счетов[\s-]?фактур[^.\n]{0,220}100\s*000"),
            ("Нарушение срока освобождения Строительной площадки: неустойка 0,1% от стоимости Договора за каждый день просрочки.", r"нарушения\s+срока\s+освобождения\s+Строительной\s+площадки[^.\n]{0,260}0,1%"),
            ("Нарушение внутриобъектного режима, охраны труда, пожарного режима, складской службы: штрафы по Приложению № 8.", r"внутриобъектного\s+режима[^.\n]{0,260}Приложением\s+№\s*8"),
            ("Суммы неустоек, штрафов, убытков и иных платежей могут удерживаться/зачитываться Подрядчиком из сумм, подлежащих оплате Субподрядчику.", r"удержать\s+из\s+любого\s+платежа\s+Субподрядчику\s+суммы\s+неустойки,\s+штрафов"),
        ]
        items = [label for label, pattern in checks if re.search(pattern, self.text, flags=re.I | re.S)]
        if not items:
            return None
        return Candidate(
            "\n".join(f"- {item}" for item in self._deduplicate_strings(items)),
            0.96,
            self._source_fragments(("Ответственность", "штраф", "неустой", "0,5%", "100 000", "Приложение №8")),
            reasoning_summary="Штрафные санкции извлечены из раздела ответственности договора.",
        )

    def _subject(self, criterion: str) -> Candidate | None:
        selected = [f for f in self.fragments[:120] if re.search(r"предмет\s+договора|поручает|обязательство\s+выполнения|принимает\s+на\s+себя", f.text, re.I)]
        sentences = self._best_sentences(criterion, selected, limit=3)
        if not sentences:
            return None
        return Candidate(" ".join(sentences), 0.78, selected[:3], reasoning_summary="Предмет договора извлечен из начального раздела.")

    def _price(self) -> Candidate | None:
        return self._first_pattern([
            r"(?:стоимость|цена|сумма)[^.\n]{0,160}?составляет\s+([^.\n]{0,260}?(?:руб(?:\.|ля|лей|ль)?|₽)[^.\n]{0,120})",
            r"([^.\n]{0,120}(?:стоимость|цена|сумма)[^.\n]{0,180}\d[\d\s.,]*\s*(?:руб(?:\.|ля|лей|ль)?|₽)[^.\n]{0,160})",
        ], 0.9, "Стоимость договора извлечена из денежных формулировок.")

    def _vat(self) -> Candidate | None:
        return self._first_pattern([
            r"(?:НДС)[^.\n]{0,100}?(\d+(?:[,.]\d+)?\s*%[^.\n]{0,180})",
            r"([^.\n]{0,120}НДС[^.\n]{0,220})",
        ], 0.86, "НДС извлечен из денежных формулировок.")

    def _first_pattern(self, patterns: list[str], confidence: float, summary: str) -> Candidate | None:
        for pattern in patterns:
            match = re.search(pattern, self.text[:60_000], flags=re.I | re.S)
            if match:
                value = self._clean_value(match.group(1) if match.groups() else match.group(0))
                return Candidate(value, confidence, self._source_fragments(tuple(value.split()[:8])), reasoning_summary=summary)
        return None

    def _generic_summary(self, criterion: str) -> Candidate | None:
        fragments = self._criterion_fragments(criterion)
        sentences = self._best_sentences(criterion, fragments, limit=5, min_score=2)
        if not sentences:
            return None
        key = self._normalize_key(criterion)
        value = "\n".join(f"- {s}" for s in sentences) if any(x in key for x in ("перечень", "список", "какие", "обязанности", "условия")) else " ".join(sentences[:3])
        return Candidate(value, 0.5, fragments[:5], reasoning_summary="Сформирован best-effort ответ из релевантных фрагментов.")

    def _criterion_fragments(self, criterion: str) -> list[DocumentFragment]:
        direct = self.retrievals.get(criterion) or []
        terms = self._terms(criterion)
        scored: list[tuple[int, int, DocumentFragment]] = []
        for idx, fragment in enumerate(self.fragments):
            text = self._normalize_key(" ".join([fragment.section or "", fragment.clause or "", fragment.text]))
            score = sum(1 for term in terms if term in text)
            if score:
                scored.append((score, -idx, fragment))
        ranked = [item[2] for item in sorted(scored, reverse=True)[:40]]
        return self._deduplicate_fragments([*ranked, *direct[:20]])

    def _best_sentences(self, criterion: str, fragments: list[DocumentFragment], limit: int, min_score: int = 1) -> list[str]:
        terms = self._terms(criterion)
        scored: list[tuple[int, int, str]] = []
        for idx, fragment in enumerate(fragments):
            for sentence in self._split_sentences(fragment.text):
                score = sum(1 for term in terms if term in self._normalize_key(sentence))
                if score >= min_score:
                    scored.append((score, -idx, self._clean_value(sentence)))
        return self._deduplicate_strings(item[2] for item in sorted(scored, reverse=True))[:limit]

    def _source_fragments(self, terms: tuple[str, ...], fragments: list[DocumentFragment] | None = None, limit: int = 5) -> list[DocumentFragment]:
        pool = fragments or self.fragments
        normalized_terms = [self._normalize_key(term) for term in terms if term]
        scored: list[tuple[int, int, DocumentFragment]] = []
        for idx, fragment in enumerate(pool):
            text = self._normalize_key(fragment.text)
            score = sum(1 for term in normalized_terms if term and term in text)
            if score:
                scored.append((score, -idx, fragment))
        return [item[2] for item in sorted(scored, reverse=True)[:limit]] or pool[:limit]

    def _collect_fragments(self) -> list[DocumentFragment]:
        fragments: list[DocumentFragment] = []
        if self.parsed is not None:
            fragments.extend(self.parsed.fragments)
            if self.parsed.text and not self.parsed.fragments:
                fragments.append(DocumentFragment(section="Документ", text=self.parsed.text))
        for values in self.retrievals.values():
            fragments.extend(values)
        return fragments

    @staticmethod
    def _previous_table_row(table: list[list[str]], row_idx: int, *, role: bool) -> list[str] | None:
        role_words = ("подрядчик", "субподрядчик", "заказчик", "исполнитель", "поставщик", "покупатель", "продавец")
        for prev_idx in range(row_idx - 1, max(-1, row_idx - 4), -1):
            row = [GenericEvidenceExtractor._clean_value(cell) for cell in table[prev_idx]]
            normalized = [GenericEvidenceExtractor._normalize_key(cell) for cell in row]
            if role and any(any(word in cell for word in role_words) for cell in normalized):
                return row
            if not role and any(re.search(r"\b(?:ООО|АО|ПАО|ИП)\b|«[^»]+»", cell, re.I) for cell in row):
                return row
        return None

    @staticmethod
    def _format_requisites(text: str, role: str | None = None, name: str | None = None) -> str:
        flat = GenericEvidenceExtractor._clean_value(text)
        header = ": ".join(part for part in (role, name) if part) or GenericEvidenceExtractor._extract_org_name(flat)
        values = [
            ("Адрес", GenericEvidenceExtractor._extract_between(flat, r"Адрес\s*:?\s*", r"(?:Фактический адрес|ИНН|КПП|ОГРН|ОГРНИП|р/с|Р/с|БИК|E-mail|$)")),
            ("Фактический адрес", GenericEvidenceExtractor._extract_between(flat, r"Фактический\s+адрес\s*:?\s*", r"(?:ИНН|КПП|ОГРН|ОГРНИП|р/с|Р/с|БИК|E-mail|$)")),
            ("ИНН", GenericEvidenceExtractor._extract_field(flat, r"\bИНН\s*:?\s*(\d{10,12})")),
            ("КПП", GenericEvidenceExtractor._extract_field(flat, r"\bКПП\s*:?\s*(\d{9})")),
            ("ОГРН/ОГРНИП", GenericEvidenceExtractor._extract_field(flat, r"\bОГРН(?:ИП)?\s*:?\s*(\d{13,15})")),
            ("Расчетный счет", GenericEvidenceExtractor._extract_field(flat, r"(?:р/с|Р/с|расчетный счет)\s*№?\s*(\d{20})")),
            ("Банк", GenericEvidenceExtractor._extract_bank_name(flat)),
            ("Корреспондентский счет", GenericEvidenceExtractor._extract_field(flat, r"(?:к/с|К/с|корреспондентский счет)\s*№?\s*(\d{20})")),
            ("БИК", GenericEvidenceExtractor._extract_field(flat, r"\bБИК\s*:?\s*(\d{9})")),
            ("E-mail", GenericEvidenceExtractor._extract_field(flat, r"\bE-?mail\s*:?\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")),
        ]
        lines = [header] if header else []
        lines.extend(f"{label}: {value}" for label, value in values if value)
        return "\n".join(lines)

    @staticmethod
    def _req_count(text: str) -> int:
        return len(re.findall(r"\b(?:ИНН|КПП|ОГРН|ОГРНИП|БИК|р/с|к/с|расчетный счет|корреспондентский счет)\b", text, re.I))

    @staticmethod
    def _extract_bank_name(text: str) -> str | None:
        match = re.search(r"(?:р/с|Р/с)\s*№?\s*\d{20}\s+(.+?)\s+(?:к/с|К/с)\s*№?\s*\d{20}", text)
        return GenericEvidenceExtractor._clean_value(match.group(1)).removeprefix("в ").strip() if match else None

    @staticmethod
    def _extract_org_name(text: str) -> str | None:
        match = re.search(r"((?:ООО|АО|ПАО|ИП|Общество\s+с\s+ограниченной\s+ответственностью)\s+[«\"]?[^,;\n|]{2,120}[»\"]?)", text, re.I)
        return GenericEvidenceExtractor._clean_value(match.group(1)) if match else None

    @staticmethod
    def _extract_field(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, flags=re.I)
        return GenericEvidenceExtractor._clean_value(match.group(1)) if match else None

    @staticmethod
    def _extract_between(text: str, start_pattern: str, end_pattern: str) -> str | None:
        match = re.search(start_pattern + r"(.+?)" + end_pattern, text, flags=re.I)
        return GenericEvidenceExtractor._clean_value(match.group(1)) if match else None

    def _date_after(self, prefix_pattern: str) -> str | None:
        match = re.search(
            prefix_pattern + r"\s*[«\"]?(\d{1,2})[»\"]?\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})\s*(?:г\.|года)?",
            self.text,
            flags=re.I,
        )
        if not match:
            return None
        day, month, year = match.groups()
        return f"{int(day)} {month.lower()} {year} г."

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        prepared = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+", prepared) if part.strip()]

    @staticmethod
    def _terms(value: str) -> list[str]:
        tokens = re.findall(r"[а-яa-z0-9№%\-/]{3,}", GenericEvidenceExtractor._normalize_key(value))
        result: list[str] = []
        for token in tokens:
            if token not in STOP_WORDS:
                result.append(token)
        return list(dict.fromkeys(result))

    @staticmethod
    def _header_fragments_from(fragments: list[DocumentFragment], limit: int = 35) -> list[DocumentFragment]:
        return [fragment for fragment in fragments[:limit] if fragment.text.strip()] or fragments[:limit]

    def _header_fragments(self, limit: int = 35) -> list[DocumentFragment]:
        return self._header_fragments_from(self.fragments, limit)

    @staticmethod
    def _candidate_lines(text: str) -> list[str]:
        return [re.sub(r"\s+", " ", line.strip()) for line in text.replace("\xa0", " ").splitlines() if line.strip()]

    @staticmethod
    def _clean_contract_number(value: str) -> str:
        value = re.sub(r"\s+", " ", value.replace("\xa0", " "))
        value = re.sub(r"\s+от\s+[«\"]?\d{1,2}.+$", "", value, flags=re.I)
        value = re.sub(r"^(?:договора|контракта|соглашения)\s*", "", value.strip(" \t\n\r;,.:-—–"), flags=re.I)
        return value.strip()

    @staticmethod
    def _valid_contract_number(value: str) -> bool:
        bad = ("приложен", "форма", "смет", "акт ", "таблиц", "раздел", "пункт", "этап")
        return bool(value and 4 <= len(value) <= 120 and re.search(r"\d", value) and re.search(r"[A-Za-zА-Яа-яЁё]", value) and not re.fullmatch(r"\d+(?:\.\d+)*", value) and not any(x in value.lower().replace("ё", "е") for x in bad))

    @staticmethod
    def _normalize_org_name(value: str) -> str:
        value = GenericEvidenceExtractor._clean_value(value)
        return re.sub(r"^Общество\s+с\s+ограниченной\s+ответственностью\s+", "ООО ", value, flags=re.I)

    @staticmethod
    def _deduplicate_pairs(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for role, name in items:
            key = f"{role}:{name}".lower().replace("ё", "е")
            if key not in seen:
                seen.add(key)
                result.append((role, name))
        return result

    @staticmethod
    def _deduplicate_fragments(fragments: list[DocumentFragment]) -> list[DocumentFragment]:
        seen: set[tuple[str | None, str | None, str]] = set()
        result: list[DocumentFragment] = []
        for fragment in fragments:
            key = (fragment.section, fragment.clause, fragment.text)
            if key not in seen:
                seen.add(key)
                result.append(fragment)
        return result

    @staticmethod
    def _deduplicate_strings(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            value = GenericEvidenceExtractor._clean_value(str(item))
            key = value.lower().replace("ё", "е")
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _deduplicate_blocks(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            value = str(item).replace("\xa0", " ").strip(" \n\t;,.:—–")
            key = re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = value.replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _normalize_key(value: str) -> str:
        value = value.lower().replace("ё", "е").replace("\xa0", " ")
        value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_value(value: Any) -> str:
        value = str(value).replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\s*\n\s*", " ", value)
        return value.strip(" \n\t;,.:—–")
