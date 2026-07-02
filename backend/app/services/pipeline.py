from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.agents.criteria_planning import CriteriaPlanningAgent
from app.agents.document_parsing import DocumentParsingAgent
from app.agents.excel_filling import ExcelFillingAgent
from app.agents.extraction import ExtractionAgent
from app.agents.retrieval import RetrievalAgent
from app.agents.structure_analysis import StructureAnalysisAgent
from app.agents.validation import ValidationAgent
from app.core.config import settings
from app.domain.jobs import JobStatus
from app.infrastructure.logging.async_logger import AsyncJobLogger
from app.infrastructure.parsers.doc_converter import convert_with_libreoffice
from app.infrastructure.parsers.excel_template import read_criteria
from app.infrastructure.repositories.file_repository import output_filename
from app.services.progress_service import progress_service


class Pipeline:
    def __init__(self, repo, files):
        self.repo = repo
        self.files = files

    async def stage(self, job, agent, action, progress, logger, fn):
        job.status = JobStatus.processing
        job.progress = progress
        job.current_action = action
        await self.repo.save(job)

        await progress_service.publish(
            job.job_id,
            progress=progress,
            status=job.status,
            agent=agent,
            action=action,
        )
        await logger.log(
            job_id=job.job_id,
            agent=agent,
            action=action,
            status="started",
            progress=progress,
        )

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(fn(), timeout=settings.STAGE_TIMEOUT_SECONDS)
            await logger.log(
                job_id=job.job_id,
                agent=agent,
                action=action,
                status="completed",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return result
        except Exception as exc:
            await logger.log(
                job_id=job.job_id,
                agent=agent,
                action=action,
                status="failed",
                error=str(exc),
            )
            raise

    async def run(self, job, contract_path: Path, template_path: Path):
        root = self.repo.job_dir(job.job_id)
        logger = AsyncJobLogger(root / "logs" / "job.jsonl")
        await logger.start()

        try:
            if template_path.suffix.lower() == ".xls":
                template_path = await convert_with_libreoffice(
                    template_path,
                    root / "working",
                    ".xlsx",
                )

            parsed = await self.stage(
                job,
                "DocumentParsingAgent",
                "Извлечение текста и таблиц договора",
                10,
                logger,
                lambda: DocumentParsingAgent().run(contract_path, root / "working"),
            )

            await self.stage(
                job,
                "StructureAnalysisAgent",
                "Анализ структуры договора",
                25,
                logger,
                lambda: StructureAnalysisAgent().run(parsed),
            )

            _, _, _, criteria = read_criteria(template_path)

            plans = await self.stage(
                job,
                "CriteriaPlanningAgent",
                "Планирование извлечения критериев",
                35,
                logger,
                lambda: CriteriaPlanningAgent().run(criteria),
            )

            retrievals = await self.stage(
                job,
                "RetrievalAgent",
                "Поиск релевантных фрагментов",
                50,
                logger,
                lambda: RetrievalAgent().run(plans, parsed),
            )

            results = await self.stage(
                job,
                "ExtractionAgent",
                "Извлечение значений критериев",
                70,
                logger,
                lambda: ExtractionAgent().run(
                    retrievals,
                    parsed=parsed,
                    job_id=job.job_id,
                    logger=logger,
                ),
            )

            valid = await self.stage(
                job,
                "ValidationAgent",
                "Проверка извлеченных значений",
                85,
                logger,
                lambda: ValidationAgent().run(results),
            )

            out = root / "output" / output_filename(contract_path.name)
            await self.stage(
                job,
                "ExcelFillingAgent",
                "Заполнение Excel-шаблона",
                95,
                logger,
                lambda: ExcelFillingAgent().run(template_path, out, valid),
            )

            job.status = JobStatus.completed
            job.progress = 100
            job.current_action = "Готово"
            job.output_path = str(out)
            job.output_filename = out.name
            await self.repo.save(job)

            await progress_service.publish(
                job.job_id,
                progress=100,
                status=job.status,
                agent="Pipeline",
                action="Готово",
            )
        except Exception as exc:
            self.files.rollback_outputs(root)

            job.status = JobStatus.failed
            job.error = str(exc)
            job.current_action = "Ошибка обработки"
            await self.repo.save(job)

            await progress_service.publish(
                job.job_id,
                progress=job.progress,
                status=job.status,
                agent="Pipeline",
                action="Ошибка",
                error=str(exc),
            )
        finally:
            await logger.close()
