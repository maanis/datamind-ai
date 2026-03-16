# RAG Backend v3 — Python FastAPI

Multi-tenant RAG platform with metadata-first answering, Redis session caching, real-time SSE streaming, and multi-LLM support (Gemini / Ollama).

---

## What's New in v3

| Feature | v2 | v3 |
|---|---|---|
| Metadata-first answering | ❌ | ✅ Skip SQL/RAG for trivial stats |
| Session cache | MongoDB (slow) | Redis/Upstash (fast, 3-turn cache) |
| Follow-up email | Broken (lost recipients) | Fixed — recipients persisted in Redis |
| Ollama endpoint | `/api/generate` (wrong) | `/api/chat` (correct for qwen2.5:7b) |
| Email SMTP | STARTTLS broke port 465 | Fixed — `SMTP_SSL` for port 465 |
| Response shape | Inconsistent per intent | Unified JSON for all intents |
| SSE step events | Basic | Emoji-labelled (🔀🔍⚡✍️📧) |
| Metadata in queries | Not fetched | Full numeric stats fetched alongside metadataForQuery |

---

## Architecture

```
handle_query()
├── STEP 0: Greeting regex → instant reply (0ms)
├── STEP 1: Load Redis session (last 3 turns + cached SQL results)
├── STEP 2: Load workspace metadata from MongoDB (metadataForQuery + full stats)
├── STEP 3: Metadata-first check
│   └── Can MongoDB stats answer this? (row counts, min/max/avg, column lists)
│       YES → return immediately, no SQL/RAG
├── STEP 4: Planner LLM
│   ├── Call 1: Route/classify intent (lightweight — stripped metadata)
│   └── Call 2: Generate SQL (if structured/hybrid — full DDL + column samples)
├── STEP 5: Execute intent
│   ├── structured → SQL → SQLite → LLM answer
│   ├── rag        → Qdrant → reranker → LLM answer
│   ├── hybrid     → SQL + Qdrant in parallel → LLM merge
│   ├── email      → pull recipients from Redis → LLM draft → stage for approval
│   └── action     → confirm/modify/cancel pending email
├── STEP 6: Save to Redis session (last_result, last_intent, conversation)
└── STEP 7: Return unified JSON response
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
# LLM — switch between providers
LLM_PROVIDER=gemini          # or "ollama" for local dev

# Ollama (if using local dev)
OLLAMA_URL=http://localhost:11434   # base URL only, NO /api/... suffix
OLLAMA_MODEL=qwen2.5:7b

# Gemini (production)
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash

# Redis session — Upstash (free tier, no local Redis needed)
# Sign up at https://upstash.com → Create Redis → copy URL and token
UPSTASH_REDIS_URL=https://your-endpoint.upstash.io
UPSTASH_REDIS_TOKEN=your-token-here

# OR standard Redis
# REDIS_URL=redis://localhost:6379

# Email — Resend SMTP
SMTP_HOST=smtp.resend.com
SMTP_PORT=465
SMTP_USERNAME=resend
SMTP_PASSWORD=re_your_key_here
SMTP_FROM_EMAIL=noreply@yourdomain.com
SMTP_USE_SSL=true     # port 465 = direct SSL
```

### 3. Start the server

```bash
uvicorn app:main --host 0.0.0.0 --port 8000 --reload
```

### 4. (Dev) Start Ollama

```bash
ollama serve
ollama pull qwen2.5:7b
```

---

## API Endpoints

### `POST /query`

Standard query (blocking JSON response).

**Request:**
```json
{
  "workspace_id": "6999b9c216d0aac9a6c5fd26",
  "question": "Show me the top 5 employees by rating",
  "document_id": null,
  "stream": false
}
```

**Response (unified shape):**
```json
{
  "success": true,
  "intent": "structured",
  "turn_id": 3,
  "session_id": null,
  "is_multi_step": false,
  "response": {
    "message": "Here are the top 5 employees by rating...",
    "data": [{"name": "Manish Jha", "rating": 4.9}, "..."],
    "email_draft": null,
    "clarification_question": null
  },
  "meta": {
    "is_follow_up": false,
    "source_files": ["employees.csv"],
    "cache_used": false,
    "sql_used": "SELECT * FROM table_... ORDER BY rating DESC LIMIT 5",
    "chunks_used": [],
    "metadata_answered": false,
    "latency_ms": 1240
  }
}
```

### `POST /query-stream`

Streaming query with real-time pipeline events via SSE.
Called by Node.js streamController — do not call directly from frontend.

**Extra fields:**
```json
{
  "workspace_id": "...",
  "question": "...",
  "session_id": "uuid-from-node",
  "event_callback_url": "http://localhost:3000/query/stream/uuid/event"
}
```

### `POST /ingest-document`

Document ingestion (multipart/form-data).

### `DELETE /delete-document`
### `DELETE /delete-workspace`
### `DELETE /delete-all-documents`
### `POST /clear-memory`
### `GET /health`

---

## Unified Response Shape

Every intent returns the same top-level structure:

| Field | Type | Description |
|---|---|---|
| `success` | bool | Always true (errors throw HTTP 500) |
| `intent` | str | `structured \| rag \| hybrid \| email \| greeting \| clarification \| out_of_scope` |
| `turn_id` | int | Session turn counter |
| `response.message` | str | Human-readable answer |
| `response.data` | array\|null | SQL result rows (structured/hybrid) |
| `response.email_draft` | object\|null | Email draft pending approval |
| `response.clarification_question` | str\|null | Question for user |
| `meta.sql_used` | str\|null | SQL query that ran |
| `meta.cache_used` | bool | True if answered from metadata/cache |
| `meta.metadata_answered` | bool | True if answered from MongoDB stats (no SQL) |
| `meta.latency_ms` | int | Total request latency |

---

## Metadata-First Answering

For many common stat questions, the answer lives directly in MongoDB — no SQL, no Qdrant.

**Questions answered from metadata (fast path ~10ms):**
- "How many rows does employees.csv have?"
- "What columns does the orders table have?"
- "What's the average delivery time?"
- "What are the unique cities in the data?"
- "What's the max total_amount?"
- "Tell me about this dataset"

**Requires SQL (~500ms-2s):**
- "Show me employees with rating > 4.8"
- "Which restaurant has the most orders in Pune?"
- "List terminated employees hired after 2022"

---

## Redis Session

Session is scoped to `workspace_id`. Each session stores:

```json
{
  "turn_id": 4,
  "last_intent": "structured",
  "last_sql": "SELECT * FROM ... WHERE rating > 4.8",
  "last_result": [{"name": "...", "email": "...", "rating": 4.9}],
  "recipients": [{"email": "...", "name": "..."}],
  "email_templates": [{"subject": "...", "body": "..."}],
  "pending_action": "email_approval",
  "conversation": [
    {"role": "user", "content": "Show top performers"},
    {"role": "assistant", "content": "Here are 5 employees..."}
  ]
}
```

**Setting up Upstash (free, no local Redis):**
1. Go to [upstash.com](https://upstash.com)
2. Create a Redis database (free tier = 10k commands/day)
3. Copy `UPSTASH_REDIS_URL` and `UPSTASH_REDIS_TOKEN` to `.env`

If neither Upstash nor Redis is configured, the system falls back to an **in-memory dict** (works fine for single-process dev, lost on restart).

---

## LLM Configuration

### Switching providers

```env
LLM_PROVIDER=gemini   # production
LLM_PROVIDER=ollama   # local dev
```

### Ollama — critical notes

- URL must be the **base URL only**: `http://localhost:11434`
- **Do NOT** include `/api/generate` or `/api/chat` in the URL — the code adds `/api/chat` automatically
- `qwen2.5:7b` requires `/api/chat` (tool-calling capable models)
- The old `/api/generate` endpoint does not support tool calls

```bash
# Pull the model
ollama pull qwen2.5:7b

# Check it's running
curl http://localhost:11434/api/chat -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"hi"}],"stream":false}'
```

---

## Email — Resend SMTP

Port 465 uses direct SSL (`SMTP_SSL`), **not** STARTTLS.

```env
SMTP_HOST=smtp.resend.com
SMTP_PORT=465
SMTP_USERNAME=resend
SMTP_PASSWORD=re_your_resend_api_key
SMTP_FROM_EMAIL=noreply@yourdomain.com
SMTP_USE_SSL=true
SMTP_USE_TLS=false
```

**Email flow:**
1. User: "Show me all terminated employees"
2. System runs SQL → stores rows in Redis session
3. User: "Send them a termination email"
4. System pulls recipients from Redis → LLM generates draft → stages for approval
5. User: "yes" / "send"
6. System sends emails via Resend SMTP → clears pending action

---

## File Structure

```
rag_backend_v3/
├── app.py                    # FastAPI entry point
├── config.py                 # Environment config
├── models.py                 # Pydantic request/response models
├── mongo_utils.py            # MongoDB helpers
├── qdrant_utils.py           # Qdrant vector DB helpers
├── routes.py                 # Route handlers (ingestion, deletion)
├── requirements.txt
├── .env                      # Environment variables (DO NOT COMMIT)
│
├── llm/
│   ├── base.py               # BaseLLM abstract class
│   ├── factory.py            # get_llm() factory
│   ├── gemini.py             # Gemini provider
│   └── ollama.py             # Ollama provider (/api/chat)
│
├── planner/
│   └── planner.py            # 2-call planner: route_query() + generate_sql()
│
├── services/
│   ├── query_service.py      # Main query orchestrator (v3)
│   ├── redis_session.py      # Redis/Upstash session management (NEW)
│   ├── metadata_answer.py    # Metadata-first answering (NEW)
│   ├── memory.py             # Legacy MongoDB memory (kept for compatibility)
│   └── conversation_state.py # Legacy MongoDB state (kept for compatibility)
│
├── tools/
│   ├── email_tool.py         # SMTP email sender (SSL/STARTTLS fixed)
│   ├── semantic_search.py    # Qdrant vector search + reranker
│   ├── sql_executor.py       # SQLite query executor
│   ├── sql_tool.py           # SQL utilities
│   ├── refusal_handler.py    # Out-of-scope / clarification responses
│   └── clarification_tool.py # Clarification helpers
│
├── ingestion/
│   └── helpers.py            # Document parsing and ingestion pipeline
│
└── nodejs-server/            # Node.js auth + SSE bridge (see its own README)
```

---

## SSE Streaming Events

When using `/query-stream`, the frontend receives events like:

```
🧠 Loading workspace context...
📋 Checking metadata cache...
🔀 Classifying intent...
⚙️ Generating SQL query...
⚡ Executing SQL query...
⚡ SQL returned 47 rows
✍️ Generating answer...
✅ Answer ready
```

Each event:
```json
{
  "type": "step",
  "message": "⚡ SQL returned 47 rows",
  "tool": "sql_executor",
  "data": null
}
```

Final event:
```json
{
  "type": "done",
  "message": "✅ Answer ready",
  "status": "completed",
  "finalAnswer": "Here are the results...",
  "intent": "structured"
}
```
