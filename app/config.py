from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db"

    # Ollama — main LLM
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "gemma3:latest"
    OLLAMA_TIMEOUT_SECONDS: float = 120.0
    OLLAMA_MAX_RETRIES: int = 1

    # Ollama — embeddings
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
    )


settings = Settings()
