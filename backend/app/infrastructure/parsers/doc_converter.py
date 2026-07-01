import asyncio
from pathlib import Path
from app.core.exceptions import PipelineError
async def convert_with_libreoffice(path: Path, out_dir: Path, target_ext: str) -> Path:
    proc=await asyncio.create_subprocess_exec("libreoffice","--headless","--convert-to",target_ext.strip('.'),"--outdir",str(out_dir),str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err=await proc.communicate()
    if proc.returncode: raise PipelineError(f"Ошибка конвертации LibreOffice: {err.decode(errors='ignore')}")
    out=out_dir/(path.stem+target_ext)
    if not out.exists():
        matches=list(out_dir.glob(path.stem+".*"));
        if matches: return matches[0]
        raise PipelineError("LibreOffice не создал выходной файл")
    return out
