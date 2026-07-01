from datetime import datetime
from pydantic import BaseModel
class JobCreateResponse(BaseModel): job_id: str; status: str
class JobResponse(BaseModel):
    job_id: str; status: str; progress: int; current_action: str; error: str | None; created_at: datetime; updated_at: datetime
