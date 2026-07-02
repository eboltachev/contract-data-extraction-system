from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.domain.documents import DocumentFragment
from app.domain.extraction import ExtractionResult

NOT_FOUND = "Не найдено в договоре. Требует ручной проверки."


@dataclass(frozen=True)
class EvidencePack:
    criterion: str
    fragments: list[DocumentFragment]

    def as_text(self, limit: int = 24_000) -> str:
        blocks: list[str] = []
        for idx, fragment in enumerate(self.fragments, start=1):
            section = fragment.section or "Без раздела"
            clause = f"; пункт: {fragment.clause}" if fragment.clause else ""
            text = _compact(fragment.text)
            blocks.append(f"[Фрагмент {idx}; раздел: {section}{clause}]\n{text}")
        return "\n\n".join(blocks)[:limit]


class ContractToolbox:
    def __init__(self, evidence: EvidencePack) -> None:
        self.evidence = evidence

    def read_all_evidence(self, limit: int = 24_000) -> str:
        return self.evidence.as_text(limit=limit)

    def search_document(self, query: str, limit: int = 10) -> str:
        terms = _terms(query)
        scored: list[tuple[int, int, DocumentFragment]] = []

        for idx, fragment in enumerate(self.evidence.fragments):
            searchable = _normalize(" ".join([fragment.section or "", fragment.clause or "", fragment.text]))
            score = sum(1 for term in terms if term in searchable)
            if score:
                scored.append((score, -idx, fragment))

        selected = [item[2] for item in sorted(scored, reverse=True)[:limit]]
        if not selected:
            selected = self.evidence.fragments[:limit]

        return EvidencePack(self.evidence.criterion, selected).as_text()

    def broaden_context(self, limit: int = 32_000) -> str:
        return self.evidence.as_text(limit=limit)


class LangChainContractMultiAgentSystem:
    """
    Универсальный LLM-extractor.

    Контракт:
    - extract_many() всегда возвращает ExtractionResult для каждого критерия;
    - value не должен быть пустым;
    - модель не должна придумывать значение без evidence;
    - при нехватке evidence возвращается NOT_FOUND;
    - ошибки LLM по одному критерию не валят весь pipeline.
    """

    def __init__(self, job_id: str | None = None, logger=None) -> None:
        self.job_id = job_id
        self.logger = logger
        self.max_iterations = max(1, int(settings.AGENT_MAX_ITERATIONS))
        self._llm_enabled = settings.LITELLM_API_KEY.strip().lower() not in {
            "",
            "change_me",
            "none",
            "null",
        }
        self.model = self._build_model() if self._llm_enabled else None

    def _build_model(self):
        return ChatOpenAI(
            model=settings.LLM_MODEL,
            base_url=settings.LITELLM_BASE_URL,
            api_key=settings.LITELLM_API_KEY,
            temperature=0,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,
        ).bind(response_format={"type": "json_object"})

    async def log_step(
        self,
        agent: str,
        action: str,
        status: str = "started",
        **data: Any,
    ) -> None:
        if self.logger is not None:
            await self.logger.log(
                job_id=self.job_id,
                agent=agent,
                action=action,
                status=status,
                **data,
            )

    async def _ainvoke_json(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError("LLM is disabled: LITELLM_API_KEY is empty or equals change_me")

        response = await asyncio.wait_for(
            self.model.ainvoke(
                [
                    SystemMessage(content=system),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ]
            ),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )

        content = response.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)

        return _loads_json(content)

    async def batch_extract(
        self,
        retrievals: dict[str, list[DocumentFragment]],
    ) -> list[ExtractionResult]:
        criteria = list(retrievals.keys())
        evidence = EvidencePack(
            criterion="; ".join(criteria),
            fragments=self._union_fragments(retrievals),
        )

        await self.log_step(
            "LeadContractExtractionAgent",
            "batch_extract",
            criteria_count=len(criteria),
            evidence_fragments=len(evidence.fragments),
        )

        if self.model is None:
            return [
                self._not_found_result(
                    criterion=criterion,
                    fragments=fragments,
                    reason="LLM отключена: отсутствует API key.",
                )
                for criterion, fragments in retrievals.items()
            ]

        data = await self._ainvoke_json(
            system="""
Ты LeadContractExtractionAgent для извлечения данных из договоров в Excel-шаблон.

Тебе переданы:
1. criteria — список критериев из Excel-шаблона;
2. evidence — фрагменты договора.

Правила:
- Верни результат для каждого критерия из criteria.
- Используй только evidence.
- Не выдумывай отсутствующие сведения.
- Если данных нет, value = "Не найдено в договоре. Требует ручной проверки.", confidence = 0.
- Если критерий просит список, верни список строк с "- ".
- Если критерий просит реквизиты, структурируй по полям: ИНН, КПП, ОГРН, адрес, банк, р/с, к/с, БИК.
- Если критерий просит дату/номер/сумму, извлеки точное значение.
- Для Excel ответ должен быть компактным, но достаточным.
- source_quotes должны быть короткими точными цитатами из evidence.
- reasoning_summary — краткое объяснение без chain-of-thought.

Верни строгий JSON:
{
  "results": [
    {
      "criterion": "точный критерий из входного списка",
      "value": "ответ",
      "normalized_value": null или нормализованное значение,
      "confidence": число от 0 до 1,
      "source_quotes": ["короткая цитата"],
      "reasoning_summary": "краткое объяснение"
    }
  ]
}
""".strip(),
            payload={
                "criteria": criteria,
                "evidence": evidence.as_text(limit=34_000),
            },
        )

        by_criterion: dict[str, dict[str, Any]] = {}
        for item in data.get("results") or []:
            criterion = str(item.get("criterion") or "").strip()
            if criterion:
                by_criterion[criterion] = item

        results: list[ExtractionResult] = []
        for criterion, fragments in retrievals.items():
            item = by_criterion.get(criterion)
            if not item:
                results.append(
                    self._not_found_result(
                        criterion=criterion,
                        fragments=fragments,
                        reason="LLM не вернула результат для критерия в batch-режиме.",
                    )
                )
                continue

            results.append(self._to_result(criterion, item, fragments))

        await self.log_step(
            "LeadContractExtractionAgent",
            "batch_extract",
            status="completed",
            extracted=sum(1 for result in results if result.value != NOT_FOUND),
        )
        return results

    async def plan(self, criterion: str, evidence: EvidencePack) -> dict[str, Any]:
        return await self._ainvoke_json(
            system="""
Ты CriteriaPlanningAgent. Составь короткий план извлечения значения из договора.
Не извлекай значение.

Верни строгий JSON:
{
  "criterion": "критерий",
  "sub_questions": ["подвопросы для поиска"],
  "requires_calculation": false,
  "answer_format": "short_text|list|requisites|date|money|boolean",
  "validation_rules": ["правила проверки"]
}
""".strip(),
            payload={
                "criterion": criterion,
                "evidence_preview": evidence.as_text(limit=8_000),
            },
        )

    async def retrieve(
        self,
        criterion: str,
        plan: dict[str, Any],
        toolbox: ContractToolbox,
    ) -> str:
        questions = plan.get("sub_questions") or [criterion]
        blocks: list[str] = []

        for question in questions[: self.max_iterations + 3]:
            blocks.append(toolbox.search_document(str(question), limit=10))

        blocks.append(toolbox.read_all_evidence(limit=20_000))
        return "\n\n".join(blocks)[:32_000]

    async def extract(
        self,
        criterion: str,
        plan: dict[str, Any],
        evidence_text: str,
    ) -> dict[str, Any]:
        return await self._ainvoke_json(
            system="""
Ты ExtractionAgent для договоров.

Задача: извлечь значение одного критерия из evidence.

Правила:
- Используй только evidence.
- Не копируй огромные фрагменты договора; сформулируй компактный ответ.
- Если критерий требует список, верни список строк с "- ".
- Если критерий требует реквизиты, структурируй поля.
- Если критерий требует вычисление, используй только числа из evidence и кратко укажи расчет в reasoning_summary.
- Если evidence недостаточно, value = "Не найдено в договоре. Требует ручной проверки.", confidence = 0.
- source_quotes — короткие дословные цитаты, подтверждающие ответ.
- reasoning_summary — короткое объяснение, без chain-of-thought.

Верни строгий JSON:
{
  "criterion": "критерий",
  "value": "ответ",
  "normalized_value": null или строка,
  "confidence": число от 0 до 1,
  "source_quotes": ["цитата"],
  "reasoning_summary": "краткое объяснение"
}
""".strip(),
            payload={
                "criterion": criterion,
                "plan": plan,
                "evidence": evidence_text,
            },
        )

    async def validate(
        self,
        criterion: str,
        extracted: dict[str, Any],
        evidence_text: str,
    ) -> dict[str, Any]:
        return await self._ainvoke_json(
            system="""
Ты ValidationAgent. Проверь извлеченное значение.

Исправь результат, если:
- value не отвечает критерию;
- value не подтвержден evidence;
- value слишком длинный и является склейкой фрагментов;
- пропущены важные найденные в evidence значения.

Если подтверждения нет, установи:
value = "Не найдено в договоре. Требует ручной проверки.", confidence = 0.

Верни строгий JSON той же формы:
{
  "criterion": "критерий",
  "value": "ответ",
  "normalized_value": null или строка,
  "confidence": число от 0 до 1,
  "source_quotes": ["цитата"],
  "reasoning_summary": "краткое объяснение"
}
""".strip(),
            payload={
                "criterion": criterion,
                "extracted": extracted,
                "evidence": evidence_text[:24_000],
            },
        )

    async def extract_one(
        self,
        criterion: str,
        fragments: list[DocumentFragment],
    ) -> ExtractionResult:
        evidence = EvidencePack(criterion=criterion, fragments=fragments)
        toolbox = ContractToolbox(evidence)

        if self.model is None:
            return self._not_found_result(
                criterion=criterion,
                fragments=fragments,
                reason="LLM отключена: отсутствует API key.",
            )

        try:
            await self.log_step("CriteriaPlanningAgent", "plan_criterion", criterion=criterion)
            plan = await self.plan(criterion, evidence)
            await self.log_step(
                "CriteriaPlanningAgent",
                "plan_criterion",
                status="completed",
                criterion=criterion,
                sub_questions=len(plan.get("sub_questions") or []),
            )

            await self.log_step("RetrievalAgent", "retrieve_evidence", criterion=criterion)
            evidence_text = await self.retrieve(criterion, plan, toolbox)
            await self.log_step(
                "RetrievalAgent",
                "retrieve_evidence",
                status="completed",
                criterion=criterion,
                evidence_chars=len(evidence_text),
            )

            extracted: dict[str, Any] | None = None
            for _ in range(self.max_iterations):
                await self.log_step("ExtractionAgent", "extract_criterion", criterion=criterion)
                extracted = await self.extract(criterion, plan, evidence_text)
                await self.log_step(
                    "ExtractionAgent",
                    "extract_criterion",
                    status="completed",
                    criterion=criterion,
                    confidence=extracted.get("confidence"),
                )

                value = str(extracted.get("value") or "").strip()
                confidence = _safe_float(extracted.get("confidence"))
                if value and value != NOT_FOUND and confidence >= 0.55:
                    break

                evidence_text = toolbox.broaden_context()

            await self.log_step("ValidationAgent", "validate_criterion", criterion=criterion)
            validated = await self.validate(criterion, extracted or {}, evidence_text)
            await self.log_step(
                "ValidationAgent",
                "validate_criterion",
                status="completed",
                criterion=criterion,
                confidence=validated.get("confidence"),
            )

            return self._to_result(criterion, validated, fragments)

        except Exception as exc:
            await self.log_step(
                "ExtractionAgent",
                "extract_criterion",
                status="failed",
                criterion=criterion,
                error=type(exc).__name__,
            )
            return self._not_found_result(
                criterion=criterion,
                fragments=fragments,
                reason=f"LLM/tool pipeline unavailable: {type(exc).__name__}",
            )

    async def extract_many(
        self,
        retrievals: dict[str, list[DocumentFragment]],
    ) -> list[ExtractionResult]:
        if not retrievals:
            return []

        if self.model is None:
            return [
                self._not_found_result(
                    criterion=criterion,
                    fragments=fragments,
                    reason="LLM отключена: отсутствует API key.",
                )
                for criterion, fragments in retrievals.items()
            ]

        try:
            batch_results = await asyncio.wait_for(
                self.batch_extract(retrievals),
                timeout=settings.EXTRACTION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            await self.log_step(
                "LeadContractExtractionAgent",
                "batch_extract",
                status="failed",
                error=type(exc).__name__,
            )
            batch_results = []

        complete = {
            result.criterion: result
            for result in batch_results
            if result.value
            and result.value != NOT_FOUND
            and not result.value.startswith("Не найдено")
            and result.confidence >= 0.45
        }

        missing = {
            criterion: fragments
            for criterion, fragments in retrievals.items()
            if criterion not in complete
        }

        if missing:
            semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_REQUESTS)

            async def guarded(
                criterion: str,
                fragments: list[DocumentFragment],
            ) -> ExtractionResult:
                async with semaphore:
                    try:
                        return await asyncio.wait_for(
                            self.extract_one(criterion, fragments),
                            timeout=settings.EXTRACTION_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        await self.log_step(
                            "ExtractionAgent",
                            "extract_criterion",
                            status="failed",
                            criterion=criterion,
                            error="TimeoutError",
                        )
                        return self._not_found_result(
                            criterion=criterion,
                            fragments=fragments,
                            reason="Extraction timeout exceeded.",
                        )

            fallback_results = await asyncio.gather(
                *(guarded(criterion, fragments) for criterion, fragments in missing.items())
            )
            complete.update({result.criterion: result for result in fallback_results})

        return [
            complete.get(
                criterion,
                self._not_found_result(
                    criterion=criterion,
                    fragments=fragments,
                    reason="Extractor did not produce result.",
                ),
            )
            for criterion, fragments in retrievals.items()
        ]

    def _to_result(
        self,
        criterion: str,
        item: dict[str, Any],
        fragments: list[DocumentFragment],
    ) -> ExtractionResult:
        value = str(item.get("value") or NOT_FOUND).strip() or NOT_FOUND
        quotes = [str(quote) for quote in item.get("source_quotes") or []]
        confidence = _safe_float(item.get("confidence"))
        if value == NOT_FOUND or value.startswith("Не найдено"):
            confidence = 0.0

        return ExtractionResult(
            criterion=criterion,
            value=value,
            normalized_value=item.get("normalized_value"),
            confidence=confidence,
            source_fragments=self._match_sources(quotes, fragments),
            reasoning_summary=str(
                item.get("reasoning_summary")
                or "Ответ синтезирован универсальным LLM extraction pipeline."
            ),
        )

    def _not_found_result(
        self,
        criterion: str,
        fragments: list[DocumentFragment],
        reason: str,
    ) -> ExtractionResult:
        return ExtractionResult(
            criterion=criterion,
            value=NOT_FOUND,
            normalized_value=None,
            confidence=0.0,
            source_fragments=fragments[:3],
            reasoning_summary=reason,
        )

    def _match_sources(
        self,
        quotes: list[str],
        fragments: list[DocumentFragment],
    ) -> list[DocumentFragment]:
        matched: list[DocumentFragment] = []

        for quote in quotes[:8]:
            needle = _normalize(quote)[:180]
            if not needle:
                continue

            for fragment in fragments:
                if needle and needle in _normalize(fragment.text) and fragment not in matched:
                    matched.append(fragment)
                    break

        if matched:
            return matched[:5]

        return fragments[:3]

    @staticmethod
    def _union_fragments(
        retrievals: dict[str, list[DocumentFragment]],
    ) -> list[DocumentFragment]:
        seen: set[tuple[str | None, str | None, str]] = set()
        unique: list[DocumentFragment] = []

        for fragments in retrievals.values():
            for fragment in fragments:
                key = (fragment.section, fragment.clause, fragment.text)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(fragment)

        return unique


def _loads_json(content: str) -> dict[str, Any]:
    text = content.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else {}
        raise


def _terms(value: str) -> list[str]:
    value = _normalize(value)
    tokens = re.findall(r"[а-яa-z0-9№%\-/]{3,}", value)
    return list(dict.fromkeys(tokens))


def _normalize(value: str) -> str:
    value = value.lower().replace("ё", "е").replace("\xa0", " ")
    value = re.sub(r"[^а-яa-z0-9№%\-/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, result))
