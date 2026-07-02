from __future__ import annotations

import asyncio
import ast

from app.agents.base import BaseAgent
from app.agents.generic_extraction import GenericEvidenceExtractor, NOT_FOUND
from app.core.config import settings
from app.domain.documents import ParsedDocument
from app.domain.extraction import ExtractionResult


class ExtractionAgent(BaseAgent):
    name = "ExtractionAgent"

    async def run(
        self,
        retrievals,
        parsed: ParsedDocument | None = None,
        job_id: str | None = None,
        logger=None,
    ) -> list[ExtractionResult]:
        generic = GenericEvidenceExtractor(parsed=parsed, retrievals=retrievals)
        generic_results = generic.extract_many(retrievals.keys())

        if logger is not None:
            await logger.log(
                job_id=job_id,
                agent="GenericEvidenceExtractor",
                action="extract_candidates",
                status="completed",
                extracted=len(generic_results),
            )

        if _llm_disabled():
            return [
                _result_or_not_found(criterion, fragments, generic_results)
                for criterion, fragments in retrievals.items()
            ]

        try:
            from app.agents.langchain_contract_agents import LangChainContractMultiAgentSystem

            system = LangChainContractMultiAgentSystem(job_id=job_id, logger=logger)
            llm_results = await asyncio.wait_for(
                system.extract_many(retrievals),
                timeout=settings.EXTRACTION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if logger is not None:
                await logger.log(
                    job_id=job_id,
                    agent="ExtractionAgent",
                    action="llm_extract_many",
                    status="failed",
                    error=type(exc).__name__,
                )
            llm_results = []

        llm_results = [_normalize_result_value(result) for result in llm_results]
        merged = _merge_results(retrievals, llm_results, generic_results)
        return [merged[criterion] for criterion in retrievals.keys()]


def _merge_results(
    retrievals: dict[str, list],
    llm_results: list[ExtractionResult],
    generic_results: dict[str, ExtractionResult],
) -> dict[str, ExtractionResult]:
    llm_by_criterion = {result.criterion: result for result in llm_results}
    merged: dict[str, ExtractionResult] = {}

    for criterion, fragments in retrievals.items():
        generic = generic_results.get(criterion)
        llm = llm_by_criterion.get(criterion)

        # Для стабильных договорных полей evidence/rule-based результат точнее LLM:
        # LLM склонна выбирать соседние пункты, приложения и фрагменты из нерелевантных разделов.
        if generic is not None and _is_stable_contract_field(criterion) and generic.confidence >= 0.70:
            merged[criterion] = generic
            continue

        if generic is not None and generic.confidence >= 0.90:
            merged[criterion] = generic
            continue

        if _is_good(llm) and (generic is None or llm.confidence >= generic.confidence + 0.20):
            merged[criterion] = llm
            continue

        if generic is not None:
            merged[criterion] = generic
            continue

        if _is_good(llm):
            merged[criterion] = llm
            continue

        merged[criterion] = _not_found(
            criterion,
            fragments,
            "Данных с достаточным подтверждением не найдено в evidence.",
        )

    return merged


def _normalize_result_value(result: ExtractionResult) -> ExtractionResult:
    value = result.value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, list):
                    value = "\n".join(f"- {str(item).strip(' -')}" for item in parsed if str(item).strip())
            except (SyntaxError, ValueError):
                value = stripped
    result.value = str(value).strip()
    return result


def _result_or_not_found(
    criterion: str,
    fragments: list,
    results: dict[str, ExtractionResult],
) -> ExtractionResult:
    return results.get(
        criterion,
        _not_found(
            criterion=criterion,
            fragments=fragments,
            reason="Данных с достаточным подтверждением не найдено в evidence.",
        ),
    )


def _not_found(criterion: str, fragments: list, reason: str) -> ExtractionResult:
    return ExtractionResult(
        criterion=criterion,
        value=NOT_FOUND,
        normalized_value=None,
        confidence=0.0,
        source_fragments=fragments[:3],
        reasoning_summary=reason,
    )


def _is_good(result: ExtractionResult | None) -> bool:
    if not result or not result.value:
        return False
    value = result.value.strip()
    if value == NOT_FOUND or value.startswith("Не найдено"):
        return False
    if len(value) > 6000:
        return False
    if result.confidence < 0.60:
        return False
    return True


def _is_stable_contract_field(criterion: str) -> bool:
    normalized = criterion.lower().replace("ё", "е")
    markers = (
        "номер", "дата", "контрагент", "сторон", "реквиз", "инн", "кпп", "огрн",
        "документ", "закрыва", "срок", "исполн", "штраф", "санкц", "пен", "неустой",
    )
    return any(marker in normalized for marker in markers)


def _llm_disabled() -> bool:
    return settings.LITELLM_API_KEY.strip().lower() in {"", "change_me", "none", "null"}
