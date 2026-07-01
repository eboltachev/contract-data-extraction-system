"""LangChain-style multi-agent contract extraction system.

The design follows LangChain's multi-agent guidance: keep a coordinator in control,
expose focused subagents/tools, isolate context per criterion, and allow iterative
re-querying of document evidence before synthesizing a final answer.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.domain.documents import DocumentFragment
from app.domain.extraction import ExtractionResult

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."


@dataclass(frozen=True)
class EvidencePack:
    criterion: str
    fragments: list[DocumentFragment]

    def as_text(self, limit: int = 18000) -> str:
        blocks: list[str] = []
        for idx, fragment in enumerate(self.fragments, start=1):
            section = fragment.section or "Без раздела"
            clause = f", пункт: {fragment.clause}" if fragment.clause else ""
            blocks.append(f"[Фрагмент {idx}; раздел: {section}{clause}]\n{fragment.text}")
        return "\n\n".join(blocks)[:limit]


class ContractToolbox:
    """Criterion-scoped tools available to subagents."""

    def __init__(self, evidence: EvidencePack):
        self.evidence = evidence

    def search_document(self, query: str, limit: int = 8) -> str:
        """Return relevant evidence fragments for a focused sub-question."""
        query_terms = {part.lower() for part in query.split() if len(part) > 2}
        scored: list[tuple[int, int, DocumentFragment]] = []
        for idx, fragment in enumerate(self.evidence.fragments):
            text = f"{fragment.section or ''} {fragment.clause or ''} {fragment.text}".lower()
            score = sum(1 for term in query_terms if term in text)
            if score:
                scored.append((score, -idx, fragment))
        selected = [item[2] for item in sorted(scored, reverse=True)[:limit]] or self.evidence.fragments[:limit]
        return EvidencePack(self.evidence.criterion, selected).as_text()

    def read_all_evidence(self) -> str:
        """Return all retrieved evidence for the criterion."""
        return self.evidence.as_text()

    def calculate(self, expression: str) -> str:
        """Safely evaluate simple arithmetic requested by a criterion."""
        allowed = set("0123456789+-*/()., ")
        if not expression or any(ch not in allowed for ch in expression):
            return "Расчет не выполнен: выражение содержит недопустимые символы."
        normalized = expression.replace(",", ".")
        try:
            return str(eval(normalized, {"__builtins__": {}}, {}))
        except Exception as exc:  # noqa: BLE001 - tool must return model-readable error
            return f"Расчет не выполнен: {exc}"

    def as_langchain_tools(self):
        toolbox = self

        @tool
        def search_document(query: str) -> str:
            """Search contract evidence by a focused query and return supporting fragments."""
            return toolbox.search_document(query)

        @tool
        def read_all_evidence() -> str:
            """Read all evidence fragments currently available for this criterion."""
            return toolbox.read_all_evidence()

        @tool
        def calculate(expression: str) -> str:
            """Run simple arithmetic for criteria that require summation or formula evaluation."""
            return toolbox.calculate(expression)

        return [search_document, read_all_evidence, calculate]


class LangChainContractMultiAgentSystem:
    """Coordinator + specialist subagents implemented with LangChain model/tool APIs."""

    def __init__(self) -> None:
        self.model = ChatOpenAI(
            model=settings.LLM_MODEL,
            base_url=settings.LITELLM_BASE_URL,
            api_key=settings.LITELLM_API_KEY,
            temperature=0,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,
        ).bind(response_format={"type": "json_object"})
        self.max_iterations = settings.AGENT_MAX_ITERATIONS

    async def _ainvoke_json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await asyncio.wait_for(
            self.model.ainvoke(
                [
                    SystemMessage(content=system),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ]
            ),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
        return json.loads(content or "{}")

    async def plan(self, criterion: str, evidence: EvidencePack) -> dict[str, Any]:
        return await self._ainvoke_json(
            """
Ты CriteriaPlanningAgent. Составь план извлечения значения из договора.
Не извлекай значение. Определи какие под-вопросы и инструменты нужны.
Верни JSON: {"criterion": string, "sub_questions": string[], "requires_calculation": bool,
"validation_rules": string[], "answer_format": string}.
""".strip(),
            {"criterion": criterion, "available_evidence_preview": evidence.as_text(6000)},
        )

    async def retrieve(self, criterion: str, plan: dict[str, Any], toolbox: ContractToolbox) -> str:
        questions = plan.get("sub_questions") or [criterion]
        tool_outputs = []
        for question in questions[: self.max_iterations + 2]:
            tool_outputs.append(toolbox.search_document(str(question)))
        if not tool_outputs:
            tool_outputs.append(toolbox.read_all_evidence())
        return "\n\n".join(tool_outputs)[:22000]

    async def extract(self, criterion: str, plan: dict[str, Any], evidence_text: str, toolbox: ContractToolbox) -> dict[str, Any]:
        tools = toolbox.as_langchain_tools()
        tool_descriptions = [f"{t.name}: {t.description}" for t in tools]
        return await self._ainvoke_json(
            """
Ты ExtractionAgent для коммерческих договоров. Используй предоставленные фрагменты как доказательства.
Синтезируй краткий, целостный ответ для Excel, а не копируй большие разрозненные чанки.
Если критерий требует список, верни аккуратный список строк. Если нужно посчитать сумму/срок,
используй данные из evidence и явно укажи расчет в reasoning_summary, но не раскрывай chain-of-thought.
Если доказательств недостаточно, верни value = "Не найдено в договоре. Требует ручной проверки.".
Верни строгий JSON: {"criterion": string, "value": string, "normalized_value": string|null,
"confidence": number, "source_quotes": string[], "reasoning_summary": string}.
""".strip(),
            {
                "criterion": criterion,
                "plan": plan,
                "available_tools": tool_descriptions,
                "evidence": evidence_text,
            },
        )

    async def validate(self, criterion: str, extracted: dict[str, Any], evidence_text: str) -> dict[str, Any]:
        return await self._ainvoke_json(
            """
Ты ValidationAgent. Проверь, что value отвечает критерию, подтвержден evidence и достаточно краток для Excel.
Если value — длинная склейка фрагментов вместо ответа, переформулируй в краткую структурированную выжимку.
Если value не подтвержден, установи confidence ниже 0.5 и добавь "Требует проверки: ...".
Верни JSON той же формы: {"criterion": string, "value": string, "normalized_value": string|null,
"confidence": number, "source_quotes": string[], "reasoning_summary": string}.
""".strip(),
            {"criterion": criterion, "extracted": extracted, "evidence": evidence_text[:18000]},
        )

    async def extract_one(self, criterion: str, fragments: list[DocumentFragment]) -> ExtractionResult:
        evidence = EvidencePack(criterion=criterion, fragments=fragments)
        toolbox = ContractToolbox(evidence)
        try:
            plan = await self.plan(criterion, evidence)
            evidence_text = await self.retrieve(criterion, plan, toolbox)
            extracted: dict[str, Any] | None = None
            for _ in range(max(1, self.max_iterations)):
                extracted = await self.extract(criterion, plan, evidence_text, toolbox)
                value = str(extracted.get("value") or "").strip()
                confidence = float(extracted.get("confidence") or 0)
                if value and value != NOT_FOUND and confidence >= 0.55:
                    break
                # Iterative data access: ask retrieval to broaden the context and retry.
                evidence_text = toolbox.read_all_evidence()
            validated = await self.validate(criterion, extracted or {}, evidence_text)
        except Exception as exc:  # noqa: BLE001 - pipeline should return controlled value per criterion
            validated = {
                "criterion": criterion,
                "value": f"Не найдено в договоре. Требует ручной проверки. LLM/tool pipeline unavailable: {type(exc).__name__}",
                "normalized_value": None,
                "confidence": 0.0,
                "source_quotes": [],
                "reasoning_summary": "Мультиагентный LangChain pipeline не смог получить ответ от LLM или инструментов.",
            }
        value = str(validated.get("value") or NOT_FOUND).strip() or NOT_FOUND
        source_quotes = [str(item) for item in validated.get("source_quotes") or []]
        sources = self._match_sources(source_quotes, fragments)
        return ExtractionResult(
            criterion=criterion,
            value=value,
            normalized_value=validated.get("normalized_value"),
            confidence=float(validated.get("confidence") or 0),
            source_fragments=sources,
            reasoning_summary=str(validated.get("reasoning_summary") or "Ответ синтезирован мультиагентным pipeline."),
        )

    def _match_sources(self, quotes: list[str], fragments: list[DocumentFragment]) -> list[DocumentFragment]:
        matched: list[DocumentFragment] = []
        for quote in quotes[:5]:
            needle = quote.strip().lower()[:160]
            if not needle:
                continue
            for fragment in fragments:
                if needle in fragment.text.lower() and fragment not in matched:
                    matched.append(fragment)
                    break
        return matched[:3] or fragments[:3]

    async def extract_many(self, retrievals: dict[str, list[DocumentFragment]]) -> list[ExtractionResult]:
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_REQUESTS)

        async def guarded(criterion: str, fragments: list[DocumentFragment]) -> ExtractionResult:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self.extract_one(criterion, fragments),
                        timeout=settings.EXTRACTION_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    return ExtractionResult(
                        criterion=criterion,
                        value="Не найдено в договоре. Требует ручной проверки. Extraction timeout exceeded.",
                        normalized_value=None,
                        confidence=0.0,
                        source_fragments=fragments[:3],
                        reasoning_summary="Превышен лимит времени извлечения для критерия.",
                    )

        return await asyncio.gather(*(guarded(criterion, fragments) for criterion, fragments in retrievals.items()))
