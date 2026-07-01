import json, asyncio
from pathlib import Path
from datetime import datetime, UTC
from app.domain.jobs import Job

class JsonJobRepository:
    def __init__(self, root: str):
        self.root=Path(root); self.root.mkdir(parents=True, exist_ok=True); self._lock=asyncio.Lock()
    def job_dir(self, job_id: str) -> Path: return self.root/job_id
    async def save(self, job: Job) -> None:
        async with self._lock:
            d=self.job_dir(job.job_id); d.mkdir(parents=True, exist_ok=True)
            job.updated_at=datetime.now(UTC)
            (d/"state.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
    async def get(self, job_id: str) -> Job | None:
        p=self.job_dir(job_id)/"state.json"
        return Job.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None
