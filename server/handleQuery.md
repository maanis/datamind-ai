# RAG Backend v4 — Query Execution Architecture

> **Design Philosophy**: State Machine Architecture (not chatbot)  
> LLM only reasons. System controls all flow.

---

## Table of Contents

1. [Entry Points](#1-entry-points)
2. [Query Processing Pipeline](#2-query-processing-pipeline)
3. [Intent Detection](#3-intent-detection)
4. [Planner System](#4-planner-system)
5. [Execution Engine](#5-execution-engine)
6. [Memory System](#6-memory-system)
7. [Workspace Data Model](#7-workspace-data-model)
8. [RAG Pipeline](#8-rag-pipeline)
9. [SQL Query Generation](#9-sql-query-generation)
10. [Tool System](#10-tool-system)
11. [Performance Bottlenecks](#11-performance-bottlenecks)
12. [Execution Graph](#12-execution-graph)

---

## 1. Entry Points

### `/query` (POST) — `app.py:60-78`

Standard query endpoint supporting JSON response or streaming.

```python
@app.post("/query")
def query_endpoint(req: QueryRequest):
    service = QueryService()
    result = service.handle_query(
        workspace_id=req.workspace_id,
        question=req.question,
        document_id=req.document_id,
        stream=req.stream
    )
    return result
```

### `/query-stream` (POST) — `app.py:81-104`

SSE streaming endpoint for real-time pipeline events via Node.js callback.

### Input Schema (`models.py`)

```python
class QueryRequest(BaseModel):
    workspace_id: str
    question: str
    document_id: Optional[str] = None
    stream: bool = False
    session_id: Optional[str] = None
    event_callback_url: Optional[str] = None
```

---

## 2. Query Processing Pipeline

### Step-by-Step Flow

| Step | Action | Location | LLM Call? |
|------|--------|----------|-----------|
| 0 | Greeting Fast-Path | `query_service.py:290-293` | No |
| 0.5 | Cached Results Check | `query_service.py:295-306` | No |
| 1 | Load Workflow State | `query_service.py:308-311` | No |
| 2 | Get Workspace Metadata | `query_service.py:313-316` | No |
| 3 | Metadata Fast-Path | `query_service.py:318-326` | No |
| 4 | Memory Facts + Follow-up Context | `query_service.py:328-344` | No |
| 5 | Fast Router (Intent Classification) | `query_service.py:346-362` | **LLM #1** |
| 6 | Workflow Collision Guard | `query_service.py:364-368` | No |
| 7 | Plan Query | `query_service.py:386-421` | No |
| 8 | Execute Steps | `query_service.py:386-421` | **LLM #2, #3** |
| 9 | Persist Facts | `query_service.py:423-476` | No |
| 10 | Build Response | `query_service.py:478-492` | No |

### Pipeline Visualization

```
User Query
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 0: Greeting Fast-Path (regex, 0ms)                       │
│         _GREETING_RE matches → immediate response             │
└───────────────────────────────────────────────────────────────┘
    ↓ (not greeting)
┌───────────────────────────────────────────────────────────────┐
│ Step 0.5: Cached Results Check                                │
│           is_explicit_followup() + _check_answer_in_cached_results() │
│           Answer in cache → immediate response                │
└───────────────────────────────────────────────────────────────┘
    ↓ (not cached)
┌───────────────────────────────────────────────────────────────┐
│ Step 1: Load Workflow State (Redis)                           │
│         get_workflow() → {active, type, next_step, outputs}  │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 2: Get Workspace Metadata                                │
│         get_workspace_metadata_for_query() from MongoDB       │
│         Filter by document_id if provided                     │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 3: Metadata Fast-Path                                    │
│         try_metadata_answer() → row_count, column_count, etc  │
│         Stats questions → immediate response                  │
└───────────────────────────────────────────────────────────────┘
    ↓ (not metadata-answerable)
┌───────────────────────────────────────────────────────────────┐
│ Step 4: Memory + Follow-up Context                            │
│         retrieve_relevant_facts() → Mem0 top_k=3 facts        │
│         get_followup_context() → previous query/SQL/entities  │
│         Combined context ≈ 80 tokens                          │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 5: Fast Router (LLM Call #1)                             │
│         fast_route() → RouterDecision                         │
│         Intent: structured | rag | hybrid | email | etc       │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 6: Workflow Collision Guard                              │
│         new_workflow_needed=True → cancel_workflow()          │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 7: Plan Query                                            │
│         plan_query() → execution plan with steps              │
│         build_execution_batches() → DAG batching              │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 8: Execute Steps (parallel within batch)                 │
│         _execute_step() routes by intent:                     │
│         → structured: planner_decision() + execute_sql()      │
│         → rag: semantic_search() + LLM answer                 │
│         → email: _run_email() → draft for approval            │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 9: Persist Facts                                         │
│         store_turn_facts() → Mem0 (async best-effort)         │
│         save_last_turn() → Redis structured turn data         │
└───────────────────────────────────────────────────────────────┘
    ↓
┌───────────────────────────────────────────────────────────────┐
│ Step 10: Build Response                                       │
│          _single() or _multi() response builder               │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. Intent Detection

### Valid Intents

| Intent | Description | Example |
|--------|-------------|---------|
| `structured` | SQL/tabular data queries | "How many employees have salary > 50000?" |
| `rag` | Unstructured document search | "What does the policy say about refunds?" |
| `hybrid` | Needs both SQL + RAG | "Summarize the performance of top 10 employees" |
| `email` | Draft/send email | "Send them a notification email" |
| `metadata` | Answered from dataset stats | "How many rows in employees.csv?" |
| `greeting` | Small talk | "Hi", "Hello" |
| `clarification` | Genuinely ambiguous | "Show me the data" (which dataset?) |
| `out_of_scope` | No relevant document | Random unrelated question |

### Heuristic Pre-checks (Skip LLM)

```python
# Greeting detection - router/fast_router.py
_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|howdy|sup|yo|good\s*(morning|afternoon|evening|night)|..."
)

# Workflow reply detection (confirmation words)
_WORKFLOW_REPLY_RE = re.compile(
    r"^(yes|ok|okay|sure|yep|yeah|send|proceed|confirm|go ahead)..."
)

# Follow-up pronoun detection - planner/multi_step_planner.py
_FOLLOWUP_RE = re.compile(
    r'\b(him|her|his|he|she|they|them|those|these|the results?|the data|...)
)
```

### Router Decision Schema

```python
@dataclass
class RouterDecision:
    intent: str              # structured | rag | hybrid | email | metadata | greeting | clarification
    is_continuation: bool    # True = resume active workflow
    new_workflow_needed: bool # True = cancel active wf, start fresh
    rewritten_query: str     # clean standalone question
    confidence: float        # 0.0 - 1.0
```

### Router Prompt Critical Rules

1. **Dataset Matching**: Assume dataset if filename/column mentioned
2. **Intent Selection**: structured if storage_mode=sqlite AND involves data retrieval
3. **Clarification**: ONLY when genuinely ambiguous (multiple datasets with same column)
4. **Follow-up Handling**: Use previous column context, mark `is_follow_up=true`

---

## 4. Planner System

### Two-Level Planning

**Level 1: Fast Router** (`router/fast_router.py`)
- Lightweight intent classification
- Workflow continuation detection
- ~200 tokens output

**Level 2: Multi-Step Planner** (`planner/multi_step_planner.py`)
- Generates execution plan with multiple steps
- Handles complex queries requiring DAG execution

### Step Schema

```python
{
  "is_multi_step": bool,
  "steps": [
    {
      "step_id": int,
      "intent": "structured | rag | hybrid | email | metadata",
      "query": str,           # Standalone question for this step
      "depends_on": [int],    # Step IDs that must complete first
      "cache_key": str,       # For result caching ("high_performers")
      "uses_cache_key": str,  # For email: which cache to pull from
      "followup_sql_context": str  # Previous query column context
    }
  ]
}
```

### DAG Execution Batching

```python
def build_execution_batches(steps):
    """
    Topological sort steps into parallel execution batches.
    
    Batch 1: steps with depends_on = []      → run in parallel
    Batch 2: steps whose deps are all done   → run in parallel
    ...
    """
```

**Example:**
```
Query: "Get top performers AND bottom performers, then email both groups"

Batch 1 (parallel):
  - Step 1: Get top performers (structured)
  - Step 2: Get bottom performers (structured)

Batch 2 (depends on 1,2):
  - Step 3: Email top performers (uses_cache_key: "top_performers")
  - Step 4: Email bottom performers (uses_cache_key: "bottom_performers")
```

---

## 5. Execution Engine

### Step Executor (`query_service.py:549-577`)

```python
def _execute_step(self, step, workspace_id, document_id, metadata_list, emitter, completed):
    intent = step["intent"]
    
    if intent == "metadata":
        return try_metadata_answer(query, metadata_list)
    
    if intent in ("structured", "hybrid"):
        return self._run_structured(...)
    
    if intent == "rag":
        return self._run_rag(...)
    
    if intent == "email":
        return self._run_email(...)
```

### Structured Flow (`_run_structured`)

```
1. planner_decision()        # LLM Call #2 - SQL generation
       ↓
2. validate_sql()            # Security check (SELECT only)
       ↓
3. execute_sql()             # Run against SQLite
       ↓
4. _structured_answer()      # LLM Call #3 - Natural language answer
       ↓
5. store_cache_in_workflow() # Cache results for email step
```

### RAG Flow (`_run_rag`)

```
1. semantic_search()         # Dense + BM25 + RRF + Reranker
       ↓
2. format_search_results()   # Build context (max 2000 chars)
       ↓
3. LLM.generate()            # LLM Call - Answer generation
```

### Email Flow (`_run_email`)

```
1. Find recipients           # From cache/completed steps/workflow
       ↓
2. _gen_email_draft()        # LLM generates subject + body
       ↓
3. set_email_pending()       # Set pending action for approval
       ↓
4. Return draft              # User must confirm before send
```

---

## 6. Memory System

### Two-Layer Architecture (`services/memory_service.py`)

```
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 1: REDIS                           │
│                  (Short-term, TTL 30 min)                   │
├─────────────────────────────────────────────────────────────┤
│  • Workflow state (active, type, next_step)                 │
│  • Step outputs (recipients, email_draft, sql_result)       │
│  • Cached rows (cache_keys)                                 │
│  • Pending action (email_approval)                          │
│  • Last turn data (query, SQL, entities, result_preview)    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   LAYER 2: MEM0 + QDRANT                    │
│                   (Long-term Semantic)                      │
├─────────────────────────────────────────────────────────────┤
│  • Extracted facts after each turn                          │
│  • Query-matched retrieval (top_k=3)                        │
│  • ~80 tokens max injected into planner prompt              │
│  • NOT raw conversation text                                │
└─────────────────────────────────────────────────────────────┘
```

### Workflow State Schema

```python
{
  "type": "email_workflow | data_query",
  "active": bool,
  "created_at": timestamp,
  "next_step": "get_recipients | draft_email | confirm_email | send_email | done",
  "outputs": {
    "recipients": [...],
    "email_draft": {"subject": ..., "body": ...},
    "sql_result": {"rows": [...], "columns": [...], "sql": "..."},
    "cache_keys": {"key_name": {...}}
  },
  "pending_action": "email_approval | null",
  "turn_id": int
}
```

### Key Memory Functions

| Function | Purpose |
|----------|---------|
| `get_workflow()` | Load Redis workflow state |
| `save_workflow()` | Persist workflow state |
| `start_workflow()` | Cancel active & start fresh |
| `advance_workflow()` | Move to next step |
| `complete_workflow()` | Mark workflow done |
| `store_cache_in_workflow()` | Cache SQL results |
| `store_turn_facts()` | Store facts in Mem0 |
| `retrieve_relevant_facts()` | Semantic fact retrieval (top_k=3) |
| `save_last_turn()` | Store structured turn data |
| `get_followup_context()` | Build context for pronoun resolution |

---

## 7. Workspace Data Model

### MongoDB Collections

**Workspace:**
```javascript
{
  _id: ObjectId,
  userId: ObjectId,
  name: string,
  sqlitePath: string,      // Path to SQLite database
  vectorCount: number,
  documentCount: number
}
```

**Document:**
```javascript
{
  _id: ObjectId,
  workspaceId: ObjectId,
  fileName: string,
  status: "pending" | "processing" | "completed" | "failed",
  storageMode: "rag" | "sqlite" | "hybrid",
  tableName: string,       // For structured data
  vectorCount: number,
  metadata: {...},
  metadataForQuery: {
    document_id: string,
    file_name: string,
    storageMode: string,
    summary: string,       // LLM-generated summary
    keywords: [string],    // LLM-generated keywords
    columns: [string],     // Column names for structured
    tableName: string
  }
}
```

### Storage Mode Decision

| Mode | Criteria | Vector Storage | SQLite Storage |
|------|----------|----------------|----------------|
| `rag` | Unstructured (PDF, TXT) | ✅ Full content | ❌ |
| `sqlite` | Large tabular (>1000 rows) | ✅ Schema only | ✅ Full data |
| `hybrid` | Small-medium tabular | ✅ Full content | ✅ Full data |

---

## 8. RAG Pipeline

### Hybrid Search Architecture (`qdrant_utils.py`)

```
Query
  ↓
┌─────────────────┐     ┌─────────────────┐
│ Dense Search    │     │ BM25 Sparse     │
│ (MiniLM-L6-v2)  │     │ (Custom encoder)│
│ Semantic match  │     │ Keyword match   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     ↓
            ┌────────────────┐
            │ RRF Fusion     │
            │ score = 1/(k+rank_dense) + 1/(k+rank_sparse)
            └────────┬───────┘
                     ↓
            ┌────────────────┐
            │ Flashrank      │
            │ Reranker       │
            │ (cross-encoder)│
            └────────┬───────┘
                     ↓
              Top K Results
```

### Why Hybrid?

| Method | Strengths | Weaknesses |
|--------|-----------|------------|
| Dense-only | Finds "topically close" chunks | Misses exact keyword matches |
| BM25-only | Finds exact terms | Misses semantic meaning |
| **Hybrid+RRF** | Best of both worlds | Slightly slower |
| **+Reranker** | Picks truly relevant results | +50-100ms |

### Hierarchical Chunking

```python
# Each point stores:
{
  "text": child_chunk,      # Small, precise — used for MATCHING
  "parent_text": parent_chunk  # Larger context — returned to LLM
}

# We RETRIEVE on small chunks (precise match)
# but RETURN parent chunk to LLM (full context)
```

---

## 9. SQL Query Generation

### Two-Call Flow (`planner/planner.py`)

**Call 1: Router** — Lightweight intent classification
- Input: Stripped metadata (no DDL, no column samples)
- Output: `{intent, rewritten_query, confidence}`

**Call 2: SQL Generator** — Full SQL generation
- Input: Rich metadata (live DDL, column samples, schema sample)
- Output: Raw SQL or `{ambiguous: true, options: [...]}`

### Metadata Enrichment (`get_rich_metadata_for_sql`)

```python
def get_rich_metadata_for_sql(metadata_entry, workspace_id):
    # 1. Get live DDL from SQLite
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?")
    
    # 2. Get column types
    cursor.execute('PRAGMA table_info("table_name")')
    
    # 3. Get sample rows (3-5)
    cursor.execute('SELECT * FROM "table_name" LIMIT 5')
    
    # 4. Get column value samples (categorical columns)
    cursor.execute('SELECT DISTINCT "col" FROM "table" LIMIT 20')
    
    return {
        "table_ddl": "CREATE TABLE ...",
        "columns_with_types": [{"name": "col", "type": "TEXT"}],
        "schema_sample": [{"col1": "val1", ...}],
        "column_value_samples": {"status": ["active", "inactive"]}
    }
```

### SQL Prompt Rules

```
IMPORTANT TEXT SEARCH RULES:
- When searching for names/text values → ALWAYS use LIKE '%search_term%'
- NEVER use exact equality (=) for name/person searches
- Example: WHERE "Party_2" LIKE '%vikash kumar%' (NOT = 'vikash kumar')
```

### SQL Security Validation

```python
def validate_sql(sql):
    # Must start with SELECT
    # Blocked: DROP, DELETE, UPDATE, INSERT, ALTER, CREATE, TRUNCATE
    # No multiple statements (;)
    # No system table access
    # No dangerous functions
```

---

## 10. Tool System

### Available Tools

| Tool | File | Purpose |
|------|------|---------|
| **Semantic Search** | `tools/semantic_search.py` | Hybrid dense+BM25+reranker search |
| **SQL Executor** | `tools/sql_executor.py` | Validated SQL execution |
| **SQL Generator** | `tools/sql_tool.py` | LLM-based SQL generation |
| **Refusal Handler** | `tools/refusal_handler.py` | Polite out-of-scope responses |
| **Email Tool** | `tools/email_tool.py` | Email drafting and sending |
| **Clarification Tool** | `tools/clarification_tool.py` | Generate clarification questions |

### Tool Selection Logic

```
Router determines intent
    ↓
Planner generates steps
    ↓
Executor calls appropriate tool

Note: NO dynamic tool selection by LLM
      System-controlled routing only
```

### Semantic Search Tool

```python
def semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    document_id: Optional[str] = None,
    use_reranker: bool = True
) -> List[Dict[str, Any]]:
    """
    1. Generate dense embedding (MiniLM-L6-v2)
    2. Dense search (semantic similarity)
    3. BM25 sparse search (keyword match)
    4. RRF fusion (merge results)
    5. Flashrank reranking (cross-encoder)
    """
```

### SQL Executor Tool

```python
@dataclass
class SQLResult:
    success: bool
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    truncated: bool
    error: Optional[str] = None
```

---

## 11. Performance Bottlenecks

### Identified Issues

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| **Multiple LLM Calls** | Structured: 3 calls (router + SQL + answer) | ~3-5s latency | ⚠️ Consider combining |
| **Mem0 Init Delay** | First query triggers Mem0 init | ~2-3s cold start | ✅ Fixed: `init_mem0()` at startup |
| **Rich Metadata Fetch** | `get_rich_metadata_for_sql()` queries SQLite | ~100-200ms | ⚠️ Cache DDL per workspace |
| **BM25 Refit** | `encoder.fit()` on every upsert | O(n) per document | ⚠️ Pre-fit or incremental |
| **Reranker Load** | First search loads Flashrank | ~1-2s cold start | ⚠️ Pre-load at startup |
| **No SQL Result Cache** | Same SQL query re-executed | Repeated latency | ⚠️ Cache recent results |
| **Gemini Rate Limits** | API returns 503 on overload | Query failure | ✅ Fixed: Retry with backoff |

### LLM Call Analysis

**Structured Query (worst case):**
```
LLM Call #1: Fast Router (intent classification)     ~500-1000ms
LLM Call #2: SQL Generator (generate SQL)            ~500-1000ms
LLM Call #3: Answer Generator (natural language)     ~500-1000ms
───────────────────────────────────────────────────────────────
Total LLM time:                                      ~1500-3000ms
```

**Optimization Opportunities:**
1. Combine router + SQL generation into single call
2. Cache SQL results for identical queries
3. Pre-compute metadata for frequently queried tables

---

## 12. Execution Graph

### Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER QUERY                                      │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                      ┌───────────▼───────────┐
                      │   Greeting Check      │ ← regex, 0ms
                      │   (/_GREETING_RE/)    │
                      └───────────┬───────────┘
                                  │ not greeting
                      ┌───────────▼───────────┐
                      │  Cached Answer Check  │ ← is_explicit_followup()
                      │  (follow-up shortcut) │
                      └───────────┬───────────┘
                                  │ not cached
          ┌───────────────────────┴───────────────────────┐
          │                                               │
┌─────────▼─────────┐                         ┌───────────▼───────────┐
│  Redis Workflow   │                         │  MongoDB Metadata     │
│  (get_workflow)   │                         │  (metadataForQuery)   │
└─────────┬─────────┘                         └───────────┬───────────┘
          │                                               │
          └───────────────────────┬───────────────────────┘
                                  │
                      ┌───────────▼───────────┐
                      │   Metadata Fast-Path  │ ← try_metadata_answer()
                      │   (row_count, etc)    │
                      └───────────┬───────────┘
                                  │ not metadata
          ┌───────────────────────┴───────────────────────┐
          │                                               │
┌─────────▼─────────┐                         ┌───────────▼───────────┐
│   Mem0 Facts      │                         │  Follow-up Context    │
│   (top_k=3)       │                         │  (last_turn data)     │
└─────────┬─────────┘                         └───────────┬───────────┘
          │                                               │
          └───────────────────────┬───────────────────────┘
                                  │
                      ┌───────────▼───────────┐
                      │    FAST ROUTER        │ ← LLM Call #1
                      │    (intent + rewrite) │   ~500-1000ms
                      └───────────┬───────────┘
                                  │
                      ┌───────────▼───────────┐
                      │   MULTI-STEP PLANNER  │ ← plan_query()
                      │   (DAG generation)    │
                      └───────────┬───────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
      ┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼───────┐
      │   STEP 1      │   │   STEP 2      │   │   STEP N      │
      │   (parallel)  │   │   (parallel)  │   │   (depends)   │
      └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
              │                   │                   │
              │ intent=structured │ intent=rag        │ intent=email
              │                   │                   │
      ┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼───────┐
      │ SQL Generator │   │ Semantic      │   │ Email Draft   │
      │ (LLM Call #2) │   │ Search        │   │ Generator     │
      └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
              │                   │                   │
      ┌───────▼───────┐   ┌───────▼───────┐          │
      │ SQL Executor  │   │ Hybrid Search │          │
      │ (SQLite)      │   │ Dense+BM25+RRF│          │
      └───────┬───────┘   └───────┬───────┘          │
              │                   │                   │
      ┌───────▼───────┐   ┌───────▼───────┐          │
      │ Answer Gen    │   │ Answer Gen    │          │
      │ (LLM Call #3) │   │ (LLM Call)    │          │
      └───────┬───────┘   └───────┬───────┘          │
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  │
                      ┌───────────▼───────────┐
                      │   Persist Facts       │ ← Mem0 + Redis
                      │   (async)             │
                      └───────────┬───────────┘
                                  │
                      ┌───────────▼───────────┐
                      │   BUILD RESPONSE      │
                      │   _single() / _multi()│
                      └───────────┬───────────┘
                                  │
                      ┌───────────▼───────────┐
                      │      RESPONSE         │
                      └───────────────────────┘
```

---

## Response Schemas

### Single Response (`_single()`)

```python
{
  "success": True,
  "intent": str,
  "turn_id": int,
  "session_id": str,
  "is_multi_step": False,
  "response": {
    "message": str,
    "data": Optional[List],
    "email_draft": Optional[Dict],
    "clarification_question": Optional[str]
  },
  "meta": {
    "source_files": [],
    "cache_used": bool,
    "sql_used": Optional[str],
    "chunks_used": List,
    "metadata_answered": bool,
    "latency_ms": int
  }
}
```

### Multi-Step Response (`_multi()`)

```python
{
  "success": True,
  "intent": "multi_step",
  "turn_id": int,
  "session_id": str,
  "is_multi_step": True,
  "steps": [
    {
      "step_id": int,
      "intent": str,
      "response": {"message": str, "data": Optional[List]},
      "sql_used": Optional[str],
      "sources": Optional[List]
    }
  ],
  "response": {
    "message": "Done — 2 queries run, 1 email draft ready for review."
  },
  "meta": {
    "steps_executed": int,
    "parallel_batches": int,
    "latency_ms": int
  }
}
```

---

## File Reference

| Component | File Path |
|-----------|-----------|
| Entry Point | `app.py` |
| Query Service | `services/query_service.py` |
| Memory Service | `services/memory_service.py` |
| Fast Router | `router/fast_router.py` |
| Planner | `planner/planner.py` |
| Multi-Step Planner | `planner/multi_step_planner.py` |
| Semantic Search | `tools/semantic_search.py` |
| SQL Executor | `tools/sql_executor.py` |
| Qdrant Utils | `qdrant_utils.py` |
| Mongo Utils | `mongo_utils.py` |
| LLM Factory | `llm/factory.py` |
| Gemini LLM | `llm/gemini.py` |
| Config | `config.py` |
| Models | `models.py` |

---

*Last updated: March 2026*
