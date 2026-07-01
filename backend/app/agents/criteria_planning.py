from app.agents.base import BaseAgent
from app.domain.criteria import ExtractionPlan
class CriteriaPlanningAgent(BaseAgent):
    name="CriteriaPlanningAgent"
    async def run(self, criteria):
        plans=[]
        for c in criteria:
            n=c.name.lower(); typ="date" if "дата" in n else "requisites" if "реквиз" in n else "text"
            sections=["header","preamble"] if any(x in n for x in ["дата","номер","контрагент"]) else ["ответственность","сроки","реквизиты","приложения"]
            plans.append(ExtractionPlan(criterion=c.name, strategy="hybrid", target_sections=sections, expected_type=typ))
        return plans
