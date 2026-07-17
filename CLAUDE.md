# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Happinest is a FastAPI backend for an AI wedding-planning consultant. The guiding principle, repeated throughout the code, is **"Backend controls all decisions. AI supports."** The conversation is a fixed 11-stage machine (S1–S11); the LLM only *proposes* memory changes and stage moves, and the backend policy layer validates, overrides, and owns the final decision. The LLM output is treated as untrusted and passes through a sanitize → validate → policy pipeline before it can affect anything.

Stack: FastAPI (async) · SQLAlchemy 2.0 async + asyncpg · PostgreSQL + **pgvector** · Alembic · Pydantic v2 · pluggable LLM provider (local Ollama/Gemma by default, or xAI Grok / Groq cloud).

> ⚠️ **`README.md`, `DEVELOPMENT.md`, and `TESTING.md` are stale and describe a removed v1 app** (a `/chat` + `/users` + `/profile` API with `User`/`Conversation`/`Message`/`WeddingProfile` models). None of that exists anymore. The current app is the v2 session-based planner described below, mounted under `/api/v2`. `README.md` also says to `cd backend` and references `../SETUP_GUIDE.md` — there is no `backend/` subdir and no `SETUP_GUIDE.md`; **the repo root is the app root.** Trust the code, not those docs.

## Commands

Run everything from the **repository root** (the `app/` package lives here), with the virtualenv active.

> **Database setup** — native Postgres or Docker, the pgvector extension, seeding, and troubleshooting: see [docs/DB_SETUP.md](docs/DB_SETUP.md).

```bash
# One-time setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # then edit as needed

# Database — requires PostgreSQL with the pgvector extension.
# The v1 migration defines a VECTOR(768) column but does NOT create the extension,
# so enable it once before migrating:
#   psql <db> -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head
python -m app.seeds.seed_runner           # seed event_sites + vendors (idempotent)

# Run the API (from repo root)
uvicorn app.main:app --reload             # docs at http://localhost:8000/docs

# Migrations
alembic revision --autogenerate -m "describe change"
alembic upgrade head

# Embeddings — REQUIRED for S6 direction matching. Sites are seeded without vectors;
# generate them via the admin endpoint (DEBUG=true only):
curl -X POST http://localhost:8000/api/v2/admin/embed-sites
```

Ollama must be running for chat **and** embeddings under the default config (`LLM_PROVIDER=ollama`, `EMBEDDING_PROVIDER=ollama`, `nomic-embed-text` = 768 dims, matching the `Vector(768)` column). Switching `LLM_PROVIDER` to `grok`/`groq` changes only chat; embeddings stay on Ollama unless you deliberately change the seeded vector dimensions.

**There are no automated tests and no configured linter/formatter** (no `pytest`, `tests/`, `ruff`, `pyproject.toml`, etc.). `TESTING.md` is a manual curl walkthrough — and it targets the removed v1 API, so don't follow it verbatim. Verify changes by hand against the `/api/v2` routes.

## API surface (all under `/api/v2`)

- `POST /sessions` — S1: create session from `{clientName, partnerName}`. **No AI call**; seeds identity memory, auto-advances to S2, returns the welcome reply.
- `GET /sessions` · `GET /sessions/{id}` · `GET /sessions/{id}/messages` · `GET /sessions/{id}/memory`
- `POST /sessions/{id}/turn` — **the main endpoint.** Body carries `eventType`: `conversation_turn` (needs `message`) or `synthesis_request` (needs/infers `synthesisType`). `draft_update` is frontend-only and is rejected with 400.
- `GET /reference/event-sites` · `/reference/vendors` (+ `/{id}`) — seeded catalog.
- `POST /admin/seed` · `POST /admin/embed-sites` — **DEBUG-only** (403 otherwise).

**API contract is camelCase in both directions.** The Pydantic schemas in `app/schemas/planner.py` literally name their fields `requestId`, `plannerReply`, `stageDecision`, `memoryPatch`, etc. Database columns and internal Python are snake_case; the orchestrator returns dicts with camelCase keys that map straight onto the response models.

## The turn pipeline (`app/services/orchestrator.py`)

`process_conversation_turn` is the heart of the system. Read it alongside `stage_policy.py` before changing conversation behavior. Rough flow:

1. Load session (backend owns `current_stage` — the client cannot drive stage) and the latest memory version.
2. **LLM Call 1 — intent** (`build_turn_intent_prompt`): classifies the message as `normal` / `gibberish` / `help` / `more_suggestions` / `correction` (+ target sections). Failure falls through as `normal`.
3. **LLM Call 2 — conversation turn** (`build_conversation_turn_prompt`): produces `plannerReply` + `memoryPatch` + a proposed `stageDecision`, using stage rules tailored to the intent.
4. `sanitize_ai_response` cleans the raw dict, then intent-based overrides force empty patches / correct decision types for gibberish/help/more_suggestions/correction.
5. `validate_ai_response` — reject-on-bad-shape (never raises); a failure returns a standard error response and **memory is never mutated on error**.
6. Apply the patch via `MemoryService.apply_patch`, detect upstream corrections (`correction_policy`), then let `StagePolicy.resolve_final_decision_with_memory` decide the *actual* stage move. Backend completion checks can override an AI "advance" (and vice-versa).
7. `align_planner_reply` rewrites the copy to match the **final** stage (the LLM was prompted on the *current* stage, but policy may have advanced it after). `build_ui_suggestions` assembles chips for the target stage.
8. Persist client + planner messages, log the turn (`observability.log_ai_turn` → `ai_turn_logs`).

Auto-chaining: completing S4 (vibe) auto-runs **brief** synthesis into S5; at S5 "show me directions" (or `synthesis_request`) runs **direction** synthesis into S6; corrections at S5/S6 regenerate the brief/directions with a change-acknowledgment.

## Stage machine (`app/services/stage_policy.py` + `app/domain/enums.py`)

`stage_policy.py` is the **single source of truth** for stage behavior; `STAGE_CONFIG` holds each stage's goal / accept-reject rules / advance condition, and the file's header documents exactly how to modify a stage. Stages, allowed transitions, and which stages need AI live in `enums.py` (`StageId`, `ALLOWED_TRANSITIONS`, `AI_REQUIRED_STAGES`, `SYNTHESIS_STAGES`).

- **S1 names, S5 brief, S6 directions, S11 summary** are *not* AI conversation stages — S1 is system-handled, and S5/S6/S11 advance via `synthesis_request`, not `conversation_turn`.
- `is_stage_complete(stage, memory)` holds the deterministic backend gates that decide when a stage may actually advance — the LLM's "advance" is ignored for gated stages (S2, S3, S4, S6–S10) unless the memory check passes.
- Decision types: `stay`, `advance` (next stage only), `reanchor` (stay + reframe after a correction), `jump` (only backward), `request_clarification`. `validate_transition` enforces these.

## Memory model — append-only + patches (`app/services/memory_service.py`, `app/domain/memory_schema.py`)

Canonical planner memory is one JSONB blob whose shape is `DEFAULT_PLANNER_MEMORY` (identity / occasion / personality / vibe / brief / direction / logistics / summary / earlySignals / committedSelections / …). It is **versioned and append-only**: every patch writes a new `session_memory_versions` row (never updates in place) plus a `session_memory_patches` audit row. `apply_patch` uses `deep_merge` where **lists in a patch replace** (not extend) base lists, and skips `None`/empty values.

Editing an upstream section **invalidates downstream artifacts** via `_INVALIDATION_MAP` (e.g. changing `occasion` marks brief/direction/budget/vendors/summary stale). `staleSections` is tracked both on the version row and inside the memory blob.

`memory_schema.py` also builds the frontend projections returned every turn: `build_selected_chips` (UI chip restore) and `build_planner_notes_view` (left-rail summary). `resolve_primary_vibe` reconciles canonical vibe vs. committed chips vs. pool labels — used widely to decide S4 completeness.

## AI gateway & providers (`app/services/ai_gateway.py`)

All model calls go through `call_llm(messages, stage, event_type) -> (parsed_dict, telemetry)`. It selects the provider from `settings.llm_provider` (`ollama` local, or OpenAI-compatible `grok`/`groq`), retries per `LLM_MAX_RETRIES`, and **never fabricates a fallback reply** — on failure it raises `AIGatewayError(code, message, http_status)`, which the orchestrator turns into an error response. Prompts use an assistant-prefill of `{` and `_extract_json` does brace-matched JSON extraction tolerant of think-blocks, markdown fences, and trailing prose. `ResponseSource.OPENAI` is defined as `settings.llm_provider`, so the "AI" source label reflects whatever provider is active — don't read it as literally OpenAI.

## Embeddings & synthesis (`app/services/embedding_service.py`)

S6 direction matching is embedding-driven, not LLM-driven, so it's fast and doesn't block on a slow model: `find_matching_event_sites` embeds a flattened memory string and runs pgvector cosine search (`<=>`) over `event_sites.embedding`, falling back to insertion order if no vectors exist. Direction reasons come from curated `event_site.profile_json`, not the model. Brief and final-summary synthesis *do* call the LLM (`build_brief_synthesis_prompt`, `build_final_summary_prompt`); `_execute_synthesis` in the orchestrator runs all three and writes a `generated_artifacts` row.

## Data model notes

Tables (all keyed by UUID): `sessions`, `session_messages`, `session_memory_versions`, `session_memory_patches`, `session_stage_history`, `generated_artifacts`, `session_event_site_recommendations`, `session_vendor_recommendations`, `event_sites` (has the `Vector(768)` embedding + JSONB `profile_json`), `vendors`, `ai_turn_logs`. Everything hangs off a session; memory versions/patches/stage-history/artifacts/recommendations are the append-only trail of a planning conversation. Import the SQLAlchemy `Base` only from `app/models/base.py`.

## Prompts

Editable prompt templates live in `app/prompts/*.txt` and are `string.Template`-substituted by `prompt_builder.py`. Active: `conversation_turn.txt`, `turn_intent.txt`, `brief_synthesis.txt`, `final_summary.txt`. `direction_synthesis.txt` exists and has a builder but the live S6 path is embedding-only, so it is effectively unused. To tune conversational behavior, edit these templates and/or the rule blocks in `stage_policy.py` (the prompt injects `STAGE_CONFIG` rules) — not the Python string literals in the orchestrator.
