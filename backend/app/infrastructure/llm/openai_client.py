import asyncio, json
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from app.core.config import settings
_client=AsyncOpenAI(base_url=settings.LITELLM_BASE_URL, api_key=settings.LITELLM_API_KEY)
_semaphore=asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_REQUESTS)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def chat_json(system: str, user: str) -> dict:
    async with _semaphore:
        resp=await asyncio.wait_for(_client.chat.completions.create(model=settings.LLM_MODEL, messages=[{"role":"system","content":system},{"role":"user","content":user}], response_format={"type":"json_object"}, temperature=0), timeout=settings.LLM_TIMEOUT_SECONDS)
    txt=resp.choices[0].message.content or "{}"
    return json.loads(txt)
