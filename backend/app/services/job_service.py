import uuid, asyncio
from pathlib import Path
from fastapi import UploadFile
from app.domain.jobs import Job
from app.core.config import settings
from app.infrastructure.repositories.job_repository import JsonJobRepository
from app.infrastructure.repositories.file_repository import FileRepository, ALLOWED_CONTRACT, ALLOWED_TEMPLATE
from app.services.pipeline import Pipeline
class JobService:
    def __init__(self): self.repo=JsonJobRepository(settings.JOB_STORAGE_DIR); self.files=FileRepository()
    async def create(self, contract_file: UploadFile, template_file: UploadFile) -> Job:
        job=Job(job_id=str(uuid.uuid4())); root=self.repo.job_dir(job.job_id); await self.files.prepare_dirs(root)
        c=await self.files.save_upload(contract_file, root/"input", ALLOWED_CONTRACT)
        t=await self.files.save_upload(template_file, root/"input", ALLOWED_TEMPLATE)
        await self.repo.save(job); asyncio.create_task(Pipeline(self.repo,self.files).run(job,c,t)); return job
job_service=JobService()
