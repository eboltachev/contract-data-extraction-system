import asyncio, json
from datetime import datetime, UTC
from pathlib import Path
class AsyncJobLogger:
    def __init__(self, path: Path): self.path=path; self.queue=asyncio.Queue(); self.task=None
    async def start(self): self.path.parent.mkdir(parents=True, exist_ok=True); self.task=asyncio.create_task(self._run())
    async def log(self, **data): await self.queue.put({"timestamp":datetime.now(UTC).isoformat(), **data})
    async def _run(self):
        with self.path.open("a", encoding="utf-8") as f:
            while True:
                item=await self.queue.get()
                if item is None: break
                f.write(json.dumps(item, ensure_ascii=False)+"\n"); f.flush()
    async def close(self):
        await self.queue.put(None)
        if self.task: await self.task
