from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db"

    # ─── LLM provider switch ──────────────────────────────────────────────────
    # "ollama" = local Gemma via Ollama
    # "grok"   = xAI Grok cloud API (console.x.ai) — keys usually xai-...
    # "groq"   = Groq cloud API (console.groq.com) — keys usually gsk_...
    LLM_PROVIDER: str = "ollama"

    # Shared LLM request knobs (apply to whichever provider is active)
    LLM_TIMEOUT_SECONDS: float = 120.0
    LLM_MAX_RETRIES: int = 1

    # Ollama — local chat (used when LLM_PROVIDER=ollama)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma3:latest"
    # Backward-compat aliases (still honored if set in older .env files)
    OLLAMA_TIMEOUT_SECONDS: float | None = None
    OLLAMA_MAX_RETRIES: int | None = None

    # Grok / xAI — cloud chat (used when LLM_PROVIDER=grok)
    GROK_API_KEY: str = ""
    GROK_BASE_URL: str = "https://api.x.ai/v1"
    GROK_MODEL: str = "grok-3-mini"

    # Groq — cloud chat (used when LLM_PROVIDER=groq)
    # Keys from https://console.groq.com/ start with gsk_
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    # Vision model — used for image analysis (multimodal)
    GROQ_VISION_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # Embeddings — keep local Ollama by default (pgvector dims = nomic-embed-text)
    # Direction matching depends on this; do not switch casually.
    EMBEDDING_PROVIDER: str = "ollama"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"

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
        provider = (self.LLM_PROVIDER or "ollama").strip().lower()
        if provider in ("xai", "x-ai"):
            return "grok"
        if provider not in ("ollama", "grok", "groq"):
            return "ollama"
        return provider

    @property
    def llm_timeout_seconds(self) -> float:
        if self.OLLAMA_TIMEOUT_SECONDS is not None:
            return float(self.OLLAMA_TIMEOUT_SECONDS)
        return float(self.LLM_TIMEOUT_SECONDS)

    @property
    def llm_max_retries(self) -> int:
        if self.OLLAMA_MAX_RETRIES is not None:
            return int(self.OLLAMA_MAX_RETRIES)
        return int(self.LLM_MAX_RETRIES)

    @property
    def active_chat_model(self) -> str:
        if self.llm_provider == "grok":
            return self.GROK_MODEL
        if self.llm_provider == "groq":
            return self.GROQ_MODEL
        return self.OLLAMA_MODEL

    @property
    def active_vision_model(self) -> str:
        """Model used for image analysis (multimodal). Groq only for now."""
        if self.llm_provider == "groq":
            return self.GROQ_VISION_MODEL
        # Ollama vision model if local — assumes llava or gemma3 vision variant
        return self.OLLAMA_MODEL


settings = Settings()
