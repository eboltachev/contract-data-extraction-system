from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    LITELLM_BASE_URL: str = "https://models.aicorex.tech/v1"
    LITELLM_API_KEY: str = "change_me"
    LLM_MODEL: str = "openai/gpt-oss-120b"
    EMBEDDING_MODEL: str = "nomic-ai/nomic-embed-text-v2-moe"
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    AGENT_MAX_ITERATIONS: int = 3
    MAX_CONCURRENT_LLM_REQUESTS: int = 8
    MAX_UPLOAD_SIZE_MB: int = 50
    JOB_STORAGE_DIR: str = "/storage/jobs"
    LLM_TIMEOUT_SECONDS: int = 120
    STAGE_TIMEOUT_SECONDS: int = 900
    EXTRACTION_TIMEOUT_SECONDS: int = 900
    SSE_HEARTBEAT_SECONDS: int = 10
settings = Settings()
