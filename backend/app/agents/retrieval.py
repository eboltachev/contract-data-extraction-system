import asyncio
from app.agents.base import BaseAgent
from app.infrastructure.llm.reranker import rerank
class RetrievalAgent(BaseAgent):
    name="RetrievalAgent"
    async def run_one(self, plan, fragments):
        keyed=[f for f in fragments if any(s in (f.section or '').lower() for s in plan.target_sections)] or fragments
        ranked=await rerank(plan.criterion, keyed)
        return plan.criterion, ranked[:8]
    async def run(self, plans, parsed):
        pairs=await asyncio.gather(*(self.run_one(p, parsed.fragments) for p in plans))
        return dict(pairs)
