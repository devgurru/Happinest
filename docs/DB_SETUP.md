# Database Setup

Happinest needs **PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension**
(the `event_sites.embedding` column is `vector(768)`). The app reads its connection
string from `DATABASE_URL` in `.env`; the default the code expects is:

```
DATABASE_URL=postgresql+asyncpg://root:root@localhost:5432/wedding_ai_db
```

So the target is a database `wedding_ai_db` owned by a role `root` (password `root`),
reachable on `localhost:5432`, with the `vector` extension enabled.

> ⚠️ **The migration does not create the pgvector extension.** `happinest_schema_v1`
> defines a `VECTOR(768)` column but assumes the extension already exists. You must run
> `CREATE EXTENSION vector;` **before** `alembic upgrade head`, or the migration fails with
> `type "vector" does not exist`.

Pick **one** of the two setups below, then do the [common steps](#common-steps-schema--seed--run).

---

## Option A — Native PostgreSQL (Ubuntu/Debian)

```bash
sudo apt install postgresql-16 postgresql-16-pgvector
```

Create the role, database, and extension (peer auth → run as the `postgres` OS user):

```bash
sudo -u postgres psql -c "CREATE ROLE root WITH LOGIN SUPERUSER PASSWORD 'root';"
sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"
sudo -u postgres psql -d wedding_ai_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Manage the server with `sudo pg_ctlcluster 16 main start|stop|restart` (it also auto-starts on boot).

### If the cluster came up on 5433 instead of 5432

`pg_createcluster` (run automatically on install) picks the next free port, so if something
else held 5432 at install time, the cluster lands on **5433**. Check with `pg_lsclusters`.
Free 5432 (stop whatever holds it), then move the cluster and restart:

```bash
sudo pg_conftool 16 main set port 5432
sudo pg_ctlcluster 16 main restart
```

(Or leave it on 5433 and set `DATABASE_URL=...@localhost:5433/wedding_ai_db` instead.)

---

## Option B — Docker (no sudo, self-contained)

Requires an image with pgvector baked in. `pg16` or `pg17` both work:

```bash
docker run -d --name happinest-postgres \
  -e POSTGRES_USER=root -e POSTGRES_PASSWORD=root -e POSTGRES_DB=wedding_ai_db \
  -p 5432:5432 -v happinest_pgdata:/var/lib/postgresql/data \
  --restart unless-stopped \
  pgvector/pgvector:pg16

# wait until ready, then enable the extension
until docker exec happinest-postgres pg_isready -U root >/dev/null 2>&1; do sleep 1; done
docker exec happinest-postgres psql -U root -d wedding_ai_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Lifecycle: `docker start|stop happinest-postgres`. Data persists in the `happinest_pgdata`
volume. Remove entirely with `docker rm -f happinest-postgres && docker volume rm happinest_pgdata`.

> The container publishes `0.0.0.0:5432`. If you later install native Postgres, it will grab
> 5432 during the container's lifetime — stop the container first (see the 5433 note above).

---

## Common steps — schema + seed + run

From the repo root, with the virtualenv active:

```bash
alembic upgrade head              # create tables (extension must already exist)
python -m app.seeds.seed_runner   # seed 15 event sites + 12 vendors (idempotent)
uvicorn app.main:app --reload     # http://localhost:8000/docs
```

### Verify

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/v2/reference/event-sites | head   # expect 15 sites
curl -s -X POST http://localhost:8000/api/v2/sessions \
  -H 'Content-Type: application/json' \
  -d '{"clientName":"Aisha","partnerName":"Rohan"}'                  # expect 201
```

### Embeddings (needed for S6 direction matching)

Seeded event sites have **no** embedding vectors yet, so pgvector search returns nothing until
you generate them. This needs Ollama running the `nomic-embed-text` model (768-dim, matching the
column). With `DEBUG=true`:

```bash
curl -X POST http://localhost:8000/api/v2/admin/embed-sites
```

Not required for stages S1–S5.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Migration: `type "vector" does not exist` | Run `CREATE EXTENSION vector;` in `wedding_ai_db`, then re-run `alembic upgrade head`. |
| App request 500, `asyncpg ... ConnectionRefused` | No server on 5432. Start it (`pg_ctlcluster 16 main start` / `docker start happinest-postgres`). The app boots without a DB and only fails on the first request that touches it. |
| `role "root" does not exist` | Create it — see Option A. |
| `database "wedding_ai_db" does not exist` | `sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"` |
| Cluster on 5433, app expects 5432 | Move the cluster (see 5433 note) or point `DATABASE_URL` at 5433. |
| DBeaver SSL error | Set `sslmode=disable` (Driver properties) — dev servers here have no TLS; the app connects in plaintext too. |

## Reset (wipe and start over)

```bash
# native
sudo -u postgres psql -c "DROP DATABASE wedding_ai_db;"
sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"
sudo -u postgres psql -d wedding_ai_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head && python -m app.seeds.seed_runner

# docker
docker rm -f happinest-postgres && docker volume rm happinest_pgdata
# then re-run the Option B block
```
