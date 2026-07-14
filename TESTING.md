# Wedding AI Backend — API Testing Guide

> **Purpose**: Step-by-step flow to test the entire backend from scratch.
> **Tools**: curl (terminal) or Swagger UI at http://localhost:8000/docs
> **Prerequisite**: Server must be running — `uvicorn app.main:app --reload`

---

## 🧪 Testing Strategy

```
Step 1: Health Check
    ↓
Step 2: Create User
    ↓
Step 3: Create Conversation
    ↓
Step 4: Send Opening Message
    ↓
Step 5: Continue Conversation (multiple turns)
    ↓
Step 6: Check Wedding Profile (auto-updated)
    ↓
Step 7: Resume Conversation (simulate app restart)
    ↓
Step 8: Check Completion Percentage
```

---

## Step 1 — Health Check

Confirm the server and Ollama are both connected.

```bash
curl http://localhost:8000/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "app": "Wedding AI Backend",
  "version": "1.0.0",
  "model": "gemma3:latest"
}
```

✅ Pass if `status` is `"ok"` and `model` is `"gemma3:latest"`
❌ Fail if you get `connection refused` → server not running

---

## Step 2 — Create a User

```bash
curl -s -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Arslan",
    "email": "arslan@test.com"
  }' | python3 -m json.tool
```

**Expected Response:**
```json
{
  "id": 1,
  "name": "Arslan",
  "email": "arslan@test.com",
  "created_at": "2026-07-08T..."
}
```

📌 **Save the `id`** — you'll use it as `user_id` in the next step.

---

## Step 3 — Create a Conversation

```bash
curl -s -X POST http://localhost:8000/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1
  }' | python3 -m json.tool
```

**Expected Response:**
```json
{
  "id": 1,
  "user_id": 1,
  "title": null,
  "status": "active",
  "created_at": "2026-07-08T...",
  "updated_at": "2026-07-08T..."
}
```

📌 **Save the `id`** — this is your `conversation_id` for all chat calls.

---

## Step 4 — Send First Message

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "Hi, I am Arslan and my fiancée is Ayesha"
  }' | python3 -m json.tool
```

**Expected Response:**
```json
{
  "conversation_id": 1,
  "response": "How wonderful, Arslan! Congratulations to you and Ayesha...",
  "profile_updates": {
    "couple": {
      "groom": "Arslan",
      "bride": "Ayesha"
    }
  },
  "completion_percentage": 16.7
}
```

✅ Verify:
- `response` is a natural, warm AI message
- `profile_updates` contains bride/groom names
- `completion_percentage` > 0

> ⏱️ **First response is slow** (5–30 sec) — Gemma3 cold starts on CPU. Subsequent ones are faster.

---

## Step 5 — Continue the Conversation

Send follow-up messages one at a time. After each one, verify the `profile_updates` field captures the new information.

### Message 2 — City
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "We are planning the wedding in Lahore"
  }' | python3 -m json.tool
```
✅ `profile_updates` should contain `"city": "Lahore"`

---

### Message 3 — Guest Count
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "We expect around 300 guests"
  }' | python3 -m json.tool
```
✅ `profile_updates` should contain `"guest_count": 300`

---

### Message 4 — Budget
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "Our budget is around 15 to 20 lakh rupees"
  }' | python3 -m json.tool
```
✅ `profile_updates` should contain a `"budget"` field

---

### Message 5 — Style
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "We want a traditional Pakistani wedding with royal feel"
  }' | python3 -m json.tool
```
✅ `profile_updates` should contain `"style"` and possibly `"decor_theme"`

---

### Message 6 — Events
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "We will have Mehndi, Baraat, and Walima"
  }' | python3 -m json.tool
```
✅ `profile_updates` should contain `"events": ["Mehndi", "Baraat", "Walima"]`

---

## Step 6 — Check the Wedding Profile

After several messages, check the accumulated profile:

```bash
curl -s http://localhost:8000/profile/1 | python3 -m json.tool
```

**Expected Response:**
```json
{
  "id": 1,
  "conversation_id": 1,
  "profile_json": {
    "couple": {
      "bride": "Ayesha",
      "groom": "Arslan"
    },
    "city": "Lahore",
    "venue": null,
    "wedding_date": null,
    "guest_count": 300,
    "budget": "15-20 lakh",
    "preferred_colors": [],
    "style": "Traditional Royal",
    "events": ["Mehndi", "Baraat", "Walima"],
    "catering_preference": null,
    ...
  },
  "completion_percentage": 50.0,
  "updated_at": "2026-07-08T..."
}
```

✅ Verify `profile_json` is being filled incrementally
✅ Verify `completion_percentage` grows with each turn

---

## Step 7 — View Message History

Check all stored messages in the conversation:

```bash
curl -s http://localhost:8000/conversations/1/messages | python3 -m json.tool
```

**Expected**: A list of alternating `user` and `assistant` messages in chronological order.

```json
[
  { "role": "user", "content": "Hi, I am Arslan..." },
  { "role": "assistant", "content": "How wonderful, Arslan!..." },
  { "role": "user", "content": "We are planning the wedding in Lahore" },
  { "role": "assistant", "content": "Lahore is a beautiful city!..." },
  ...
]
```

---

## Step 8 — Test Conversation Resume

Simulate a user returning to the app after closing it.

```bash
# Send a new message without re-creating anything
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 1,
    "message": "I am back, let us continue"
  }' | python3 -m json.tool
```

✅ The AI should remember Arslan, Ayesha, Lahore, etc. from previous messages
✅ It should continue asking about missing fields (not repeat what's already collected)

---

## Step 9 — Test Duplicate User (Error Handling)

```bash
curl -s -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Arslan Duplicate",
    "email": "arslan@test.com"
  }' | python3 -m json.tool
```

**Expected:** `409 Conflict`
```json
{
  "detail": "Email already registered"
}
```

---

## Step 10 — Test Invalid Conversation (Error Handling)

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": 9999,
    "message": "Hello"
  }' | python3 -m json.tool
```

**Expected:** `404 Not Found`
```json
{
  "detail": "Conversation not found"
}
```

---

## 🌐 Using Swagger UI (Alternative to curl)

Open http://localhost:8000/docs in your browser.

**Order to follow in Swagger:**
1. `POST /users` → create user, copy `id`
2. `POST /conversations` → paste `user_id`, copy conversation `id`
3. `POST /chat` → paste `conversation_id`, type your message
4. `GET /profile/{conversation_id}` → check the profile after each chat

Swagger lets you see the full request/response without writing curl commands.

---

## ✅ Test Checklist

| Test | Status |
|------|--------|
| `/health` returns `ok` with `gemma3:latest` | ⬜ |
| Create user → returns `id: 1` | ⬜ |
| Create conversation → returns `id: 1` | ⬜ |
| First chat message → AI responds naturally | ⬜ |
| `profile_updates` extracts bride/groom names | ⬜ |
| `completion_percentage` increases per turn | ⬜ |
| Profile endpoint shows accumulated data | ⬜ |
| Message history shows alternating roles | ⬜ |
| Resume conversation — AI remembers context | ⬜ |
| Duplicate email → `409 Conflict` | ⬜ |
| Bad conversation ID → `404 Not Found` | ⬜ |

---

## 🐌 Performance Notes (CPU-only mode)

Since Gemma3 runs on CPU (no GPU detected):

| Turn | Expected response time |
|------|----------------------|
| First (cold start) | 15–40 seconds |
| Subsequent turns | 5–20 seconds |
| Extraction call | 3–10 seconds |

This is normal for CPU inference. Response time depends on your RAM and CPU speed.

---

## 📋 Quick Reset (Start Fresh)

If you want to wipe data and start over:

```bash
# Drop and recreate the DB
sudo -u postgres psql -c "DROP DATABASE wedding_ai_db;"
sudo -u postgres psql -c "CREATE DATABASE wedding_ai_db OWNER root;"

# Re-run migrations
source venv/bin/activate
alembic upgrade head
```
