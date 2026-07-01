from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from app.services.job_service import job_service
from app.services.progress_service import progress_service
from app.domain.jobs import JobStatus
from app.api.schemas import JobCreateResponse, JobResponse
router=APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
@router.post("", response_model=JobCreateResponse)
async def create_job(contract_file: UploadFile=File(...), template_file: UploadFile=File(...)):
    try: job=await job_service.create(contract_file, template_file); return JobCreateResponse(job_id=job.job_id, status=job.status)
    except Exception as e: raise HTTPException(400, str(e))
@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job=await job_service.repo.get(job_id)
    if not job: raise HTTPException(404, "Задача не найдена")
    return JobResponse(**job.model_dump())
@router.get("/{job_id}/events")
async def events(job_id: str): return StreamingResponse(progress_service.stream(job_id), media_type="text/event-stream")
@router.get("/{job_id}/download")
async def download(job_id: str):
    job=await job_service.repo.get(job_id)
    if not job: raise HTTPException(404, "Задача не найдена")
    if job.status != JobStatus.completed or not job.output_path: raise HTTPException(409, "Файл еще не готов")
    return FileResponse(Path(job.output_path), filename=job.output_filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
