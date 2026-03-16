# RAG Backend v3 — Node.js Server

Express.js server providing:
- JWT authentication
- Workspace management
- Bridge to Python FastAPI backend
- Real-time pipeline streaming via SSE (Server-Sent Events)

---

## Quick Start

```bash
cd nodejs-server
npm install
npm run dev      # nodemon for hot-reload
# or
npm start
```

---

## Environment Variables (`.env`)

```env
PORT=3000
MONGODB_URI=mongodb://localhost:27017/nodejs-server
JWT_SECRET=your-super-secret-jwt-key-change-this-in-production

# Python FastAPI base URL
PYTHON_API_BASE=http://localhost:8000

# This server's own base URL — sent to Python so it can POST SSE events back
# In production: https://yourapi.yourdomain.com
NODE_API_BASE=http://localhost:3000
```

---

## API Routes

### Auth (`/api/auth`)
| Method | Route | Description |
|---|---|---|
| POST | `/api/auth/register` | Register new user |
| POST | `/api/auth/login` | Login, returns JWT |

### Query (`/api/query`)
| Method | Route | Description |
|---|---|---|
| POST | `/api/query` | Standard query (blocking) |
| POST | `/api/query/stream` | Start streaming query → returns `{ sessionId }` |
| GET | `/api/query/stream/:sessionId` | SSE subscription — frontend connects here |
| POST | `/api/query/:workspaceId/memory/clear` | Clear conversation memory |

### Workspace (`/api/workspace`)
| Method | Route | Description |
|---|---|---|
| POST | `/api/workspace` | Create workspace |
| GET | `/api/workspace` | List user's workspaces |
| GET | `/api/workspace/:id` | Get workspace details |
| DELETE | `/api/workspace/:id` | Delete workspace |
| POST | `/api/workspace/:id/ingest` | Ingest document into workspace |
| DELETE | `/api/workspace/:id/documents/:docId` | Delete document |

---

## Real-Time SSE Streaming

### How it works

```
1. Frontend   →  POST /api/query/stream  →  { sessionId }
2. Frontend   →  GET  /api/query/stream/:sessionId  (SSE connection opens)
3. Node.js    →  POST /query-stream to Python  (fire-and-forget background)
4. Python     →  POST /api/query/stream/:sessionId/event  (for each pipeline step)
5. Node.js    →  appends event to MongoDB StreamEvent doc
6. Node.js SSE  →  polls MongoDB every 250ms, pushes new events to frontend
7. Python sends "done" event  →  SSE connection closes
```

### Frontend usage example

```javascript
// Step 1: Start the query
const { sessionId } = await fetch('/api/query/stream', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ question, workspaceId })
}).then(r => r.json());

// Step 2: Connect to SSE
const evtSource = new EventSource(
  `/api/query/stream/${sessionId}`,
  { headers: { Authorization: `Bearer ${token}` } }
);

evtSource.onmessage = (e) => {
  const event = JSON.parse(e.data);
  
  if (event.type === 'step') {
    // Show step to user: "⚡ SQL returned 47 rows"
    showStepIndicator(event.message);
  }
  
  if (event.type === 'done') {
    // Display the final answer
    showAnswer(event.finalAnswer);
    evtSource.close();
  }
  
  if (event.type === 'error') {
    showError(event.message);
    evtSource.close();
  }
};
```

### SSE Event Types

| type | When | Fields |
|---|---|---|
| `step` | Each pipeline step | `message`, `tool`, `data?` |
| `done` | Query complete | `finalAnswer`, `intent`, `status: "completed"` |
| `error` | Something failed | `message`, `status: "error"` |

### Step tools and their emoji

| tool | emoji | Meaning |
|---|---|---|
| `memory` | 🧠 | Loading session context |
| `router` | 🔀 | Classifying intent |
| `metadata` | 📋 | Checking metadata cache |
| `sql_generator` | ⚙️ | Generating SQL |
| `sql_executor` | ⚡ | Running SQL query |
| `semantic_search` | 🔍 | Searching documents |
| `reranker` | 📊 | Reranking chunks |
| `llm` | ✍️ | Generating answer |
| `email` | 📧 | Email operations |
| `cache` | ⚡ | Cache hit |
| `system` | 💬 | General |

---

## Query Response Shape

All responses from `/api/query` follow the unified shape:

```json
{
  "success": true,
  "intent": "structured",
  "turn_id": 3,
  "session_id": null,
  "is_multi_step": false,
  "response": {
    "message": "Here are the results...",
    "data": [{}, {}],
    "email_draft": null,
    "clarification_question": null
  },
  "meta": {
    "is_follow_up": false,
    "source_files": ["employees.csv"],
    "cache_used": false,
    "sql_used": "SELECT ...",
    "chunks_used": [],
    "metadata_answered": false,
    "latency_ms": 1240
  }
}
```

**Email draft shape** (when `intent === "email"`):
```json
{
  "response": {
    "message": "I've prepared an email draft for 5 recipients...",
    "email_draft": {
      "subject": "Congratulations on Your Promotion!",
      "body": "Dear {{name}},\n\nWe are pleased...",
      "recipients": [{"email": "...", "name": "..."}],
      "editable": true
    }
  }
}
```

---

## File Structure

```
nodejs-server/
├── server.js
├── package.json
├── .env
│
├── routes/
│   ├── auth.js
│   ├── query.js          # /api/query + /api/query/stream/*
│   ├── stream.js         # SSE route definitions
│   ├── users.js
│   └── workspace.js
│
├── controllers/
│   ├── authController.js
│   ├── queryController.js     # handleQuery, clearMemory
│   ├── streamController.js    # initiateStreamQuery, subscribeToStream, appendStreamEvent
│   ├── userController.js
│   └── workspaceController.js
│
├── middlewares/
│   └── auth.js           # JWT verification middleware
│
└── models/
    ├── User.js
    ├── Workspace.js
    ├── Document.js
    ├── IngestionJob.js
    ├── StreamEvent.js    # TTL-indexed event store for SSE (auto-deleted after 1hr)
    └── Usage.js
```

---

## StreamEvent MongoDB Model

SSE events are stored in MongoDB and TTL-auto-deleted after 1 hour.

```js
{
  sessionId: "uuid",           // unique per query
  workspaceId: ObjectId,       // workspace isolation
  userId: ObjectId,            // user isolation
  events: [
    { type: "step", message: "⚡ SQL returned 5 rows", tool: "sql_executor", timestamp }
  ],
  status: "active|completed|error",
  finalAnswer: "Here are the results...",
  intent: "structured",
  expiresAt: Date              // TTL index — MongoDB auto-deletes
}
```

---

## Production Notes

1. **NODE_API_BASE** must be set to your public domain so Python can POST events back:
   ```env
   NODE_API_BASE=https://api.yourapp.com
   ```

2. **SSE through Nginx** — add this to your Nginx config to prevent buffering:
   ```nginx
   location /api/query/stream {
     proxy_buffering off;
     proxy_cache off;
     proxy_set_header X-Accel-Buffering no;
   }
   ```

3. **Rate limiting** — adjust the limiter in `server.js` (currently 100 req/15min per IP).

4. **JWT secret** — change `JWT_SECRET` to a strong random value in production.
