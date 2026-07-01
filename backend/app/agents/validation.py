import re, asyncio
from app.agents.base import BaseAgent
class ValidationAgent(BaseAgent):
    name="ValidationAgent"
    async def validate_one(self, r):
        ok=bool(r.value.strip()) and "Не найдено" not in r.value
        if r.confidence < 0.45 and ok: r.value="Требует проверки: "+r.value
        if "дата" in r.criterion.lower() and ok and not re.search(r"\d", r.value): r.value="Требует проверки: "+r.value
        return r
    async def run(self, results): return await asyncio.gather(*(self.validate_one(r) for r in results))
