from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Railway/Heroku give postgresql:// — this app uses async SQLAlchemy + asyncpg."""
    if not url or url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        return normalize_database_url(v)

    # ─── LLM provider switch ──────────────────────────────────────────────────
    # "grok"   = xAI Grok cloud API (console.x.ai) — keys usually xai-...
    # "groq"   = Groq cloud API (console.groq.com) — keys usually gsk_...
    # "openai" = OpenAI API (platform.openai.com)
    LLM_PROVIDER: str = "openai"

    # Shared LLM request knobs (apply to whichever provider is active)
    LLM_TIMEOUT_SECONDS: float = 120.0
    LLM_MAX_RETRIES: int = 1

    # Grok / xAI — cloud chat (used when LLM_PROVIDER=grok)
    GROK_API_KEY: str = ""
    GROK_BASE_URL: str = "https://api.x.ai/v1"
    GROK_MODEL: str = "grok-3-mini"

    # Groq — cloud chat (used when LLM_PROVIDER=groq)
    # Keys from https://console.groq.com/ start with gsk_
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "qwen/qwen3.6-27b"
    # Vision model — used for image analysis (multimodal)
    GROQ_VISION_MODEL: str = "qwen/qwen3.6-27b"

    # OpenAI — cloud chat + embeddings
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_VISION_MODEL: str = "gpt-4o"

    # Embeddings — uses OpenAI text-embedding-3-small
    EMBEDDING_PROVIDER: str = "openai"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # App
    DEBUG: bool = False
    APP_NAME: str = "Happinest Wedding Planner"
    APP_VERSION: str = "2.0.0"

    # Conversation
    MAX_HISTORY_MESSAGES: int = 20

    # Logging
    AI_LOG_PROMPTS: bool = False  # Set True in dev to log full prompts

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def llm_provider(self) -> str:
        provider = (self.LLM_PROVIDER or "openai").strip().lower()
        if provider in ("xai", "x-ai"):
            return "grok"
        if provider not in ("grok", "groq", "openai"):
            return "openai"
        return provider

    @property
    def llm_timeout_seconds(self) -> float:
        return float(self.LLM_TIMEOUT_SECONDS)

    @property
    def llm_max_retries(self) -> int:
        return int(self.LLM_MAX_RETRIES)

    @property
    def active_chat_model(self) -> str:
        if self.llm_provider == "grok":
            return self.GROK_MODEL
        if self.llm_provider == "groq":
            return self.GROQ_MODEL
        return self.OPENAI_MODEL

    @property
    def active_vision_model(self) -> str:
        """Model used for image analysis (multimodal). Groq/OpenAI/Grok."""
        if self.llm_provider == "groq":
            return self.GROQ_VISION_MODEL
        if self.llm_provider == "grok":
            return self.GROK_MODEL
        return self.OPENAI_VISION_MODEL


settings = Settings()
