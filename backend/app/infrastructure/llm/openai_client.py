import asyncio
import json
from typing import Any

from openai import APIStatusError, AsyncOpenAI, AuthenticationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings

_client = AsyncOpenAI(base_url=settings.LITELLM_BASE_URL, api_key=settings.LITELLM_API_KEY)
_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_REQUESTS)
_llm_disabled = settings.LITELLM_API_KEY in {"", "change_me"}


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, AuthenticationError):
        return False
    if isinstance(exc, APIStatusError) and exc.status_code in {401, 403}:
        return False
    return True


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def chat_json(system: str, user: str) -> dict[str, Any]:
    global _llm_disabled
    if _llm_disabled:
        raise RuntimeError("LLM is disabled because API key is missing or was rejected")
    try:
        async with _semaphore:
            resp = await asyncio.wait_for(
                _client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"},
                    temperature=0,
                ),
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
    except (AuthenticationError, APIStatusError) as exc:
        if isinstance(exc, AuthenticationError) or getattr(exc, "status_code", None) in {401, 403}:
            _llm_disabled = True
        raise
    txt = resp.choices[0].message.content or "{}"
    return json.loads(txt)
