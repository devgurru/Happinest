from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import engine
from app.api import sessions, conversation, reference, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — tables managed by Alembic migrations, not create_all
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Happinest — backend-controlled AI wedding planning consultant",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(sessions.router, prefix="/api/v2")
app.include_router(conversation.router, prefix="/api/v2")
app.include_router(reference.router, prefix="/api/v2")
app.include_router(admin.router, prefix="/api/v2")


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "llmProvider": settings.llm_provider,
        "model": settings.active_chat_model,
        "embeddingProvider": settings.EMBEDDING_PROVIDER,
        "embeddingModel": settings.OLLAMA_EMBEDDING_MODEL,
    }
