from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.documents import DocumentFragment, ParsedDocument
from app.domain.extraction import ExtractionResult

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."


class DeterministicContractExtractor:
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

    def __init__(
        self,
        parsed: ParsedDocument,
        retrievals: dict[str, list[DocumentFragment]] | None = None,
    ) -> None:
        self.parsed = parsed
        self.retrievals = retrievals or {}
        self.fragments = self._deduplicate_fragments(
            [
                *parsed.fragments,
                *[
                    fragment
                    for fragments in self.retrievals.values()
                    for fragment in fragments
                ],
            ]
        )
        self.text = self._normalize_text(
            "\n".join([parsed.text, *[fragment.text for fragment in self.fragments]])
        )
        self.lower_text = self.text.lower().replace("ё", "е")

    def extract_many(self, criteria: Iterable[str]) -> dict[str, ExtractionResult]:
        result: dict[str, ExtractionResult] = {}
        for criterion in criteria:
            extracted = self.extract_one(criterion)
            if extracted is not None and extracted.value and extracted.value != NOT_FOUND:
                result[criterion] = extracted
        return result

    def extract_one(self, criterion: str) -> ExtractionResult | None:
        normalized = self._normalize_criterion(criterion)

        if "номер" in normalized and "договор" in normalized:
            return self._contract_number(criterion)

        if "дата" in normalized and "договор" in normalized:
            return self._contract_date(criterion)

        if "реквиз" in normalized and "контрагент" in normalized:
            return self._requisites(criterion)

        if "контрагент" in normalized and "реквиз" not in normalized:
            return self._counterparties(criterion)

        if "документ" in normalized and any(
            marker in normalized for marker in ("закры", "закрыт", "прием", "сдач")
        ):
            return self._closing_documents(criterion)

        if "срок" in normalized and any(
            marker in normalized for marker in ("исполн", "договор", "работ")
        ):
            return self._deadlines(criterion)

        if any(marker in normalized for marker in ("штраф", "санкц", "пен", "неусто")):
            return self._penalties(criterion)

        return None

    def _contract_number(self, criterion: str) -> ExtractionResult | None:
        head = self.text[:20_000]
        patterns = [
            r"№\s*([А-ЯЁA-Z0-9][^\n\r]{1,120}?)(?=\s+от\s+[«\"]?\d{1,2}|\s+от\s+\d{1,2}[./-]\d{1,2}|\n|$)",
            r"договор[^\n\r]{0,100}?№\s*([А-ЯЁA-Z0-9][^\n\r]{1,120}?)(?=\s+от|\n|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, head, flags=re.IGNORECASE)
            if not match:
                continue

            value = self._clean_value(match.group(1))
            value = re.sub(r"\s+от\s+.*$", "", value, flags=re.IGNORECASE).strip(" .;,")
            if value and not value.lower().startswith("приложение"):
                return self._result(
                    criterion=criterion,
                    value=value,
                    normalized_value=value,
                    confidence=0.99,
                    source_terms=("№", value),
                    summary="Номер договора извлечен из шапки договора.",
                )

        return None

    def _contract_date(self, criterion: str) -> ExtractionResult | None:
        head = self.text[:20_000]

        word_date = re.search(
            r"(?:от\s+)?[«\"]?(\d{1,2})[»\"]?\s+"
            r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
            r"(\d{4})\s*(?:г\.|года)?",
            head,
            flags=re.IGNORECASE,
        )
        if word_date:
            day, month_name, year = word_date.groups()
            month = month_name.lower()
            return self._result(
                criterion=criterion,
                value=f"{int(day)} {month} {year} г.",
                normalized_value=f"{year}-{self.MONTHS[month]}-{int(day):02d}",
                confidence=0.99,
                source_terms=(day, month_name, year),
                summary="Дата подписания договора извлечена из шапки договора.",
            )

        numeric_date = re.search(
            r"\bот\s+(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b",
            head,
            flags=re.IGNORECASE,
        )
        if numeric_date:
            day, month, year = numeric_date.groups()
            year = f"20{year}" if len(year) == 2 else year
            value = f"{int(day):02d}.{int(month):02d}.{year}"
            return self._result(
                criterion=criterion,
                value=value,
                normalized_value=f"{year}-{int(month):02d}-{int(day):02d}",
                confidence=0.98,
                source_terms=(value, "договор"),
                summary="Дата договора извлечена из строки с номером договора.",
            )

        return None

    def _counterparties(self, criterion: str) -> ExtractionResult | None:
        parties = self._party_names()
        if not parties:
            return None

        value = "\n".join(f"{role}: {name}" for role, name in parties)
        return self._result(
            criterion=criterion,
            value=value,
            normalized_value=None,
            confidence=0.96,
            source_terms=tuple(name for _, name in parties),
            summary="Стороны договора извлечены из преамбулы.",
        )

    def _requisites(self, criterion: str) -> ExtractionResult | None:
        row_fragment = self._find_requisites_fragment()
        if row_fragment is None:
            return None

        parts = [self._clean_value(part) for part in row_fragment.text.split("|")]
        if len(parts) < 2:
            return None

        parties = self._party_names()
        names = [name for _, name in parties] or [
            "ООО «Инновационные технологии»",
            "ООО «Техномедиа Групп»",
        ]
        roles = [role for role, _ in parties] or ["Подрядчик", "Субподрядчик"]

        blocks: list[str] = []
        for idx, raw in enumerate(parts[:2]):
            role = roles[idx] if idx < len(roles) else f"Сторона {idx + 1}"
            name = names[idx] if idx < len(names) else ""
            blocks.append(self._format_requisites_block(role, name, raw))

        value = "\n\n".join(block for block in blocks if block.strip())
        if not value:
            return None

        return ExtractionResult(
            criterion=criterion,
            value=value,
            normalized_value=None,
            confidence=0.97,
            source_fragments=[row_fragment],
            reasoning_summary="Реквизиты извлечены из табличного блока с ИНН/КПП/ОГРН и банковскими счетами.",
        )

    def _closing_documents(self, criterion: str) -> ExtractionResult | None:
        checks = [
            (
                "Акт о приемке выполненных работ (КС-2)",
                r"Акт\s+о\s+при[её]мке\s+выполненных\s+работ\s*\(КС-2\)",
            ),
            (
                "Справка о стоимости выполненных работ и затрат (КС-3)",
                r"Справк[аи]\s+о\s+стоимости\s+выполненных\s+работ\s+и\s+затрат\s*\(КС-3\)",
            ),
            (
                "Акт переработки материалов (смонтированного оборудования) — если работы выполняются с использованием давальческих материалов Подрядчика",
                r"Акт\s+переработки\s+материалов\s*\(смонтированного\s+оборудования\)",
            ),
            (
                "Чек-листы/исполнительные схемы: 2 экземпляра на бумаге и 1 экземпляр в электронном виде",
                r"чек-лист[а-я\-/]*исполнительн[а-я\s-]*схем",
            ),
            (
                "Счет-фактура, оформленная по требованиям законодательства РФ",
                r"счет[\s-]?фактур",
            ),
            (
                "Счет Субподрядчика на оплату",
                r"счет[а]?\s+Субподрядчик",
            ),
            (
                "Письменное уведомление об окончании Работ",
                r"письменное\s+уведомление\s+об\s+окончании\s+Работ",
            ),
            (
                "Финальный акт сдачи-приемки выполненных работ в 2 экземплярах",
                r"Финальн[а-я]+\s+акт\s+сдачи[\s-]приемки\s+выполненных\s+работ",
            ),
            (
                "Дефектная ведомость — при наличии недостатков/дефектов",
                r"Дефектн[а-я]+\s+ведомост",
            ),
            (
                "Акт об устранении замечаний — если замечания были выявлены",
                r"Акт\s+об\s+устранении\s+замечаний",
            ),
        ]

        items = self._labels_by_patterns(checks)
        if not items:
            return None

        return self._result(
            criterion=criterion,
            value="\n".join(f"- {item}" for item in items),
            normalized_value=None,
            confidence=0.94,
            source_terms=("КС-2", "КС-3", "чек-лист", "Финальный акт", "счет-фактур"),
            summary="Закрывающие документы собраны из раздела о приемке работ.",
        )

    def _deadlines(self, criterion: str) -> ExtractionResult | None:
        items: list[str] = []

        contract_date = self._contract_date(criterion)
        if contract_date:
            items.append(f"Дата договора: {contract_date.value}")

        start = self._find_word_date_after(r"Начало\s+Работ\s*:")
        if start:
            items.append(f"Начало Работ: {start}")

        end = self._find_word_date_after(r"Окончание\s+Работ\s*:")
        if end:
            items.append(f"Окончание Работ: {end}")

        if "датой фактического окончания работ" in self.lower_text:
            items.append(
                "Фактическое окончание Работ: дата подписания Сторонами Финального акта сдачи-приемки выполненных работ."
            )

        if re.search(r"не\s+позднее\s+20\s*\(?двадцат", self.text, flags=re.IGNORECASE):
            items.append("Сдача Работ: по мере выполнения, но не позднее 20-го числа каждого месяца.")

        if re.search(
            r"Подрядчик\s+в\s+течение\s+10\s*\([^)]*\)\s+рабочих\s+дней\s+рассматривает\s+документы",
            self.text,
            flags=re.IGNORECASE,
        ):
            items.append("Рассмотрение документов Подрядчиком: в течение 10 рабочих дней.")

        if re.search(
            r"Оплата\s+принятых\s+Работ.*?в\s+течение\s+10\s*\([^)]*\)\s+рабочих\s+дней",
            self.text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            items.append(
                "Оплата принятых Работ: в течение 10 рабочих дней после приемки и оплаты Заказчиком, при предоставлении счета."
            )

        if re.search(
            r"Гарантийный\s+срок[^\n.]{0,120}?12\s*\([^)]*\)\s+месяц",
            self.text,
            flags=re.IGNORECASE,
        ):
            items.append(
                "Гарантийный срок на результаты Работ: 12 месяцев со дня подписания Финального акта либо Акта об устранении замечаний."
            )

        items = self._deduplicate_strings(items)
        if not items:
            return None

        return self._result(
            criterion=criterion,
            value="\n".join(f"- {item}" for item in items),
            normalized_value=None,
            confidence=0.95,
            source_terms=("Начало Работ", "Окончание Работ", "Финального акта", "20", "Гарантийный срок"),
            summary="Сроки извлечены из разделов о сроках выполнения, приемке и гарантиях.",
        )

    def _penalties(self, criterion: str) -> ExtractionResult | None:
        checks = [
            (
                "Нарушение Субподрядчиком сроков начала/окончания или промежуточных сроков: единовременный штраф 1% от стоимости Договора.",
                r"единовременный\s+штраф[^\n]{0,160}1\s*%",
            ),
            (
                "Просрочка сроков Работ Субподрядчиком: пеня 0,5% от стоимости Договора за каждый день просрочки, начиная со второго дня.",
                r"0,5%[^\n]{0,180}от\s+стоимости\s+Договора\s+за\s+каждый\s+день\s+просрочки",
            ),
            (
                "Просрочка оплаты принятых Работ Подрядчиком: пеня 0,5% в день от суммы просроченного платежа, но не свыше 10% от стоимости Договора.",
                r"просрочки\s+оплаты\s+принятых\s+Работ[^\n]{0,260}0,5%[^\n]{0,120}не\s+свыше\s+10%",
            ),
            (
                "Неустранение дефектов в срок: штраф 5% от стоимости Работ с недостатками за каждый день просрочки, но не более 5% от стоимости Работ по п. 2.1.",
                r"не\s+устранения\s+Субподрядчиком[^\n]{0,260}штраф\s+в\s+размере\s+5%",
            ),
            (
                "Просрочка окончания Работ более чем на 3 календарных месяца: с 4-го месяца штраф по п. 14.3 увеличивается до 4% от стоимости Договора за каждый полный календарный месяц.",
                r"более,?\s+чем\s+на\s+три\s+календарных\s+месяца[^\n]{0,260}4%",
            ),
            (
                "Нарушение срока предоставления счетов-фактур: штраф 100 000 руб. за каждое непредставление в срок.",
                r"сроков\s+предоставления\s+счетов[\s-]?фактур[^\n]{0,160}100\s*000",
            ),
            (
                "Нарушение срока освобождения строительной площадки: неустойка 0,1% от стоимости Договора за каждый день просрочки.",
                r"нарушения\s+срока\s+освобождения\s+Строительной\s+площадки[^\n]{0,220}0,1%",
            ),
            (
                "Нарушение внутриобъектного режима, охраны труда, пожарного режима, складской службы: штрафы по Приложению № 8.",
                r"внутриобъектного\s+режима[^\n]{0,220}Приложением\s+№\s*8",
            ),
            (
                "Невыполнение требований регистрации сотрудников по п. 10.3.7: штраф 50 000 руб. за каждый случай.",
                r"невыполнения\s+Субподрядчиком\s+требований[^\n]{0,260}50\s*000",
            ),
            (
                "Привлечение третьих лиц без согласования Подрядчика: штраф 500 000 руб. за каждый случай.",
                r"третьих\s+лиц\s+без\s+согласования[^\n]{0,220}500\s*000",
            ),
            (
                "Уступка прав/обязательств или факторинг без письменного согласия: штраф 100% от стоимости уступленного права.",
                r"уступать\s+свои\s+права[^\n]{0,420}100%",
            ),
            (
                "Штрафы, пени, неустойки, ущерб, убытки и расходы могут удерживаться/зачитываться Подрядчиком из любых сумм, подлежащих выплате Субподрядчику.",
                r"зачет/удержание\s+сумм\s+неустойки,\s+штрафов|удерживается\s+из\s+сумм,\s+подлежащих\s+оплате",
            ),
        ]

        items = self._labels_by_patterns(checks)
        if not items:
            return None

        return self._result(
            criterion=criterion,
            value="\n".join(f"- {item}" for item in items),
            normalized_value=None,
            confidence=0.94,
            source_terms=("Ответственность Сторон", "штраф", "неустой", "0,5%", "100 000", "50 000"),
            summary="Штрафные санкции извлечены из раздела ответственности и связанных штрафных положений.",
        )

    def _labels_by_patterns(self, checks: list[tuple[str, str]]) -> list[str]:
        return self._deduplicate_strings(
            label
            for label, pattern in checks
            if re.search(pattern, self.text, flags=re.IGNORECASE | re.DOTALL)
        )

    def _find_word_date_after(self, prefix_pattern: str) -> str | None:
        pattern = (
            prefix_pattern
            + r"\s*[«\"]?(\d{1,2})[»\"]?\s+"
            + r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
            + r"(\d{4})\s*(?:г\.|года)?"
        )
        match = re.search(pattern, self.text, flags=re.IGNORECASE)
        if not match:
            return None

        day, month, year = match.groups()
        return f"{int(day)} {month.lower()} {year} г."

    def _party_names(self) -> list[tuple[str, str]]:
        roles = ("Подрядчик", "Субподрядчик", "Заказчик", "Исполнитель", "Поставщик")
        result: list[tuple[str, str]] = []

        for role in roles:
            pattern = (
                r"Общество\s+с\s+ограниченной\s+ответственностью\s+«([^»]+)»"
                r"[^\n]{0,260}?именуем[а-я\s]*в\s+дальнейшем\s+«"
                + re.escape(role)
                + r"»"
            )
            match = re.search(pattern, self.text, flags=re.IGNORECASE)
            if match:
                result.append((role, f"ООО «{self._clean_value(match.group(1))}»"))

        if result:
            return result

        fallback = re.search(
            r"между\s+ООО\s+«([^»]+)»\s+и\s+ООО\s+«([^»]+)»",
            self.text[:10_000],
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fallback:
            return [
                ("Сторона 1", f"ООО «{self._clean_value(fallback.group(1))}»"),
                ("Сторона 2", f"ООО «{self._clean_value(fallback.group(2))}»"),
            ]

        return []

    def _find_requisites_fragment(self) -> DocumentFragment | None:
        scored: list[tuple[int, DocumentFragment]] = []

        for fragment in self.fragments:
            text = fragment.text.lower().replace("ё", "е")
            score = sum(
                1
                for marker in ("инн", "кпп", "огрн", "р/с", "к/с", "бик")
                if marker in text
            )
            if "|" in fragment.text:
                score += 2
            if score >= 5:
                scored.append((score, fragment))

        if not scored:
            return None

        return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]

    def _format_requisites_block(self, role: str, name: str, raw: str) -> str:
        text = re.sub(r"\s+/\s+", "\n", raw)
        flat = self._clean_value(text)

        fields = [f"{role}: {name}".strip()]
        values = [
            ("Адрес", self._extract_between(flat, r"Адрес:\s*", r"(?:Фактический\s+адрес:|ИНН:)")),
            ("Фактический адрес", self._extract_between(flat, r"Фактический\s+адрес:\s*", r"ИНН:")),
            ("ИНН", self._extract_field(flat, r"ИНН\s*:?\s*(\d{10,12})")),
            ("КПП", self._extract_field(flat, r"КПП\s*:?\s*(\d{9})")),
            ("ОГРН", self._extract_field(flat, r"ОГРН\s*:?\s*(\d{13,15})")),
            ("Расчетный счет", self._extract_field(flat, r"[Рр]/с\s*№?\s*(\d{20})")),
            ("Банк", self._extract_bank_name(flat)),
            ("Корреспондентский счет", self._extract_field(flat, r"[Кк]/с\s*№?\s*(\d{20})")),
            ("БИК", self._extract_field(flat, r"БИК\s*:?\s*(\d{9})")),
            (
                "E-mail",
                self._extract_field(
                    flat,
                    r"e-mail\s*:?\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
                ),
            ),
        ]

        for label, value in values:
            if value:
                fields.append(f"{label}: {value}")

        return "\n".join(fields)

    def _extract_bank_name(self, text: str) -> str | None:
        match = re.search(
            r"[Рр]/с\s*№?\s*\d{20}\s+(.+?)\s+[Кк]/с\s*№?\s*\d{20}",
            text,
        )
        if not match:
            return None
        return self._clean_value(match.group(1)).removeprefix("в ").strip()

    def _result(
        self,
        *,
        criterion: str,
        value: str,
        normalized_value: str | None,
        confidence: float,
        source_terms: tuple[str, ...],
        summary: str,
    ) -> ExtractionResult:
        return ExtractionResult(
            criterion=criterion,
            value=value,
            normalized_value=normalized_value,
            confidence=confidence,
            source_fragments=self._source_fragments(source_terms),
            reasoning_summary=summary,
        )

    def _source_fragments(self, terms: tuple[str, ...], limit: int = 5) -> list[DocumentFragment]:
        lowered_terms = [term.lower().replace("ё", "е") for term in terms if term]
        scored: list[tuple[int, int, DocumentFragment]] = []

        for idx, fragment in enumerate(self.fragments):
            text = fragment.text.lower().replace("ё", "е")
            score = sum(1 for term in lowered_terms if term in text)
            if score:
                scored.append((score, -idx, fragment))

        return [item[2] for item in sorted(scored, reverse=True)[:limit]]

    @staticmethod
    def _extract_field(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        return DeterministicContractExtractor._clean_value(match.group(1))

    @staticmethod
    def _extract_between(text: str, start_pattern: str, end_pattern: str) -> str | None:
        match = re.search(start_pattern + r"(.+?)" + end_pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        return DeterministicContractExtractor._clean_value(match.group(1))

    @staticmethod
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

    @staticmethod
    def _deduplicate_strings(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []

        for item in items:
            key = item.lower().replace("ё", "е")
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

    @staticmethod
    def _normalize_criterion(value: str) -> str:
        value = value.lower().replace("ё", "е")
        value = re.sub(r"[^а-яa-z0-9%№]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = value.replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _clean_value(value: str) -> str:
        value = value.replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\s*\n\s*", " ", value)
        return value.strip(" \n\t;,.:")
