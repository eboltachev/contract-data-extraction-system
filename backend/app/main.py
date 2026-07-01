from fastapi import FastAPI
from app.core.logging import setup_logging
from app.api.routes.jobs import router as jobs_router
from app.api.routes.health import router as health_router
setup_logging(); app=FastAPI(title="Contract Data Extraction System")
app.include_router(health_router); app.include_router(jobs_router)
