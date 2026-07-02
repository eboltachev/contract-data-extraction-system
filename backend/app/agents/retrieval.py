from __future__ import annotations

import asyncio
import re

from app.agents.base import BaseAgent
from app.domain.documents import DocumentFragment
from app.infrastructure.llm.reranker import rerank


class RetrievalAgent(BaseAgent):
    name = "RetrievalAgent"

    KEYWORDS: dict[str, tuple[str, ...]] = {
        "дата": ("договор", "контракт", "от", "дата", "подпис"),
        "номер": ("договор", "контракт", "соглашение", "№", "номер"),
        "контрагент": ("исполнитель", "подрядчик", "субподрядчик", "поставщик", "заказчик", "общество", "ооо", "ао"),
        "реквиз": ("реквизит", "инн", "кпп", "огрн", "бик", "расчетный", "счет", "банк"),
        "документ": ("кс-2", "кс-3", "акт", "счет-фактура", "упд", "закрыва", "приемк"),
        "закрыва": ("кс-2", "кс-3", "акт", "счет-фактура", "чек-лист", "исполнительн", "финальный"),
        "срок": ("срок", "календар", "рабоч", "начало", "окончание", "в течение", "не позднее"),
        "исполн": ("срок", "начало", "окончание", "фактического окончания", "финального акта"),
        "штраф": ("штраф", "пеня", "пени", "неустой", "ответствен", "процент", "%"),
        "санкц": ("штраф", "пеня", "пени", "неустой", "ответствен", "приложение № 8"),
    }

    SECTION_BOOSTS: dict[str, tuple[str, ...]] = {
        "реквиз": ("реквизиты", "подписи сторон"),
        "документ": ("порядок оплаты", "приемки работ"),
        "закрыва": ("порядок оплаты", "приемки работ"),
        "срок": ("сроки выполнения", "порядок оплаты"),
        "исполн": ("сроки выполнения",),
        "штраф": ("ответственность",),
        "санкц": ("ответственность",),
    }

    async def run_one(self, plan, fragments: list[DocumentFragment]):
        if not fragments:
            return plan.criterion, []

        criterion = plan.criterion
        keywords = self._keywords_for(criterion)
        scored: list[tuple[float, int, DocumentFragment]] = []

        for idx, fragment in enumerate(fragments):
            text = self._normalize(" ".join([fragment.section or "", fragment.clause or "", fragment.text]))
            section = self._normalize(fragment.section or "")
            score = 0.0

            for keyword in keywords:
                normalized_keyword = self._normalize(keyword)
                if normalized_keyword and normalized_keyword in text:
                    score += 3.0

            for marker, sections in self.SECTION_BOOSTS.items():
                if marker in self._normalize(criterion):
                    score += sum(5.0 for section_hint in sections if self._normalize(section_hint) in section)

            for target in getattr(plan, "target_sections", []) or []:
                if self._normalize(target) in section:
                    score += 2.0

            for page_name in getattr(plan, "allowed_wiki_pages", []) or []:
                if self._normalize(page_name) in section:
                    score += 14.0

            if "|" in fragment.text and any(marker in self._normalize(criterion) for marker in ("реквиз", "таблиц", "контрагент")):
                score += 4.0

            if self._is_header_field(criterion) and idx < 40:
                score += 10.0
                if "№" in fragment.text or re.search(r"\d{1,2}\s+[а-я]+\s+\d{4}", fragment.text, re.I):
                    score += 15.0

            if score:
                scored.append((score, -idx, fragment))

        candidates = [item[2] for item in sorted(scored, reverse=True)[:60]] or fragments[:40]
        expanded = self._expand_neighbors(fragments, candidates, radius=1)
        base = self._deduplicate([*candidates, *expanded])
        ranked = await rerank(criterion, base)

        if self._is_header_field(criterion):
            header = self._header_fragments(fragments)
            page_fragments = self._allowed_page_fragments(fragments, getattr(plan, "allowed_wiki_pages", []) or [])
            return criterion, self._deduplicate([*page_fragments, *header, *ranked])[:32]

        page_fragments = self._allowed_page_fragments(fragments, getattr(plan, "allowed_wiki_pages", []) or [])
        return criterion, self._deduplicate([*page_fragments, *ranked, *base])[:36]

    async def run(self, plans, parsed):
        pairs = await asyncio.gather(*(self.run_one(plan, parsed.fragments) for plan in plans))
        return dict(pairs)

    def _keywords_for(self, criterion: str) -> tuple[str, ...]:
        normalized = self._normalize(criterion)
        words: list[str] = []
        for marker, values in self.KEYWORDS.items():
            if marker in normalized:
                words.extend(values)
        words.extend(re.findall(r"[а-яА-Яa-zA-Z0-9№%\-/]{3,}", normalized))
        return tuple(dict.fromkeys(words))

    @staticmethod
    def _allowed_page_fragments(fragments: list[DocumentFragment], page_names: list[str]) -> list[DocumentFragment]:
        if not page_names:
            return []
        normalized_pages = [RetrievalAgent._normalize(page_name) for page_name in page_names]
        return [
            fragment
            for fragment in fragments
            if any(page in RetrievalAgent._normalize(fragment.section or "") for page in normalized_pages)
        ][:20]

    @staticmethod
    def _expand_neighbors(
        all_fragments: list[DocumentFragment],
        candidates: list[DocumentFragment],
        radius: int = 1,
    ) -> list[DocumentFragment]:
        index_by_key = {
            (fragment.section, fragment.clause, fragment.text): idx
            for idx, fragment in enumerate(all_fragments)
        }
        expanded: list[DocumentFragment] = []
        for fragment in candidates[:30]:
            idx = index_by_key.get((fragment.section, fragment.clause, fragment.text))
            if idx is None:
                continue
            start = max(0, idx - radius)
            end = min(len(all_fragments), idx + radius + 1)
            expanded.extend(all_fragments[start:end])
        return expanded

    @staticmethod
    def _is_header_field(criterion: str) -> bool:
        normalized = criterion.lower().replace("ё", "е")
        return any(marker in normalized for marker in ("номер", "дата")) and any(
            marker in normalized for marker in ("договор", "контракт", "соглашен", "подпис")
        )

    @staticmethod
    def _header_fragments(fragments: list[DocumentFragment], limit: int = 40) -> list[DocumentFragment]:
        return [fragment for fragment in fragments[:limit] if fragment.text.strip()] or fragments[:limit]

    @staticmethod
    def _deduplicate(fragments: list[DocumentFragment]) -> list[DocumentFragment]:
        seen: set[tuple[str | None, str | None, str]] = set()
        unique: list[DocumentFragment] = []
        for fragment in fragments:
            key = (fragment.section, fragment.clause, fragment.text)
            if key in seen:
                continue
            seen.add(key)
            unique.append(fragment)
        return unique

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.lower().replace("ё", "е").replace("\xa0", " ")
        value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()
