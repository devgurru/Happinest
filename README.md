# Wedding AI — Backend

FastAPI + PostgreSQL + Ollama AI-powered wedding planning consultant.

## Stack
- **FastAPI** — async REST API
- **SQLAlchemy (async)** + **Alembic** — ORM & migrations
- **PostgreSQL** — persistent storage
- **Ollama (Gemma 3)** — local LLM for conversation

## Setup

```bash
# From the backend/ directory:
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Environment

Copy `.env.example` → `.env` and fill in your values:

```env
DATABASE_URL=postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:latest
DEBUG=true
```

## Run

```bash
# Always run from inside the backend/ directory
cd backend
source venv/bin/activate
uvicorn app.main:app --reload
```

API docs available at → **http://localhost:8000/docs**

## Database Migrations

```bash
cd backend
alembic upgrade head          # apply all migrations
alembic revision --autogenerate -m "describe change"  # create new migration
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/users` | Create user |
| `GET`  | `/users/{id}` | Get user |
| `POST` | `/conversations/` | Create conversation |
| `GET`  | `/conversations/{id}` | Get conversation |
| `GET`  | `/conversations/{id}/messages` | Get messages |
| `POST` | `/chat` | Send message, get AI reply |
| `GET`  | `/profile/{conversation_id}` | Get wedding profile |
| `GET`  | `/health` | Health check |
