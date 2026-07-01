import asyncio
from openai import AsyncOpenAI
from app.core.config import settings
_client=AsyncOpenAI(base_url=settings.LITELLM_BASE_URL, api_key=settings.LITELLM_API_KEY)
async def embed_texts(texts: list[str]) -> list[list[float]]:
    resp=await asyncio.wait_for(_client.embeddings.create(model=settings.EMBEDDING_MODEL, input=texts), timeout=settings.LLM_TIMEOUT_SECONDS)
    return [d.embedding for d in resp.data]
def cosine(a,b):
    import math
    return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)) or 1)
