from app.agents.base import BaseAgent
class StructureAnalysisAgent(BaseAgent):
    name="StructureAnalysisAgent"
    async def run(self, parsed):
        sections={}
        for f in parsed.fragments: sections.setdefault((f.section or "Общее").lower(), []).append(f)
        return sections
