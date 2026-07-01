from app.agents.base import BaseAgent
from app.agents.langchain_contract_agents import LangChainContractMultiAgentSystem


class ExtractionAgent(BaseAgent):
    name = "ExtractionAgent"

    async def run(self, retrievals):
        system = LangChainContractMultiAgentSystem()
        return await system.extract_many(retrievals)
