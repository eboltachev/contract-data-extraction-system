import re, shutil
from pathlib import Path
from fastapi import UploadFile
from app.core.config import settings
from app.core.exceptions import ValidationError
ALLOWED_CONTRACT={".doc", ".docx"}; ALLOWED_TEMPLATE={".xls", ".xlsx"}
def safe_filename(name: str) -> str:
    base=Path(name).name.replace("/", "_").replace("\\", "_")
    return re.sub(r"[\x00-\x1f]", "", base)[:180] or "file"
def output_filename(contract_name: str) -> str: return str(Path(safe_filename(contract_name)).with_suffix(".xlsx"))
class FileRepository:
    async def prepare_dirs(self, root: Path) -> None:
        for p in ["input","working","output","logs","failed_artifacts"]: (root/p).mkdir(parents=True, exist_ok=True)
    async def save_upload(self, upload: UploadFile, dest: Path, allowed: set[str]) -> Path:
        name=safe_filename(upload.filename or "upload"); ext=Path(name).suffix.lower()
        if ext not in allowed: raise ValidationError(f"Недопустимое расширение файла: {ext}")
        out=dest/name; size=0; limit=settings.MAX_UPLOAD_SIZE_MB*1024*1024
        with out.open("wb") as f:
            while chunk := await upload.read(1024*1024):
                size += len(chunk)
                if size > limit: raise ValidationError("Файл превышает допустимый размер")
                f.write(chunk)
        return out
    def rollback_outputs(self, root: Path) -> None:
        failed=root/"failed_artifacts"; failed.mkdir(exist_ok=True)
        for p in (root/"output").glob("*"): shutil.move(str(p), failed/p.name)
