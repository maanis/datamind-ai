"""
services/memory_service.py — v4

Two-layer memory architecture:
  Layer 1 — Redis (short-term, TTL 30 min)
    Stores ONLY: workflow_state, step_outputs, cached_rows, pending_action
    NO raw conversation text stored here.

  Layer 2 — Mem0 + Qdrant (long-term semantic)
    Stores extracted facts after each turn.
    Retrieval is query-matched (top_k=3), NOT chronological.
    This keeps prompt context flat (~80 tokens) regardless of turn count.

Why this beats raw conversation storage:
  - Turn 10 never sees context from Turn 2 unless it's semantically relevant
  - LLM prompt size stays constant — no hallucination growth
  - Workflow state is mechanical (not fed to LLM), so it can be verbose

Workflow State Schema (Redis only):
{
  "type": "email_workflow | data_query",
  "active": true,
  "created_at": timestamp,
  "next_step": "get_recipients | draft_email | confirm_email | send_email | done",
  "outputs": {
    "recipients": [...],
    "email_draft": {"subject": ..., "body": ...},
    "sql_result": {"rows": [...], "columns": [...], "sql": "..."},
    "cache_keys": {"key_name": {"rows": [...], "columns": [...], "sql": "..."}}
  },
  "pending_action": "email_approval | null",
  "turn_id": 3
}
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# REDIS CLIENT (workflow state only)
# ---------------------------------------------------------------------------

_redis_client = None
_memory_fallback: Dict[str, str] = {}

WORKFLOW_TTL = int(os.getenv("WORKFLOW_TTL_SECONDS", "1800"))   # 30 min
SESSION_TTL  = int(os.getenv("SESSION_TTL_SECONDS",  "3600"))   # 1 hr


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as _r
        url = os.getenv("REDIS_URL")
        if url:
            c = _r.from_url(url, decode_responses=True)
            c.ping()
            _redis_client = c
            print("DEBUG [memory]: Redis connected via REDIS_URL")
            return _redis_client
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        c = _r.Redis(host=host, port=port, decode_responses=True)
        c.ping()
        _redis_client = c
        print(f"DEBUG [memory]: Redis connected at {host}:{port}")
        return _redis_client
    except Exception as e:
        print(f"WARNING [memory]: Redis unavailable ({e}) — in-memory fallback")
        return None


def _rget(key: str) -> Optional[str]:
    c = _get_redis()
    if c:
        try:
            return c.get(key)
        except Exception:
            pass
    return _memory_fallback.get(key)


def _rset(key: str, value: str, ttl: int = SESSION_TTL):
    c = _get_redis()
    if c:
        try:
            c.set(key, value, ex=ttl)
            return
        except Exception:
            pass
    _memory_fallback[key] = value


def _rdel(key: str):
    c = _get_redis()
    if c:
        try:
            c.delete(key)
            return
        except Exception:
            pass
    _memory_fallback.pop(key, None)


# ---------------------------------------------------------------------------
# MEM0 CLIENT (long-term semantic facts)
# ---------------------------------------------------------------------------

_mem0_client = None
_mem0_init_attempted = False


def init_mem0():
    """
    Initialize Mem0 at server startup. Call this from app.py lifespan.
    This avoids the delay of lazy initialization during the first query.
    """
    global _mem0_client, _mem0_init_attempted
    if _mem0_init_attempted:
        return _mem0_client
    
    _mem0_init_attempted = True
    print("DEBUG [memory]: Initializing Mem0 at startup...")
    
    try:
        from mem0 import Memory
        qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "url": qdrant_url,
                    "collection_name": "rag_workspace_memory",
                    "embedding_model_dims": 384,
                }
            },
            "llm": {
                "provider": "gemini",
                "config": {
                    "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                    "api_key": os.getenv("GEMINI_API_KEY", ""),
                    "temperature": 0.1,
                    "max_tokens": 500,
                }
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "sentence-transformers/all-MiniLM-L6-v2"
                }
            }
        }
        _mem0_client = Memory.from_config(config)
        print("DEBUG [memory]: Mem0 initialized with Qdrant backend")
        return _mem0_client
    except Exception as e:
        print(f"WARNING [memory]: Mem0 unavailable ({e}) — long-term memory disabled")
        return None


def _get_mem0():
    """Get the Mem0 client. Initializes lazily if not already done."""
    global _mem0_client, _mem0_init_attempted
    if _mem0_client is not None:
        return _mem0_client
    if not _mem0_init_attempted:
        return init_mem0()
    return None  # Already attempted but failed


# ---------------------------------------------------------------------------
# WORKFLOW STATE (Redis)
# Stores mechanical state — NEVER fed raw to LLM prompt
# ---------------------------------------------------------------------------

def _wf_key(workspace_id: str) -> str:
    return f"rag:workflow:{workspace_id}"


def _empty_workflow() -> Dict[str, Any]:
    return {
        "type":           None,
        "active":         False,
        "created_at":     time.time(),
        "next_step":      None,
        "outputs":        {
            "recipients":  [],
            "email_draft": None,
            "sql_result":  None,
            "cache_keys":  {},
        },
        "pending_action": None,
        "turn_id":        0,
    }


def get_workflow(workspace_id: str) -> Dict[str, Any]:
    raw = _rget(_wf_key(workspace_id))
    if not raw:
        return _empty_workflow()
    try:
        return json.loads(raw)
    except Exception:
        return _empty_workflow()


def save_workflow(workspace_id: str, wf: Dict[str, Any]):
    _rset(_wf_key(workspace_id), json.dumps(wf, default=str), ttl=WORKFLOW_TTL)


def start_workflow(workspace_id: str, wf_type: str, first_step: str) -> Dict[str, Any]:
    """Cancel any active workflow and start a fresh one."""
    wf = _empty_workflow()
    wf["type"]       = wf_type
    wf["active"]     = True
    wf["next_step"]  = first_step
    wf["created_at"] = time.time()
    save_workflow(workspace_id, wf)
    print(f"DEBUG [workflow]: started '{wf_type}' → first_step='{first_step}'")
    return wf


def advance_workflow(workspace_id: str, completed_step: str, next_step: Optional[str],
                     output_key: Optional[str] = None, output_value: Any = None):
    """Move workflow to next step, optionally store a step output."""
    wf = get_workflow(workspace_id)
    if output_key and output_value is not None:
        wf["outputs"][output_key] = output_value
    wf["next_step"] = next_step
    if next_step is None or next_step == "done":
        wf["active"] = False
    save_workflow(workspace_id, wf)
    print(f"DEBUG [workflow]: '{completed_step}' → '{next_step}'")


def complete_workflow(workspace_id: str):
    wf = get_workflow(workspace_id)
    wf["active"]          = False
    wf["next_step"]       = "done"
    wf["pending_action"]  = None   # CRITICAL: clear so next query isn't intercepted
    save_workflow(workspace_id, wf)
    print("DEBUG [workflow]: completed")


def cancel_workflow(workspace_id: str, reason: str = ""):
    _rdel(_wf_key(workspace_id))
    print(f"DEBUG [workflow]: cancelled ({reason})")


def store_cache_in_workflow(workspace_id: str, cache_key: str,
                             rows: List[Dict], columns: List[str],
                             sql: Optional[str] = None):
    wf = get_workflow(workspace_id)
    wf["outputs"]["cache_keys"][cache_key] = {
        "rows":    rows[:200],
        "columns": columns,
        "sql":     sql,
        "stored_at": time.time(),
    }
    # Also update sql_result for the latest query
    wf["outputs"]["sql_result"] = {
        "rows":    rows[:200],
        "columns": columns,
        "sql":     sql,
    }
    save_workflow(workspace_id, wf)


def get_cached_result(workspace_id: str, cache_key: str) -> Optional[Dict]:
    wf = get_workflow(workspace_id)
    return wf["outputs"]["cache_keys"].get(cache_key)


def set_email_pending(workspace_id: str, recipients: List[Dict],
                      draft: Dict):
    wf = get_workflow(workspace_id)
    wf["outputs"]["recipients"]  = recipients
    wf["outputs"]["email_draft"] = draft
    wf["pending_action"]         = "email_approval"
    wf["next_step"]              = "confirm_email"
    save_workflow(workspace_id, wf)


def clear_pending_action(workspace_id: str):
    wf = get_workflow(workspace_id)
    wf["pending_action"] = None
    save_workflow(workspace_id, wf)
    print("DEBUG [workflow]: pending_action cleared")


def increment_turn(workspace_id: str) -> int:
    wf = get_workflow(workspace_id)
    wf["turn_id"] = wf.get("turn_id", 0) + 1
    save_workflow(workspace_id, wf)
    return wf["turn_id"]


# ---------------------------------------------------------------------------
# LONG-TERM FACTS (Mem0)
# Enhanced with semantic/episodic memory types and importance scoring
# ---------------------------------------------------------------------------

# Memory type constants
MEMORY_TYPE_SEMANTIC = "semantic"    # Schema knowledge, column info, data patterns
MEMORY_TYPE_EPISODIC = "episodic"    # User actions, query history, workflow patterns

# Importance score thresholds
IMPORTANCE_HIGH = 0.8    # Schema knowledge, permanent facts
IMPORTANCE_MEDIUM = 0.5  # Query patterns, column usage
IMPORTANCE_LOW = 0.2     # Temporary results, transient info


def store_turn_facts(workspace_id: str, question: str, answer: str,
                     intent: str, extra_context: str = "",
                     dataset: str = "", columns: List[str] = None,
                     sql_query: str = ""):
    """
    Extract and store semantic facts from a completed turn.
    
    Enhanced to:
    1. Classify memory as semantic or episodic
    2. Score importance for retrieval prioritization
    3. Store structured metadata for better filtering
    """
    mem = _get_mem0()
    if not mem:
        return

    columns = columns or []
    
    # Extract different types of facts
    facts_to_store = []
    
    # 1. SEMANTIC FACTS - Schema/column knowledge (high importance)
    if dataset and columns:
        schema_fact = f"{dataset} contains columns: {', '.join(columns[:10])}"
        facts_to_store.append({
            "text": schema_fact,
            "type": MEMORY_TYPE_SEMANTIC,
            "importance": IMPORTANCE_HIGH,
            "dataset": dataset,
        })
    
    # 2. EPISODIC FACTS - What the user did (medium importance)
    if intent == "structured" and dataset:
        action_fact = f"User retrieved data from {dataset}"
        if columns:
            action_fact += f" using columns {', '.join(columns[:5])}"
        facts_to_store.append({
            "text": action_fact,
            "type": MEMORY_TYPE_EPISODIC,
            "importance": IMPORTANCE_MEDIUM,
            "dataset": dataset,
        })
    
    # 3. QUERY PATTERN FACTS - How user phrases queries (medium importance)
    if sql_query and len(sql_query) < 500:
        # Extract pattern (e.g., "filtering by rating", "ordering by date")
        pattern = _extract_query_pattern_from_sql(sql_query)
        if pattern:
            pattern_fact = f"Query pattern for {dataset}: {pattern}"
            facts_to_store.append({
                "text": pattern_fact,
                "type": MEMORY_TYPE_SEMANTIC,
                "importance": IMPORTANCE_MEDIUM,
                "dataset": dataset,
            })
    
    # 4. GENERAL TURN SUMMARY (low importance, for context)
    turn_text = f"User asked ({intent}): {question[:200]}"
    if answer:
        turn_text += f"\nResult: {answer[:200]}"
    facts_to_store.append({
        "text": turn_text,
        "type": MEMORY_TYPE_EPISODIC,
        "importance": IMPORTANCE_LOW,
        "dataset": dataset,
    })

    # Store each fact with metadata
    for fact in facts_to_store:
        try:
            mem.add(
                fact["text"],
                user_id=workspace_id,
                metadata={
                    "intent": intent,
                    "workspace": workspace_id,
                    "memory_type": fact["type"],
                    "importance_score": fact["importance"],
                    "dataset": fact.get("dataset", ""),
                }
            )
        except Exception as e:
            print(f"DEBUG [memory]: Mem0 store failed for fact ({e})")


def _extract_query_pattern_from_sql(sql: str) -> Optional[str]:
    """Extract a human-readable pattern from SQL."""
    patterns = []
    sql_lower = sql.lower()
    
    # Aggregations
    if "count(" in sql_lower:
        patterns.append("counting records")
    if "sum(" in sql_lower:
        patterns.append("summing values")
    if "avg(" in sql_lower:
        patterns.append("averaging values")
    if "max(" in sql_lower or "min(" in sql_lower:
        patterns.append("finding extremes")
    
    # Filtering
    if "where" in sql_lower:
        # Extract filter column
        match = re.search(r'where\s+"?(\w+)"?\s*(?:=|>|<|like)', sql_lower)
        if match:
            patterns.append(f"filtering by {match.group(1)}")
    
    # Ordering
    if "order by" in sql_lower:
        match = re.search(r'order\s+by\s+"?(\w+)"?', sql_lower)
        if match:
            direction = "descending" if "desc" in sql_lower else "ascending"
            patterns.append(f"ordering by {match.group(1)} ({direction})")
    
    # Limiting
    if "limit" in sql_lower:
        match = re.search(r'limit\s+(\d+)', sql_lower)
        if match:
            patterns.append(f"top {match.group(1)} records")
    
    return " | ".join(patterns) if patterns else None


def retrieve_relevant_facts(workspace_id: str, question: str,
                             top_k: int = 3) -> str:
    """
    Retrieve top_k semantically relevant facts for the current query.
    Returns a compact string for injection into the planner prompt.
    Format: "- fact1\n- fact2\n- fact3"
    """
    mem = _get_mem0()
    if not mem:
        return ""

    try:
        results = mem.search(question, user_id=workspace_id, limit=top_k)
        if not results:
            return ""
        
        # Handle different Mem0 response formats
        # Newer versions: {"results": [...]} or {"memories": [...]}
        # Older versions: direct list [...]
        if isinstance(results, dict):
            results = results.get("results") or results.get("memories") or []
        
        facts = []
        for r in results:
            # Handle both dict format and string format
            if isinstance(r, dict):
                memory_text = r.get("memory") or r.get("text") or r.get("content") or ""
            elif isinstance(r, str):
                memory_text = r
            else:
                continue
            if memory_text and len(memory_text) > 5:
                facts.append(f"- {memory_text[:200]}")
        return "\n".join(facts)
    except Exception as e:
        print(f"DEBUG [memory]: Mem0 search failed ({e})")
        return ""


# ---------------------------------------------------------------------------
# LIGHTWEIGHT CONVERSATION TURN TRACKER (Redis — last 3 turns)
# Stores structured context for follow-up resolution
# ---------------------------------------------------------------------------

def _turn_key(workspace_id: str) -> str:
    return f"rag:turns:{workspace_id}"


def _history_key(workspace_id: str) -> str:
    return f"rag:history:{workspace_id}"


def save_last_turn(workspace_id: str, intent: str, action_summary: str,
                   query: str = "", sql: str = "", entities: List[str] = None,
                   result_preview: str = ""):
    """
    Store structured data about the last turn for follow-up resolution.
    
    Args:
        workspace_id: Workspace identifier
        intent: Query intent (structured, rag, etc.)
        action_summary: Brief summary of the response  
        query: Original user query
        sql: SQL query executed (if any)
        entities: Key entities mentioned in the query (names, IDs, etc.)
        result_preview: Brief preview of results for context
    """
    data = {
        "intent":  intent,
        "summary": action_summary[:300],
        "query":   query[:500],
        "sql":     sql[:500] if sql else "",
        "entities": (entities or [])[:10],  # Max 10 entities
        "result_preview": result_preview[:500],
        "ts":      time.time(),
    }
    _rset(_turn_key(workspace_id), json.dumps(data), ttl=WORKFLOW_TTL)
    
    # Also maintain a short conversation history (last 3 turns)
    history = get_conversation_history(workspace_id)
    history.append({
        "query": query[:300],
        "intent": intent,
        "entities": (entities or [])[:5],
        "sql": sql[:300] if sql else "",
        "response": action_summary[:200],
        "ts": time.time()
    })
    # Keep only last 3 turns
    history = history[-3:]
    _rset(_history_key(workspace_id), json.dumps(history), ttl=SESSION_TTL)


def get_last_turn(workspace_id: str) -> Optional[Dict]:
    raw = _rget(_turn_key(workspace_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def get_conversation_history(workspace_id: str) -> List[Dict]:
    """Get last 3 conversation turns for context."""
    raw = _rget(_history_key(workspace_id))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def get_followup_context(workspace_id: str) -> str:
    """
    Build a context string for follow-up queries.
    Returns structured context about the last query for pronoun resolution.
    """
    last = get_last_turn(workspace_id)
    if not last:
        return ""
    
    parts = []
    
    if last.get("query"):
        parts.append(f"Previous query: {last['query']}")
    
    if last.get("entities"):
        entities_str = ", ".join(str(e) for e in last["entities"][:5])
        parts.append(f"Entities mentioned: {entities_str}")
    
    if last.get("sql"):
        parts.append(f"SQL used: {last['sql']}")
    
    if last.get("result_preview"):
        parts.append(f"Results: {last['result_preview'][:200]}")
    
    return "\n".join(parts)


def clear_all(workspace_id: str):
    """Full reset — clears workflow + turn tracker + history."""
    _rdel(_wf_key(workspace_id))
    _rdel(_turn_key(workspace_id))
    _rdel(_history_key(workspace_id))
    print(f"DEBUG [memory]: cleared all state for workspace {workspace_id}")