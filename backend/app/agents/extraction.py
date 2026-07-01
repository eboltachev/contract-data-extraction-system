import asyncio

from app.core.config import settings
from app.agents.base import BaseAgent
from app.agents.langchain_contract_agents import LangChainContractMultiAgentSystem


class ExtractionAgent(BaseAgent):
    name = "ExtractionAgent"

    async def run(self, retrievals, job_id: str | None = None, logger=None):
        system = LangChainContractMultiAgentSystem(job_id=job_id, logger=logger)
        return await asyncio.wait_for(system.extract_many(retrievals), timeout=settings.EXTRACTION_TIMEOUT_SECONDS)
