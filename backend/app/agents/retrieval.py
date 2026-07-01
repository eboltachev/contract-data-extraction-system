import asyncio
import re

from app.agents.base import BaseAgent
from app.domain.documents import DocumentFragment
from app.infrastructure.llm.reranker import rerank


class RetrievalAgent(BaseAgent):
    name = "RetrievalAgent"

    KEYWORDS: dict[str, tuple[str, ...]] = {
        "дата": ("договор", "от", "дата", "подпис"),
        "номер": ("договор", "№", "номер"),
        "контрагент": ("исполнитель", "подрядчик", "поставщик", "заказчик", "общество", "ооо", "ао"),
        "реквиз": ("реквизит", "инн", "кпп", "огрн", "бик", "расчетный", "счет", "банк"),
        "документ": ("кс-2", "кс-3", "акт", "счет-фактура", "упд", "закрыва", "приемк"),
        "срок": ("срок", "календар", "рабоч", "до ", "в течение", "этап"),
        "штраф": ("штраф", "пеня", "неустой", "ответствен", "процент", "%"),
    }

    def _keywords_for(self, criterion: str) -> tuple[str, ...]:
        low = criterion.lower()
        words: list[str] = []
        for marker, values in self.KEYWORDS.items():
            if marker in low:
                words.extend(values)
        words.extend(re.findall(r"[а-яА-Яa-zA-Z0-9№%-]{3,}", low))
        return tuple(dict.fromkeys(words))

    async def run_one(self, plan, fragments: list[DocumentFragment]):
        keywords = self._keywords_for(plan.criterion)
        scored: list[tuple[int, int, DocumentFragment]] = []
        for idx, fragment in enumerate(fragments):
            text = fragment.text.lower()
            section = (fragment.section or "").lower()
            score = sum(3 for kw in keywords if kw.lower() in text)
            score += sum(2 for target in plan.target_sections if target in section)
            if score:
                scored.append((score, -idx, fragment))
        candidates = [item[2] for item in sorted(scored, reverse=True)[:32]] or fragments[:32]
        # Always prepend document header and tail: contract number/date/counterparty are usually
        # in the header, while requisites/signatures are often at the end.
        merged = [*fragments[:10], *candidates, *fragments[-20:]]
        seen: set[tuple[str | None, str | None, str]] = set()
        unique: list[DocumentFragment] = []
        for fragment in merged:
            key = (fragment.section, fragment.clause, fragment.text)
            if key not in seen:
                seen.add(key)
                unique.append(fragment)
        ranked = await rerank(plan.criterion, unique)
        # Keep enough context for deterministic extraction; previous 8-fragment limit often dropped
        # the actual clause and produced mostly "Не найдено" values when LLM was unavailable.
        return plan.criterion, ranked[:16]

    async def run(self, plans, parsed):
        pairs = await asyncio.gather(*(self.run_one(p, parsed.fragments) for p in plans))
        return dict(pairs)
