import asyncio
import json
import re
from dataclasses import dataclass

from app.agents.base import BaseAgent
from app.domain.documents import DocumentFragment
from app.domain.extraction import ExtractionResult
from app.infrastructure.llm.openai_client import chat_json

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."


@dataclass(frozen=True)
class Candidate:
    value: str
    confidence: float
    fragments: list[DocumentFragment]
    summary: str


class ExtractionAgent(BaseAgent):
    name = "ExtractionAgent"

    DATE_RE = re.compile(
        r"(?:[«\"]?\d{1,2}[»\"]?\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\s*г?\.?|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        re.IGNORECASE,
    )
    MONEY_OR_PERCENT_RE = re.compile(r"(?:\d+[\s\d]*(?:[,.]\d+)?\s*(?:%|процент|руб|₽)|1/\d+)" , re.IGNORECASE)
    REQUISITES_RE = re.compile(r"\b(?:ИНН|КПП|ОГРН|ОГРНИП|БИК|р/?с|к/?с|расчетн\w*\s+счет|корр\w*\s+счет)\b", re.IGNORECASE)

    def _context(self, fragments: list[DocumentFragment]) -> str:
        return "\n".join(f.text.strip() for f in fragments if f.text.strip())

    def _source(self, fragments: list[DocumentFragment], value: str) -> list[DocumentFragment]:
        if not value or value == NOT_FOUND:
            return fragments[:3]
        low = value.lower()[:80]
        selected = [f for f in fragments if low and low in f.text.lower()]
        if selected:
            return selected[:3]
        value_words = {w for w in re.findall(r"[а-яА-Яa-zA-Z0-9-]{4,}", value.lower())}
        ranked = sorted(
            fragments,
            key=lambda f: len(value_words & set(re.findall(r"[а-яА-Яa-zA-Z0-9-]{4,}", f.text.lower()))),
            reverse=True,
        )
        return ranked[:3]

    def _sentences(self, text: str) -> list[str]:
        prepared = re.sub(r"\s+", " ", text)
        return [s.strip(" ;") for s in re.split(r"(?<=[.!?])\s+|\n+", prepared) if s.strip()]

    def _extract_contract_number(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        patterns = [
            r"(?:договор|контракт)\s*(?:[\w\s\-()]+)?\s*№\s*([^\n,;]+?)(?=\s+(?:от|г\.|между|заключ)|$)",
            r"№\s*([А-ЯA-Z0-9][А-ЯA-Zа-яa-z0-9./_()\-\s]{2,80}?)(?=\s+(?:от|г\.|между|заключ)|[\n,;]|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                continue
            value = re.sub(r"\s+", " ", m.group(1)).strip(" .;,:№")
            # Reject list item markers such as "2)" that caused corrupted tables before.
            if re.fullmatch(r"\d+[).]", value) or len(value) < 2:
                continue
            return Candidate(value=value, confidence=0.92, fragments=self._source(fragments, value), summary="Номер найден рядом с заголовком договора или символом №.")
        return None

    def _extract_sign_date(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        header = text[:2500]
        m = re.search(r"(?:договор|контракт)[^\n]{0,200}?\sот\s*(" + self.DATE_RE.pattern + r")", header, re.IGNORECASE)
        if not m:
            m = re.search(r"(?:г\.|город)\s*[А-ЯA-Zа-яa-z\- ]{2,80}\s+(" + self.DATE_RE.pattern + r")", header, re.IGNORECASE)
        if not m:
            m = self.DATE_RE.search(header)
        if m:
            value = m.group(1) if m.lastindex else m.group(0)
            return Candidate(value=value.strip(), confidence=0.9, fragments=self._source(fragments, value), summary="Дата найдена в шапке договора.")
        return None

    def _extract_counterparty(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        org_re = r"(?:ООО|ОАО|АО|ПАО|ЗАО|ИП)\s*(?:[«\"][^»\"]+[»\"]|[А-ЯA-Zа-яa-z0-9 .\-]{3,80})"
        role_patterns = [
            rf"({org_re})[^\n]{{0,220}}именуем\w*\s+в\s+дальнейшем\s+[«\"]?(?:Исполнитель|Подрядчик|Поставщик|Контрагент)[»\"]?",
            rf"[«\"]?(?:Исполнитель|Подрядчик|Поставщик|Контрагент)[»\"]?[^\n]{{0,120}}({org_re})",
        ]
        for pattern in role_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                value = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;")
                return Candidate(value=value, confidence=0.86, fragments=self._source(fragments, value), summary="Контрагент определен по роли в преамбуле договора.")
        orgs = [re.sub(r"\s+", " ", m.group(0)).strip(" ,.;") for m in re.finditer(org_re, text[:6000], re.IGNORECASE)]
        unique = list(dict.fromkeys(orgs))
        if unique:
            value = unique[1] if len(unique) > 1 else unique[0]
            return Candidate(value=value, confidence=0.68, fragments=self._source(fragments, value), summary="Контрагент выбран из организаций, найденных в преамбуле.")
        return None

    def _extract_requisites(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        matched: list[str] = []
        start = next((i for i, line in enumerate(lines) if "реквиз" in line.lower()), -1)
        scope = lines[start : start + 60] if start >= 0 else lines
        for line in scope:
            if self.REQUISITES_RE.search(line) or re.search(r"\b\d{9,20}\b", line):
                matched.append(re.sub(r"\s+", " ", line))
        matched = list(dict.fromkeys(matched))[:14]
        if matched:
            value = "\n".join(matched)
            return Candidate(value=value, confidence=0.82, fragments=self._source(fragments, value), summary="Реквизиты собраны из строк с ИНН/КПП/ОГРН/БИК/счетами.")
        return None

    def _extract_closing_documents(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        doc_terms = ["КС-2", "КС-3", "акт", "счет-фактура", "счёт-фактура", "УПД", "накладная", "счет на оплату", "счёт на оплату"]
        sentences = [s for s in self._sentences(text) if any(term.lower() in s.lower() for term in doc_terms)]
        items: list[str] = []
        for s in sentences[:8]:
            found = [term for term in doc_terms if term.lower() in s.lower()]
            if found:
                label = ", ".join(dict.fromkeys(found))
                items.append(f"{label} — {s}")
        items = list(dict.fromkeys(items))[:8]
        if items:
            return Candidate("\n".join(items), 0.78, self._source(fragments, " ".join(items)), "Закрывающие документы извлечены из условий приемки/оплаты.")
        return None

    def _extract_terms(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        sentences = [
            s for s in self._sentences(text)
            if re.search(r"\bсрок|в течение|до\s+\d|календарн|рабочих?\s+дн|этап", s, re.IGNORECASE)
        ]
        if sentences:
            value = "\n".join(dict.fromkeys(sentences[:6]))
            return Candidate(value, 0.78, self._source(fragments, value), "Сроки найдены по словам-маркерам сроков и календарных/рабочих дней.")
        return None

    def _extract_penalties(self, text: str, fragments: list[DocumentFragment]) -> Candidate | None:
        sentences = [
            s for s in self._sentences(text)
            if re.search(r"штраф|пен[яи]|неустой|ответствен", s, re.IGNORECASE)
        ]
        valuable = [s for s in sentences if self.MONEY_OR_PERCENT_RE.search(s)] or sentences
        if valuable:
            value = "\n".join(dict.fromkeys(valuable[:6]))
            return Candidate(value, 0.8 if valuable else 0.62, self._source(fragments, value), "Штрафные санкции извлечены из раздела ответственности.")
        return None

    def deterministic_extract(self, criterion: str, fragments: list[DocumentFragment]) -> Candidate | None:
        text = self._context(fragments)
        low = criterion.lower()
        if "номер" in low:
            return self._extract_contract_number(text, fragments)
        if "дата" in low and "подпис" in low:
            return self._extract_sign_date(text, fragments)
        if "реквиз" in low:
            return self._extract_requisites(text, fragments)
        if "контрагент" in low:
            return self._extract_counterparty(text, fragments)
        if "документ" in low or "закрыт" in low:
            return self._extract_closing_documents(text, fragments)
        if "срок" in low:
            return self._extract_terms(text, fragments)
        if "штраф" in low or "санкц" in low or "наруш" in low:
            return self._extract_penalties(text, fragments)
        return None

    async def llm_extract(self, criterion: str, fragments: list[DocumentFragment]) -> Candidate | None:
        context = self._context(fragments)[:16000]
        if not context:
            return None
        try:
            data = await chat_json(
                "Ты извлекаешь данные из коммерческого договора. Верни только JSON без скрытых рассуждений.",
                json.dumps(
                    {
                        "criterion": criterion,
                        "context": context,
                        "schema": {"value": "string", "confidence": "number", "reasoning_summary": "short string"},
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            return None
        value = str(data.get("value") or "").strip()
        if not value:
            return None
        return Candidate(
            value=value,
            confidence=float(data.get("confidence") or 0.55),
            fragments=self._source(fragments, value),
            summary=str(data.get("reasoning_summary") or "Значение извлечено LLM из релевантного контекста."),
        )

    async def run_one(self, criterion: str, fragments: list[DocumentFragment]) -> ExtractionResult:
        candidate = self.deterministic_extract(criterion, fragments)
        if candidate is None:
            candidate = await self.llm_extract(criterion, fragments)
        if candidate is None:
            candidate = Candidate(NOT_FOUND, 0.0, fragments[:3], "Значение не найдено детерминированными правилами, LLM недоступна или не вернула результат.")
        return ExtractionResult(
            criterion=criterion,
            value=candidate.value,
            normalized_value=candidate.value if candidate.value != NOT_FOUND else None,
            confidence=candidate.confidence,
            source_fragments=candidate.fragments,
            reasoning_summary=candidate.summary,
        )

    async def run(self, retrievals):
        return await asyncio.gather(*(self.run_one(c, f) for c, f in retrievals.items()))
