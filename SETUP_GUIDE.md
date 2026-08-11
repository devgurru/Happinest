# Happinest Backend — New Machine Setup Guide

> Set up PostgreSQL, run migrations, seed reference data, and start the API on a fresh machine.  
> For API testing after setup, see [TESTING_GUIDE.md](./TESTING_GUIDE.md).

---

## What you need

| Tool           | Version   | Purpose                                    |
| -------------- | --------- | ------------------------------------------ |
| **Python**     | 3.12+     | FastAPI app                                |
| **PostgreSQL** | 14+       | Database                                   |
| **pgvector**   | extension | Event-site embeddings (direction matching) |

---

## 1. Clone the repo

```bash
git clone <your-repo-url> wedding-theme-recomendation # check your directory accordingly
cd wedding-theme-recomendation-2/backend
```

All commands below assume you are in the **`backend/`** directory (where `alembic.ini` lives).

---

## 2. Install PostgreSQL + pgvector

### Ubuntu / Debian (example)

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
# pgvector package name varies by Postgres version, e.g.:
sudo apt install -y postgresql-16-pgvector
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### macOS (Homebrew example)

```bash
brew install postgresql@16 pgvector
brew services start postgresql@16
```

---

## 3. Create database and user

Adjust username/password/db name if you prefer something other than `root` / `wedding_ai_db`.

```bash
sudo -u postgres psql
```

Inside `psql`:

```sql
-- Skip CREATE USER if you already have a Postgres login
CREATE USER root WITH PASSWORD 'root' CREATEDB;

CREATE DATABASE wedding_ai_db OWNER root;

\c wedding_ai_db

-- Required for event_sites.embedding column
CREATE EXTENSION IF NOT EXISTS vector;

\q
```

**Verify:**

```bash
psql "postgresql://root:root@localhost:5432/wedding_ai_db" -c "\dx"
# Should list "vector" extension
```

---

Verify api keys in .env before checking health

## 4. Python virtual environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Restart uvicorn, then: `curl http://localhost:8000/health`

---

## 5. Environment file

```bash
cp .env.example .env
```

Edit `.env` if your Postgres credentials differ and update api keys:

| Variable       | Notes                                                        |
| -------------- | ------------------------------------------------------------ |
| `DATABASE_URL` | Must use `postgresql+asyncpg://` (not plain `postgresql://`) |
| `DEBUG=true`   | Enables `/api/v2/admin/*` seed & embed endpoints             |

---

## 6. Run database migrations

**Important:** run Alembic from **`backend/`** (same folder as `alembic.ini`).

```bash
cd backend   # if not already there

# Apply all migrations (creates tables)
alembic upgrade head
```

**Check status:**

```bash
alembic current          # shows latest applied revision
alembic history          # lists all migrations
```

**Existing migrations in this repo:**

| Revision        | File                                       | What it adds                                               |
| --------------- | ------------------------------------------ | ---------------------------------------------------------- |
| `ba7e52c55a34`  | `20260710_0548_..._happinest_schema_v1.py` | Full v2 schema (sessions, memory, event_sites, vendors, …) |
| `20260713_1218` | `20260713_1218_add_message_metadata.py`    | Message `metadata_json` / selectedChips                    |

**On a new machine you only run `upgrade head`** — do **not** run `revision --autogenerate` unless you changed SQLAlchemy models.

### If migration fails on `vector` type

Enable the extension manually, then retry:

```bash
psql "postgresql://root:root@localhost:5432/wedding_ai_db" -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head
```

---

## 7. Seed reference data

Loads **15 event sites** and **12 vendors** (idempotent — safe to re-run).

```bash
python -m app.seeds.seed_runner
```

Expected output:

```
Running seed loader...
  EventSites: 15 inserted, 0 skipped
  Vendors:    12 inserted, 0 skipped
Seed complete.
```

Or via API (server must be running, `DEBUG=true`):

```bash
curl -X POST http://localhost:8000/api/v2/admin/seed
```

---

## 8. Generate event-site embeddings (for S6 directions)

Embeddings power “show me directions” (fast pgvector match). Run once after seeding:

**Option A — API (server running):**

```bash
curl -X POST http://localhost:8000/api/v2/admin/embed-sites
```

---

## 9. Start the API

```bash
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 10. Verify setup

| Check              | Command / URL                                                                 |
| ------------------ | ----------------------------------------------------------------------------- |
| Health             | `curl http://localhost:8000/health`                                           |
| Swagger            | http://localhost:8000/docs                                                    |
| Tables exist       | `psql ... -c "\dt"`                                                           |
| Event sites seeded | `psql ... -c "SELECT count(*) FROM event_sites;"`                             |
| Embeddings present | `psql ... -c "SELECT count(*) FROM event_sites WHERE embedding IS NOT NULL;"` |

## Common commands (daily dev)

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload

# After pulling new code with migrations
alembic upgrade head

# Re-seed (skips existing slugs)
python -m app.seeds.seed_runner

#always run this endpoint after runner seeder to create data embeddings
curl -X POST http://localhost:8000/api/v2/admin/embed-sites

---
```
