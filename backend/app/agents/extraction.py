import asyncio, re, json
from app.agents.base import BaseAgent
from app.domain.extraction import ExtractionResult
from app.infrastructure.llm.openai_client import chat_json
class ExtractionAgent(BaseAgent):
    name="ExtractionAgent"
    def regex_extract(self, criterion, text):
        low=criterion.lower()
        if "номер" in low:
            m=re.search(r"(?:договор[а-я\s]*№|№)\s*([\wА-Яа-я./()\-]+)", text, re.I); return m.group(1) if m else None
        if "дата" in low:
            m=re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s+[а-яА-Я]+\s+\d{4})", text); return m.group(1) if m else None
        return None
    async def run_one(self, criterion, fragments):
        context="\n".join(f.text for f in fragments)[:12000]
        value=self.regex_extract(criterion, context)
        if not value:
            try:
                data=await chat_json("Извлеки значение критерия из договора. Верни JSON.", json.dumps({"criterion":criterion,"context":context}, ensure_ascii=False))
                value=str(data.get("value") or "").strip()
                conf=float(data.get("confidence") or 0.55)
            except Exception:
                value=""; conf=0.25
        else: conf=0.75
        if not value: value="Не найдено в договоре. Требует ручной проверки."
        return ExtractionResult(criterion=criterion, value=value, normalized_value=value, confidence=conf, source_fragments=fragments[:3], reasoning_summary="Значение извлечено из релевантных фрагментов договора.")
    async def run(self, retrievals):
        return await asyncio.gather(*(self.run_one(c, f) for c,f in retrievals.items()))
