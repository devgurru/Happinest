# Wedding AI Backend — Developer Guide & Progress Tracker

> **Status**: 🚧 Milestone 1 — In Progress
> **Last Updated**: 2026-07-08
> **LLM Model**: Gemma3 (already installed via Ollama)
> **Database**: PostgreSQL — user: `root` / pass: `root`

---

## 📁 Project Structure

```
wedding-theme-recomendation-2/
├── app/
│   ├── api/
│   │   ├── chat.py              # POST /chat, POST /users, GET /profile
│   │   └── conversations.py     # POST|GET /conversations, GET messages
│   ├── services/
│   │   ├── llm_service.py       # Ollama API calls (chat + extraction)
│   │   ├── profile_service.py   # JSON merge + completion score
│   │   └── conversation_service.py  # Full chat pipeline orchestration
│   ├── models/
│   │   ├── user.py              # SQLAlchemy User model
│   │   ├── conversation.py      # SQLAlchemy Conversation model
│   │   ├── message.py           # SQLAlchemy Message model
│   │   └── wedding_profile.py   # SQLAlchemy WeddingProfile (JSONB) model
│   ├── prompts/
│   │   ├── system_prompt.txt    # Main consultant AI prompt (EDITABLE)
│   │   └── extraction_prompt.txt # Profile extraction prompt (EDITABLE)
│   ├── schemas/
│   │   └── chat.py              # Pydantic request/response schemas
│   ├── database/
│   │   └── database.py          # AsyncEngine + session factory
│   ├── utils/                   # Shared utilities (future)
│   ├── config.py                # Pydantic settings (reads .env)
│   └── main.py                  # FastAPI app entry point
├── migrations/
│   ├── env.py                   # Alembic async environment
│   └── versions/                # Auto-generated migration files
├── .env.example                 # Environment variable template
├── .env                         # Your local config (never commit this)
├── requirements.txt
├── DEVELOPMENT.md               # This file
└── TESTING.md                   # API test flow guide
```

---

## 🧰 Tech Stack

| Component       | Technology               |
|----------------|--------------------------|
| Language        | Python 3.12+             |
| API Framework   | FastAPI                  |
| ORM             | SQLAlchemy 2.0 (async)   |
| Migrations      | Alembic                  |
| Database        | PostgreSQL               |
| DB Driver       | asyncpg                  |
| Validation      | Pydantic v2              |
| LLM Runtime     | Ollama                   |
| **LLM Model**   | **Gemma3 (gemma3:latest)** |
| HTTP Client     | httpx (async)            |

> ℹ️ **Why Gemma3?** Originally planned as Qwen3:4b, but Ollama's registry was unreachable
> due to an SSL/network issue. Gemma3 (4.3B, Q4_K_M) was already installed and is
> equally capable for conversational survey tasks.

---

## 🚀 Setup Guide (One-Time)

### Prerequisites
- Python 3.12+
- PostgreSQL running locally (user: `root`, pass: `root`)
- Ollama installed with `gemma3:latest` ✅ already done

### Step 1 — Confirm Ollama is Running

Ollama runs as a systemd service. Verify:
```bash
systemctl status ollama          # should say: active (running)
curl http://127.0.0.1:11434/api/tags  # should list gemma3:latest
```

### Step 2 — Create the Database

```bash
sudo -u postgres psql
```
Inside psql:
```sql
CREATE DATABASE wedding_ai_db OWNER root;
\q
```

> **Note**: We're using the existing `root` PostgreSQL user (password: `root`).
> No need to create a new user.

### Step 3 — Python Environment

```bash
cd "Practice self owned/wedding-theme-recomendation-2"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 4 — Configure Environment

```bash
cp .env.example .env
```

`.env` contents (should already be correct):
```
DATABASE_URL=postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma3:latest
DEBUG=true
MAX_HISTORY_MESSAGES=20
```

### Step 5 — Run Database Migrations

```bash
source venv/bin/activate

# First time only — generate migration from models
alembic revision --autogenerate -m "initial_tables"

# Apply migration
alembic upgrade head
```

### Step 6 — Start the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 7 — Verify

| Check | URL |
|-------|-----|
| Health endpoint | http://localhost:8000/health |
| Swagger UI (interactive docs) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

---

## 🔌 API Reference

| Method | Endpoint                            | Description                      |
|--------|-------------------------------------|----------------------------------|
| POST   | `/users`                            | Create new user                  |
| GET    | `/users/{id}`                       | Get user by ID                   |
| POST   | `/conversations`                    | Start a new conversation         |
| GET    | `/conversations/{id}`               | Get conversation details         |
| GET    | `/conversations/{id}/messages`      | Get message history              |
| POST   | `/chat`                             | Send message to AI consultant    |
| GET    | `/profile/{conversation_id}`        | Get current wedding profile      |
| GET    | `/health`                           | Health check                     |

---

## 🧠 AI Pipeline (per message)

```
User Message
    ↓ Save to DB (role=user)
    ↓ Load last 20 messages as history
    ↓ Load wedding profile JSON
    ↓ Build prompt = system_prompt + profile + history + new message
    ↓ Send to Ollama (gemma3:latest) → get assistant response
    ↓ Save to DB (role=assistant)
    ↓ Second Ollama call → extract structured fields as JSON
    ↓ Merge extracted fields into profile JSON
    ↓ Recalculate completion percentage
    ↓ Return: response + profile_updates + completion %
```

---

## 📋 Survey Topics (Covered Naturally by AI)

The AI consultant persona **"Aiza"** covers these topics in a natural conversation:

1. Bride and groom names
2. Wedding city / location
3. Date or season
4. Expected number of guests
5. Budget range
6. Venue preference (outdoor/indoor/hotel/farmhouse)
7. Wedding style (traditional/modern/rustic/royal)
8. Events planned (Nikkah/Baraat/Walima)
9. Preferred colors
10. Catering preferences
11. Music/entertainment
12. Photography/videography

> 📌 Edit `app/prompts/system_prompt.txt` to tune topics, tone, or question flow.

---

## 🗃️ Database Schema

### `users`
| Column     | Type        | Notes          |
|------------|-------------|----------------|
| id         | INT PK      | Auto increment |
| name       | VARCHAR     |                |
| email      | VARCHAR     | Unique         |
| created_at | TIMESTAMPTZ | Auto           |

### `conversations`
| Column     | Type        | Notes                     |
|------------|-------------|---------------------------|
| id         | INT PK      |                           |
| user_id    | INT FK      | → users.id                |
| title      | VARCHAR     | Optional                  |
| status     | ENUM        | active/completed/archived |
| created_at | TIMESTAMPTZ |                           |
| updated_at | TIMESTAMPTZ | Auto-updated              |

### `messages`
| Column          | Type        | Notes                  |
|-----------------|-------------|------------------------|
| id              | INT PK      |                        |
| conversation_id | INT FK      | → conversations.id     |
| role            | ENUM        | user/assistant/system  |
| content         | TEXT        |                        |
| created_at      | TIMESTAMPTZ |                        |

### `wedding_profiles`
| Column               | Type        | Notes                        |
|----------------------|-------------|------------------------------|
| id                   | INT PK      |                              |
| conversation_id      | INT FK      | One-to-one with conversation |
| profile_json         | JSONB       | Full wedding profile         |
| completion_percentage| FLOAT       | 0.0 – 100.0                  |
| updated_at           | TIMESTAMPTZ |                              |

---

## 📊 Milestone Tracker

### ✅ Milestone 1: Core AI Consultant

| Task | Status |
|------|--------|
| Project structure scaffold | ✅ Done |
| FastAPI app with lifespan management | ✅ Done |
| Async PostgreSQL + SQLAlchemy 2.0 | ✅ Done |
| Database models (User, Conversation, Message, WeddingProfile) | ✅ Done |
| Chat pipeline (save → history → LLM → extract → merge) | ✅ Done |
| System prompt — wedding consultant persona "Aiza" | ✅ Done |
| Extraction prompt — structured JSON from conversation | ✅ Done |
| Profile deep-merge logic | ✅ Done |
| Completion percentage calculator | ✅ Done |
| REST API endpoints | ✅ Done |
| Alembic migrations setup | ✅ Done |
| Switched model to Gemma3 (already installed) | ✅ Done |
| DB credentials updated (root/root) | ✅ Done |
| Create `wedding_ai_db` PostgreSQL database | ⏳ You do this |
| Run `pip install -r requirements.txt` | ⏳ You do this |
| Run Alembic migration | ⏳ You do this |
| Start server + hit `/health` | ⏳ You do this |
| End-to-end chat test via Swagger | ⬜ Pending |
| Survey flow tuning (your 5–6 custom questions) | ⬜ Next step |

### 🔜 Milestone 2: Frontend Integration
- [ ] Simple chat UI
- [ ] Conversation list / resume
- [ ] Profile completion widget

### 🔜 Milestone 3: Theme Generation
- [ ] Embeddings + vector search
- [ ] Theme recommendation engine
- [ ] Image generation

---

## 🔧 Common Commands

```bash
# Activate virtualenv
source venv/bin/activate

# Run dev server
uvicorn app.main:app --reload

# After changing any SQLAlchemy model
alembic revision --autogenerate -m "describe_what_changed"
alembic upgrade head

# Check DB tables
sudo -u postgres psql -d wedding_ai_db -c "\dt"

# Check Ollama model
ollama list
curl http://127.0.0.1:11434/api/tags
```

---

## 🐛 Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` on DB | `sudo systemctl start postgresql` |
| `role "root" does not exist` | Create it: `sudo -u postgres psql -c "CREATE ROLE root WITH LOGIN PASSWORD 'root' SUPERUSER;"` |
| `database "wedding_ai_db" does not exist` | `sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"` |
| Ollama `connection refused` | `systemctl start ollama` |
| `asyncpg` import error | `source venv/bin/activate` then `pip install -r requirements.txt` |
| Alembic `ModuleNotFoundError` | Run from project root with venv active |
| Gemma3 not responding | `curl http://127.0.0.1:11434/api/tags` — verify it shows `gemma3:latest` |

---

## 📝 Development Notes

- **Prompt tuning**: Edit `app/prompts/system_prompt.txt` to control AI behavior
- **Extraction failures**: Returns `{}` on bad JSON — never breaks the chat
- **Profile is JSONB**: Flexible schema, no migration needed for new profile fields
- **History window**: Change `MAX_HISTORY_MESSAGES` in `.env`
- **No auth yet**: User ID is in request body for M1; JWT planned for M2
- **Model**: Gemma3:latest (4.3B, Q4_K_M quantization) — runs CPU-only fine for dev


# 1. Create the database (one time)
sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"

# 2. Setup Python
cd "Practice self owned/wedding-theme-recomendation-2"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 3. Run migrations
alembic revision --autogenerate -m "initial_tables"
alembic upgrade head

# 4. Start server
uvicorn app.main:app --reload
