from app.core.config import settings
class BaseAgent:
    name="BaseAgent"
    def __init__(self): self.max_iterations=settings.AGENT_MAX_ITERATIONS
    async def bounded(self, func, fallback):
        for _ in range(self.max_iterations):
            result=await func()
            if result: return result
        return fallback
