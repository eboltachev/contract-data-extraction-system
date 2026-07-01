import asyncio, json
class ProgressService:
    def __init__(self): self.queues={}
    def queue(self, job_id): return self.queues.setdefault(job_id, asyncio.Queue())
    async def publish(self, job_id, **event): await self.queue(job_id).put(event)
    async def stream(self, job_id):
        q=self.queue(job_id)
        while True:
            event=await q.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("status") in {"completed","failed","rolled_back"}: break
progress_service=ProgressService()
