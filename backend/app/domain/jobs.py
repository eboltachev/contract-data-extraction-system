from datetime import datetime, UTC
from enum import StrEnum
from pydantic import BaseModel, Field

class JobStatus(StrEnum):
    queued="queued"; processing="processing"; completed="completed"; failed="failed"; rolled_back="rolled_back"

class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.queued
    progress: int = 0
    current_action: str = "Задача поставлена в очередь"
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    output_path: str | None = None
    output_filename: str | None = None
