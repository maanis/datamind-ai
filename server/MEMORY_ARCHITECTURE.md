# Optimized Memory Architecture — RAG Backend v4

> **Design Update**: Enhanced memory layer for reduced latency and improved context reuse

---

## Table of Contents

1. [Updated Architecture Diagram](#1-updated-architecture-diagram)
2. [Redis Memory Schema](#2-redis-memory-schema)
3. [Mem0 Memory Schema](#3-mem0-memory-schema)
4. [SQL Cache Design](#4-sql-cache-design)
5. [Updated handle_query Pseudocode](#5-updated-handlequery-pseudocode)
6. [Example Flows](#6-example-flows)

---

## 1. Updated Architecture Diagram

### Optimized Pipeline Flow

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 0: GREETING CHECK                                          │
│         _GREETING_RE.match(query)                               │
│         → Instant response if match (0ms)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ not greeting
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: CACHE ANSWER CHECK                                      │
│         check_cache_for_answer()                                │
│         ├─ Turn Cache: session:{id}:recent_turns (last 4 turns) │
│         └─ SQL Cache: sql_cache:{workspace}:{hash}              │
│         → Instant response if cache hit (0ms)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ cache miss
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: WORKFLOW STATE CHECK                                    │
│         get_workflow(workspace_id)                              │
│         → If active workflow, check if continuation             │
│         → If pending email approval, handle action              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: WORKSPACE REGISTRY                                      │
│         get_workspace_metadata_for_query()                      │
│         try_metadata_answer() → Fast-path for stats questions   │
└────────────────────────────┬────────────────────────────────────┘
                             │ not metadata-answerable
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: MEM0 MEMORY RETRIEVAL                                   │
│         ├─ retrieve_relevant_facts(top_k=3)                     │
│         ├─ get_followup_context()                               │
│         ├─ get_workspace_intelligence()                         │
│         ├─ get_available_cached_results()                       │
│         └─ build_compressed_context() → <120 tokens             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: FAST ROUTER (LLM Call #1)                               │
│         fast_route() → RouterDecision                           │
│         Intent: structured | rag | hybrid | email | clarify     │
│         → Skip if follow-up with cache available                │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 6-8: PLANNER + EXECUTION                                   │
│         plan_query() → DAG with steps                           │
│         build_execution_batches() → parallel batching           │
│         _execute_step() → SQL/RAG/Email                         │
│                                                                 │
│         Latency optimizations:                                  │
│         - structured → skip RAG pipeline                        │
│         - rag → skip SQL generation                             │
│         - email → reuse cached recipients                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 9: CACHE + MEM0 UPDATE (Learning)                          │
│         ├─ extract_turn_metadata()                              │
│         ├─ learn_from_turn() → Turn cache + workspace intel     │
│         ├─ set_sql_cache() → Cache SQL result                   │
│         ├─ store_turn_facts() → Mem0 semantic + episodic        │
│         └─ save_last_turn() → Legacy follow-up context          │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 10: BUILD RESPONSE                                         │
│          _single() or _multi() response builder                 │
└─────────────────────────────────────────────────────────────────┘
```

### Memory Layer Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUERY PROCESSING                                   │
│                                                                             │
│   User Query ──┬──────────────────────────────────────────────────┐        │
│                │                                                   │        │
│                ▼                                                   │        │
│   ┌─────────────────────────────────┐                              │        │
│   │     SHORT-TERM MEMORY           │                              │        │
│   │         (REDIS)                 │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ Turn Cache               │   │   ← Check first for         │        │
│   │  │ session:{id}:recent_turns│   │     quick answers           │        │
│   │  │ TTL: 30 min              │   │                              │        │
│   │  └──────────────────────────┘   │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ SQL Cache                │   │   ← Normalized query         │        │
│   │  │ sql_cache:{ws}:{hash}    │   │     lookups (5 min TTL)     │        │
│   │  │ TTL: 5 min               │   │                              │        │
│   │  └──────────────────────────┘   │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ Workflow State           │   │   ← Active workflow,         │        │
│   │  │ rag:workflow:{ws}        │   │     pending actions          │        │
│   │  │ TTL: 30 min              │   │                              │        │
│   │  └──────────────────────────┘   │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ Workspace Intelligence   │   │   ← Column mappings,         │        │
│   │  │ workspace_intel:{ws}     │   │     frequent tables          │        │
│   │  │ TTL: 24 hr               │   │                              │        │
│   │  └──────────────────────────┘   │                              │        │
│   └─────────────────────────────────┘                              │        │
│                │                                                   │        │
│                ▼                                                   │        │
│   ┌─────────────────────────────────┐                              │        │
│   │     LONG-TERM MEMORY            │                              │        │
│   │       (MEM0 + QDRANT)           │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ Semantic Memories        │   │   ← Schema knowledge         │        │
│   │  │ (importance: HIGH)       │   │     "table has columns"      │        │
│   │  └──────────────────────────┘   │                              │        │
│   │                                 │                              │        │
│   │  ┌──────────────────────────┐   │                              │        │
│   │  │ Episodic Memories        │   │   ← User actions             │        │
│   │  │ (importance: MEDIUM/LOW) │   │     "retrieved data from"    │        │
│   │  └──────────────────────────┘   │                              │        │
│   └─────────────────────────────────┘                              │        │
│                                                                    ▼        │
│                                                           [RESPONSE]        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Redis Memory Schema

### Turn Cache

```python
# Key: session:{session_id}:recent_turns
# TTL: 30 minutes
# Stores last 4 turns

[
  {
    "query": "get employees with highest rating",
    "intent": "structured",
    "dataset_used": "employee_csv",
    "sql_query": "SELECT name,email FROM employee_csv ORDER BY rating DESC LIMIT 5",
    "result_preview": ["Alice", "Bob", "Charlie"],
    "columns_used": ["name", "email", "rating"],
    "entities_detected": ["employees", "rating"],
    "timestamp": 1710252000.0,
    "row_count": 5
  },
  {
    "query": "show their emails",
    "intent": "structured",
    "dataset_used": "employee_csv",
    "sql_query": "SELECT email FROM employee_csv ORDER BY rating DESC LIMIT 5",
    "result_preview": ["alice@company.com", "bob@company.com"],
    "columns_used": ["email"],
    "entities_detected": ["emails"],
    "timestamp": 1710252060.0,
    "row_count": 5
  }
]
```

### SQL Cache

```python
# Key: sql_cache:{workspace_id}:{query_hash}
# TTL: 5 minutes
# Hash: MD5 of normalized query (lowercase, no filler words)

{
  "sql": "SELECT name,email FROM employee_csv ORDER BY rating DESC LIMIT 5",
  "rows": [
    {"name": "Alice", "email": "alice@company.com", "rating": 4.9},
    {"name": "Bob", "email": "bob@company.com", "rating": 4.8}
  ],
  "columns": ["name", "email", "rating"],
  "dataset": "employee_csv",
  "timestamp": 1710252000.0,
  "row_count": 5
}
```

### Workflow State

```python
# Key: rag:workflow:{workspace_id}
# TTL: 30 minutes

{
  "type": "email_workflow",
  "active": true,
  "created_at": 1710252000.0,
  "next_step": "confirm_email",
  "outputs": {
    "recipients": [
      {"name": "Alice", "email": "alice@company.com"},
      {"name": "Bob", "email": "bob@company.com"}
    ],
    "email_draft": {
      "subject": "Congratulations on your performance!",
      "body": "Dear team member, ..."
    },
    "sql_result": {
      "rows": [...],
      "columns": ["name", "email", "rating"],
      "sql": "SELECT ..."
    },
    "cache_keys": {
      "top_performers": {...}
    }
  },
  "pending_action": "email_approval",
  "turn_id": 3
}
```

### Workspace Intelligence

```python
# Key: workspace_intel:{workspace_id}
# TTL: 24 hours

{
  "column_mappings": {
    "emails": "email",
    "ratings": "rating",
    "names": "name",
    "salaries": "salary"
  },
  "frequent_datasets": [
    "employee_csv",
    "sales_data",
    "transactions"
  ],
  "frequent_workflows": [
    "email_workflow",
    "data_query"
  ],
  "recent_queries": [
    "employees with <NUM> rating",
    "top performers from <NAME>",
    "send email to <VAL>"
  ]
}
```

---

## 3. Mem0 Memory Schema

### Memory Entry Structure

```python
{
  "memory": "employee_csv contains columns: name, email, rating, department, salary",
  "user_id": "workspace_123",
  "metadata": {
    "intent": "structured",
    "workspace": "workspace_123",
    "memory_type": "semantic",      # semantic | episodic
    "importance_score": 0.8,        # 0.2 (low) to 0.8 (high)
    "dataset": "employee_csv"
  }
}
```

### Memory Types

| Type | Description | Importance | Example |
|------|-------------|------------|---------|
| `semantic` | Schema knowledge, permanent facts | HIGH (0.8) | "employee_csv has columns name, rating, email" |
| `semantic` | Query patterns, column usage | MEDIUM (0.5) | "Query pattern: filtering by rating, ordering descending" |
| `episodic` | User actions, what they retrieved | MEDIUM (0.5) | "User retrieved data from employee_csv using columns name, rating" |
| `episodic` | Turn summaries, transient context | LOW (0.2) | "User asked (structured): get top employees" |

### Retrieval Strategy

```python
# Query: "show me employees with good rating"

# Mem0 retrieves top_k=3 semantically similar facts:
results = [
  "employee_csv contains columns: name, email, rating, department",  # semantic, high
  "User retrieved data from employee_csv using columns name, rating",  # episodic, medium
  "Query pattern for employee_csv: filtering by rating, ordering descending"  # semantic, medium
]

# Injected into planner prompt (~80 tokens max):
planner_context = """
- employee_csv contains columns: name, email, rating, department
- User retrieved data from employee_csv using columns name, rating
- Query pattern for employee_csv: filtering by rating
"""
```

---

## 4. SQL Cache Design

### Cache Key Generation

```python
def _normalize_query(query: str) -> str:
    """
    Normalize query for consistent cache keys.
    
    Example:
    "Can you please show me the employees?" 
    → "employees"
    
    "Get all employees with rating above 4"
    → "employees rating above 4"
    """
    q = query.lower().strip()
    
    # Remove filler words
    fillers = ['the', 'a', 'an', 'please', 'can', 'you', 'show', 'me', 'get', 'find']
    for f in fillers:
        q = re.sub(rf'\b{f}\b', '', q)
    
    # Remove punctuation, collapse whitespace
    q = re.sub(r'[^\w\s]', '', q)
    q = re.sub(r'\s+', ' ', q).strip()
    
    return q

def _sql_cache_key(workspace_id: str, query: str) -> str:
    normalized = _normalize_query(query)
    query_hash = hashlib.md5(normalized.encode()).hexdigest()[:12]
    return f"sql_cache:{workspace_id}:{query_hash}"
```

### Cache Hit Logic

```python
def check_cache_for_answer(query, session_id, workspace_id):
    """
    Check if query can be answered from cache.
    
    Priority:
    1. Turn cache - exact/similar query match
    2. SQL cache - normalized query hash match
    """
    
    # 1. Check turn cache (last 4 turns)
    turns = get_recent_turns(session_id)
    for turn in reversed(turns):  # Most recent first
        if _queries_match(query, turn.query):
            return {
                "answered": True,
                "answer": format_cached_answer(turn),
                "source": "turn_cache",
                "data": turn.result_preview
            }
    
    # 2. Check SQL cache
    sql_entry = get_sql_cache(workspace_id, query)
    if sql_entry:
        return {
            "answered": True,
            "answer": f"Results ({sql_entry.row_count} rows):",
            "source": "sql_cache",
            "data": sql_entry.rows[:50]
        }
    
    return None  # Cache miss
```

### Cache Invalidation

```python
# SQL cache is invalidated via TTL (5 minutes)
# On document update/delete, call:
clear_sql_cache(workspace_id)  # Marks for fresh fetch

# Turn cache persists per session
# Cleared on session end or explicit clear
clear_turn_cache(session_id)
```

---

## 5. Updated handle_query Pseudocode

```python
def handle_query(workspace_id, question, document_id=None, session_id=None):
    """
    Optimized query pipeline with multi-layer caching.
    
    LLM calls: Minimum 1 (router), Maximum 2 (router + execution)
    Target latency: <500ms for cache hits, <2s for fresh queries
    """
    start = time.time()
    effective_session_id = session_id or workspace_id
    
    # =========================================================================
    # STEP 0: GREETING FAST-PATH (0ms)
    # =========================================================================
    if is_greeting(question):
        return greeting_response()
    
    # =========================================================================
    # STEP 1: CACHE ANSWER CHECK (0-10ms)
    # =========================================================================
    cache_hit = check_cache_for_answer(question, effective_session_id, workspace_id)
    if cache_hit:
        log("Cache hit", cache_hit.source)
        return format_response(cache_hit.answer, cache_hit.data, cache_used=True)
    
    # Legacy follow-up check (column value extraction)
    if is_explicit_followup(question):
        last_turn = get_last_turn(workspace_id)
        if cached_answer := check_answer_in_cached_results(question, last_turn):
            return format_response(cached_answer, cache_used=True)
    
    # =========================================================================
    # STEP 2: WORKFLOW STATE CHECK (5-20ms)
    # =========================================================================
    workflow = get_workflow(workspace_id)
    
    if workflow.pending_action == "email_approval":
        return handle_email_action(question, workflow)
    
    # =========================================================================
    # STEP 3: WORKSPACE REGISTRY (10-50ms)
    # =========================================================================
    metadata_list = get_workspace_metadata(workspace_id, document_id)
    
    # Metadata fast-path (row count, column count, etc.)
    if meta_answer := try_metadata_answer(question, metadata_list):
        store_turn_facts(workspace_id, question, meta_answer, "metadata")
        return format_response(meta_answer, metadata_answered=True)
    
    # =========================================================================
    # STEP 4: MEM0 MEMORY RETRIEVAL (50-150ms)
    # =========================================================================
    # Semantic facts from Mem0
    memory_facts = retrieve_relevant_facts(workspace_id, question, top_k=3)
    
    # Follow-up context for pronoun resolution
    followup_context = get_followup_context(workspace_id)
    
    # Workspace intelligence (column mappings, frequent tables)
    workspace_intel = get_workspace_context_for_planner(workspace_id)
    
    # Available cached results for partial reuse
    available_cache = get_available_cached_results(effective_session_id, workspace_id)
    
    # Compress to <120 tokens
    compressed_context = build_compressed_context(
        query=question,
        session_id=effective_session_id,
        workspace_id=workspace_id,
        memory_facts=memory_facts,
        metadata_list=metadata_list
    )
    
    combined_context = merge_contexts(followup_context, compressed_context, workspace_intel)
    
    # =========================================================================
    # STEP 5: FAST ROUTER (LLM CALL #1) (200-500ms)
    # =========================================================================
    decision = fast_route(
        question=question,
        datasets_hint=format_datasets_hint(metadata_list),
        memory_facts=combined_context,
        active_workflow=workflow.type if workflow.active else None
    )
    
    log(f"Router: intent={decision.intent}, continuation={decision.is_continuation}")
    
    # Workflow collision guard
    if decision.new_workflow_needed and workflow.active:
        cancel_workflow(workspace_id)
    
    # Special intents
    if decision.intent == "greeting":
        return greeting_response()
    
    if decision.intent == "clarification":
        return clarification_response(question, metadata_list)
    
    # =========================================================================
    # STEP 6-8: PLANNER + EXECUTION (LLM CALL #2) (500-1500ms)
    # =========================================================================
    plan = plan_query(
        question=decision.rewritten_query,
        memory_context=combined_context,
        metadata_list=metadata_list,
        available_cache=available_cache  # Pass cached results for partial reuse
    )
    
    # Execute steps (parallel within batches)
    batches = build_execution_batches(plan.steps)
    step_results = {}
    
    for batch in batches:
        for step in batch:  # Parallel execution within batch
            # Skip based on intent
            if step.intent == "structured" and should_skip_sql(step.intent):
                continue
            if step.intent == "rag" and should_skip_rag(step.intent):
                continue
            
            result = execute_step(step, workspace_id, metadata_list, step_results)
            step_results[step.step_id] = result
    
    # =========================================================================
    # STEP 9: CACHE + MEM0 UPDATE (50-200ms, async possible)
    # =========================================================================
    # Extract metadata for caching
    turn_data = extract_turn_metadata(
        query=question,
        intent=decision.intent,
        sql_query=get_sql_from_results(step_results),
        result_rows=get_data_from_results(step_results),
        columns=get_columns_from_results(step_results),
        dataset=get_dataset_from_results(step_results, metadata_list),
        entities=extract_entities(question)
    )
    
    # 1. Save to turn cache
    learn_from_turn(workspace_id, effective_session_id, turn_data)
    
    # 2. Cache SQL result
    if sql_used and result_rows:
        set_sql_cache(workspace_id, question, SQLCacheEntry(...))
    
    # 3. Store Mem0 facts (semantic + episodic)
    store_turn_facts(
        workspace_id=workspace_id,
        question=question,
        answer=combined_answer,
        intent=decision.intent,
        dataset=turn_data.dataset_used,
        columns=turn_data.columns_used,
        sql_query=turn_data.sql_query
    )
    
    # =========================================================================
    # STEP 10: BUILD RESPONSE
    # =========================================================================
    return format_response(
        step_results=step_results,
        is_multi_step=plan.is_multi_step,
        latency_ms=elapsed_ms()
    )
```

---

## 6. Example Flows

### Flow 1: Structured Query (Fresh)

```
User: "get employees with highest rating"

┌─ STEP 0: Greeting Check ─────────────────────────────────────────┐
│ Not a greeting                                                   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 1: Cache Check ────────────────────────────────────────────┐
│ Turn Cache: MISS (empty)                                         │
│ SQL Cache: MISS (first query)                                    │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 2: Workflow State ─────────────────────────────────────────┐
│ No active workflow                                               │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 3: Workspace Registry ─────────────────────────────────────┐
│ Metadata: [employee_csv(name,rating,email,department)]           │
│ Metadata answer: None                                            │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 4: Memory Retrieval ───────────────────────────────────────┐
│ Mem0 facts: "employee_csv contains columns name, rating, email"  │
│ Compressed context (45 tokens):                                  │
│   "Schema: employee_csv(name, rating, email, department)"        │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 5: Fast Router (LLM #1) ───────────────────────────────────┐
│ Intent: structured                                               │
│ Rewritten: "get employees with highest rating"                   │
│ Confidence: 0.95                                                 │
│ Latency: 450ms                                                   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 6-8: Execution ────────────────────────────────────────────┐
│ SQL Generated (LLM #2):                                          │
│   SELECT name, email, rating FROM employee_csv                   │
│   ORDER BY rating DESC LIMIT 10                                  │
│ SQL Executed: 5 rows returned                                    │
│ Answer: "Here are the top employees: Alice (4.9), Bob (4.8)..."  │
│ Latency: 850ms                                                   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 9: Cache Update ───────────────────────────────────────────┐
│ Turn Cache: SAVED (query, intent, sql, results, entities)        │
│ SQL Cache: SAVED (key: employees highest rating)                 │
│ Mem0: SAVED (semantic: columns used, episodic: query action)     │
│ Workspace Intel: UPDATED (frequent_datasets: employee_csv)       │
│ Latency: 80ms                                                    │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ RESPONSE ───────────────────────────────────────────────────────┐
│ {                                                                │
│   "intent": "structured",                                        │
│   "message": "Here are the top employees by rating...",          │
│   "data": [{"name":"Alice","rating":4.9}, ...],                  │
│   "sql_used": "SELECT name, email, rating...",                   │
│   "meta": {"latency_ms": 1380, "cache_used": false}              │
│ }                                                                │
└──────────────────────────────────────────────────────────────────┘

Total Latency: ~1400ms (2 LLM calls)
```

### Flow 2: Follow-up Query (Cache Hit)

```
User: "get employees with highest rating"  ← Previous query
User: "show me top employees"              ← Follow-up (similar)

┌─ STEP 1: Cache Check ────────────────────────────────────────────┐
│ Normalized queries:                                              │
│   Previous: "employees highest rating"                           │
│   Current:  "top employees"                                      │
│                                                                  │
│ Word overlap: 1/2 = 50% (below 80% threshold)                    │
│ Turn Cache: MISS (not similar enough)                            │
│                                                                  │
│ SQL Cache: hash("top employees") → MISS                          │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
[... continues to router, but with enhanced context ...]

┌─ STEP 4: Memory Retrieval ───────────────────────────────────────┐
│ Compressed context:                                              │
│   "Recent: employee_csv retrieved (name, rating, email)          │
│    Schema: employee_csv(name, rating, email, department)         │
│    - employee_csv contains columns name, rating, email"          │
│                                                                  │
│ Workspace intel: "Frequent tables: employee_csv"                 │
└──────────────────────────────────────────────────────────────────┘

[... router correctly routes to employee_csv ...]
```

### Flow 3: Exact Cache Hit

```
User: "get employees with highest rating"  ← Previous query
User: "get employees with highest rating"  ← Same query

┌─ STEP 1: Cache Check ────────────────────────────────────────────┐
│ SQL Cache:                                                       │
│   key: sql_cache:workspace_123:a8f2b3c1d4e5                      │
│   result: {                                                      │
│     "sql": "SELECT name, email, rating...",                      │
│     "rows": [{"name":"Alice",...}, ...],                         │
│     "row_count": 5                                               │
│   }                                                              │
│ Status: HIT!                                                     │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ RESPONSE (instant) ─────────────────────────────────────────────┐
│ {                                                                │
│   "intent": "structured",                                        │
│   "message": "Here are the results (5 rows):",                   │
│   "data": [{"name":"Alice","rating":4.9}, ...],                  │
│   "sql_used": "SELECT name, email, rating...",                   │
│   "meta": {"latency_ms": 15, "cache_used": true}                 │
│ }                                                                │
└──────────────────────────────────────────────────────────────────┘

Total Latency: ~15ms (0 LLM calls)
```

### Flow 4: RAG Query with Rewriting

```
User: "what are the promotion rules?"

┌─ STEP 1: Cache Check ────────────────────────────────────────────┐
│ Cache: MISS                                                      │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 5: Fast Router ────────────────────────────────────────────┐
│ Intent: rag                                                      │
│ No structured data matches "promotion rules"                     │
│ Latency: 400ms                                                   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 6-8: RAG Execution ────────────────────────────────────────┐
│ Query Rewriting:                                                 │
│   Original: "what are the promotion rules?"                      │
│   Expanded: "employee promotion policy rules requirements"       │
│   (added context from workspace intel + memory facts)            │
│                                                                  │
│ Vector Search (rewritten query):                                 │
│   - Dense: 0.85 similarity to "HR Policy Document"               │
│   - BM25: "promotion" keyword match                              │
│   - RRF fusion: combined score                                   │
│   - Flashrank rerank: top 5 chunks                               │
│                                                                  │
│ Answer Generated (LLM):                                          │
│   "Based on the HR policy, promotion eligibility requires..."    │
│ Latency: 1200ms                                                  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ RESPONSE ───────────────────────────────────────────────────────┐
│ {                                                                │
│   "intent": "rag",                                               │
│   "message": "Based on the HR policy...",                        │
│   "sources": [{"text": "Promotion Policy...", "score": 0.89}],   │
│   "meta": {"latency_ms": 1600, "cache_used": false}              │
│ }                                                                │
└──────────────────────────────────────────────────────────────────┘
```

### Flow 5: Email Workflow with Cache Reuse

```
User: "get top performers"       ← Query 1
User: "send them promotion email" ← Query 2 (cache reuse)

--- Query 1 ---
[... standard structured flow, results cached ...]

Turn Cache saved:
{
  "query": "get top performers",
  "dataset_used": "employee_csv",
  "result_preview": ["alice@company.com", "bob@company.com"],
  "columns_used": ["name", "email", "rating"],
  "row_count": 5
}

--- Query 2 ---

┌─ STEP 1: Cache Check ────────────────────────────────────────────┐
│ can_reuse_for_email():                                           │
│   - Query contains "email" or "send": YES                        │
│   - Query contains pronoun "them": YES                           │
│   - Previous turn has results: YES                               │
│   → Return cached data for email                                 │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 4: Memory Retrieval ───────────────────────────────────────┐
│ available_cached_results:                                        │
│   "top_performers": {                                            │
│     "columns": ["name", "email", "rating"],                      │
│     "row_count": 5,                                              │
│     "dataset": "employee_csv",                                   │
│     "query": "get top performers"                                │
│   }                                                              │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 5: Fast Router ────────────────────────────────────────────┐
│ Intent: email                                                    │
│ is_continuation: false (new intent)                              │
│ Rewritten: "send promotion email to employees"                   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ STEP 6-8: Email Execution ──────────────────────────────────────┐
│ Recipients: REUSED from turn cache (skip SQL!)                   │
│   [{"name":"Alice","email":"alice@company.com"}, ...]            │
│                                                                  │
│ Draft Generated (LLM):                                           │
│   Subject: "Congratulations on your exceptional performance!"    │
│   Body: "Dear [name], ..."                                       │
│                                                                  │
│ Workflow started: email_workflow                                 │
│ Pending action: email_approval                                   │
│ Latency: 600ms (1 LLM call, no SQL)                              │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─ RESPONSE ───────────────────────────────────────────────────────┐
│ {                                                                │
│   "intent": "email",                                             │
│   "message": "📧 Email draft ready for 5 recipients...",         │
│   "email_draft": {                                               │
│     "subject": "Congratulations...",                             │
│     "body": "Dear team member...",                               │
│     "recipients": [...],                                         │
│     "editable": true                                             │
│   },                                                             │
│   "meta": {"latency_ms": 1000, "cache_used": true}               │
│ }                                                                │
└──────────────────────────────────────────────────────────────────┘

Total Latency: ~1000ms (1 LLM call, SQL skipped via cache)
```

---

## Performance Summary

| Scenario | LLM Calls | Latency | Cache Used |
|----------|-----------|---------|------------|
| Exact cache hit | 0 | ~15ms | SQL Cache |
| Similar query (turn cache) | 0 | ~20ms | Turn Cache |
| Fresh structured query | 2 | ~1400ms | None |
| Follow-up (partial reuse) | 1-2 | ~800ms | Turn Cache |
| Email with cached data | 1 | ~1000ms | Turn Cache |
| RAG query | 2 | ~1600ms | None |
| Greeting | 0 | ~5ms | N/A |

### Latency Reduction Summary

| Optimization | Before | After | Improvement |
|--------------|--------|-------|-------------|
| Exact query repeat | ~1400ms | ~15ms | **99%** |
| Email with cached recipients | ~2500ms | ~1000ms | **60%** |
| Follow-up pronoun resolution | ~1400ms | ~800ms | **43%** |
| Workspace column mapping | ~500ms (clarification) | ~0ms | **100%** |

---

## File Reference

| Module | File | Purpose |
|--------|------|---------|
| Memory Cache | `services/memory_cache.py` | Turn cache, SQL cache, workspace intel |
| Memory Service | `services/memory_service.py` | Workflow state, Mem0 facts, turn tracker |
| Query Service | `services/query_service.py` | Main query pipeline |

---

*Last updated: March 2026*
