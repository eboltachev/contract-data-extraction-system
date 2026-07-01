# Contract Data Extraction System

Production-ready MVP для автоматизированного извлечения ключевых сведений из коммерческих договоров и заполнения Excel-шаблона.

## Архитектура

- `frontend/`: React + TypeScript + Vite, nginx reverse proxy `/api/* -> backend:8000`.
- `backend/`: Python 3.12 + FastAPI + Clean Architecture/DDD слои, Docling parsing и LangChain multi-agent extraction.
- `docker-compose.yml`: наружу публикуется только `frontend` на `6002`; backend доступен по внутренней сети через `expose: 8000`.

## Запуск

```bash
cp .env.example .env
# заполните LITELLM_API_KEY
docker compose up --build -d
```

UI: <http://localhost:6002>

## Переменные окружения

См. `.env.example`: LiteLLM endpoint, модели LLM/embeddings/reranker, лимиты итераций, конкурентности, размера загрузки и директория хранения задач.

## Формат входных файлов

- Договор: `.doc` или `.docx`.
- Шаблон: `.xls` или `.xlsx`.
- `.doc`/`.xls` конвертируются через LibreOffice headless.
- Выход всегда `.xlsx` и называется как входной договор, но с расширением `.xlsx`.

## Агентное извлечение

Pipeline:

`DocumentParsingAgent -> StructureAnalysisAgent -> CriteriaPlanningAgent -> RetrievalAgent -> ExtractionAgent -> ValidationAgent -> ExcelFillingAgent`

Критерии читаются динамически из листа шаблона с заголовками `№`, `Критерий`, `Значение`. Документ разбирается Docling в структурированный Markdown/evidence. Для каждого критерия LangChain multi-agent coordinator запускает планирование, инструментальный поиск по evidence, при необходимости итеративное расширение контекста, LLM-синтез краткого ответа и LLM-валидацию. Критерии обрабатываются конкурентно с ограничением `MAX_CONCURRENT_LLM_REQUESTS`; лимит итераций задает `AGENT_MAX_ITERATIONS`.

## Как добавить новый критерий

Добавьте новую строку в Excel-шаблон в колонку `Критерий`. Код менять не нужно: сервис прочитает критерий и заполнит соответствующую ячейку колонки `Значение`.

## Rollback и логи

Для каждой задачи создается `/storage/jobs/{job_id}/` с `input`, `working`, `output`, `logs`, `state.json`. При ошибке выходные артефакты переносятся в `failed_artifacts`, статус фиксируется, frontend показывает ошибку. Логи задач пишутся в JSONL.

## Ограничения MVP

- Reranker реализован как keyword fallback; точка расширения под внешний reranker сохранена.
- Качество извлечения зависит от доступности LiteLLM и содержимого договора.
- `.doc`/`.xls` требуют LibreOffice в backend-контейнере.

## Команды тестирования

```bash
cd backend
uv pip install --system -e '.[dev]'
pytest
ruff check app tests
```
