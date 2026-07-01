from pathlib import Path
from app.agents.base import BaseAgent
from app.infrastructure.parsers.excel_template import fill_template
class ExcelFillingAgent(BaseAgent):
    name="ExcelFillingAgent"
    async def run(self, template: Path, output: Path, results):
        fill_template(template, output, results); return output
