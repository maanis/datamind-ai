"""
services/redis_session.py

Local Redis session management (redis-py, no Upstash dependency).

Priority order for connection:
  1. REDIS_URL env var  (e.g. redis://localhost:6379)
  2. REDIS_HOST + REDIS_PORT env vars
  3. In-memory dict fallback (single-process dev, lost on restart)

Session is keyed by workspace_id with a TTL (default 1 hour).

Session schema:
{
  "turn_id": 4,
  "last_intent": "structured",
  "last_sql": "SELECT ...",
  "last_result": [{...}],          # rows from last SQL (up to 200)
  "last_rag_answer": "...",
  "last_rag_chunks": [],
  "cache_keys": {                   # named per-step caches, 3-turn TTL
    "high_performers": {
      "rows": [...], "columns": [...], "sql": "...", "turn_id": 3
    }
  },
  "pending_action": "email_approval" | null,
  "pending_action_data": {},
  "email_templates": [{"subject": ..., "body": ...}],
  "recipients": [{"email": ..., "name": ...}],
  "conversation": [                 # last 6 messages = 3 turns
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
"""

import json
import os
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# REDIS CLIENT (lazy, singleton)
# ---------------------------------------------------------------------------

_redis_client = None
_memory_fallback: Dict[str, str] = {}   # in-process fallback


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    try:
        import redis as _redis_lib

        # Option 1: full URL
        url = os.getenv("REDIS_URL")
        if url:
            client = _redis_lib.from_url(url, decode_responses=True)
            client.ping()
            _redis_client = client
            print(f"DEBUG [redis_session]: Connected via REDIS_URL={url}")
            return _redis_client

        # Option 2: host + port
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db   = int(os.getenv("REDIS_DB", "0"))
        pw   = os.getenv("REDIS_PASSWORD") or None
        client = _redis_lib.Redis(host=host, port=port, db=db, password=pw,
                                  decode_responses=True)
        client.ping()
        _redis_client = client
        print(f"DEBUG [redis_session]: Connected to Redis at {host}:{port}")
        return _redis_client

    except Exception as e:
        print(f"WARNING [redis_session]: Redis unavailable ({e}) — using in-memory fallback")
        return None


# ---------------------------------------------------------------------------
# LOW-LEVEL KEY/VALUE
# ---------------------------------------------------------------------------

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))


def _key(workspace_id: str) -> str:
    return f"rag:session:{workspace_id}"


def _get(key: str) -> Optional[str]:
    client = _get_redis()
    if client:
        try:
            return client.get(key)
        except Exception as e:
            print(f"DEBUG [redis_session]: GET error — {e}")
    return _memory_fallback.get(key)


def _set(key: str, value: str, ttl: int = SESSION_TTL):
    client = _get_redis()
    if client:
        try:
            client.set(key, value, ex=ttl)
            return
        except Exception as e:
            print(f"DEBUG [redis_session]: SET error — {e}")
    _memory_fallback[key] = value


def _delete(key: str):
    client = _get_redis()
    if client:
        try:
            client.delete(key)
            return
        except Exception as e:
            print(f"DEBUG [redis_session]: DELETE error — {e}")
    _memory_fallback.pop(key, None)


# ---------------------------------------------------------------------------
# SESSION SCHEMA
# ---------------------------------------------------------------------------

def _empty() -> Dict[str, Any]:
    return {
        "turn_id":            0,
        "last_intent":        None,
        "last_sql":           None,
        "last_result":        [],
        "last_rag_answer":    None,
        "last_rag_chunks":    [],
        "cache_keys":         {},   # { cache_key: { rows, columns, sql, turn_id } }
        "pending_action":     None,
        "pending_action_data":{},
        "email_templates":    [],
        "recipients":         [],
        "conversation":       [],
    }


# ---------------------------------------------------------------------------
# PUBLIC SESSION API
# ---------------------------------------------------------------------------

def get_session(workspace_id: str) -> Dict[str, Any]:
    raw = _get(_key(workspace_id))
    if not raw:
        return _empty()
    try:
        return json.loads(raw)
    except Exception:
        return _empty()


def save_session(workspace_id: str, session: Dict[str, Any]):
    # Trim conversation to last 3 turns (6 messages)
    conv = session.get("conversation", [])
    if len(conv) > 6:
        session["conversation"] = conv[-6:]

    # Expire cache_keys older than 3 turns
    cur = session.get("turn_id", 0)
    session["cache_keys"] = {
        k: v for k, v in session.get("cache_keys", {}).items()
        if cur - v.get("turn_id", 0) <= 3
    }

    _set(_key(workspace_id), json.dumps(session, default=str))


def clear_session(workspace_id: str):
    _delete(_key(workspace_id))


# ---------------------------------------------------------------------------
# NAMED CACHE (per cache_key)
# ---------------------------------------------------------------------------

def store_cache_key(
    workspace_id: str,
    cache_key: str,
    rows: List[Dict],
    columns: List[str],
    sql: Optional[str] = None,
):
    """Store a named result cache entry for the current turn."""
    session = get_session(workspace_id)
    turn_id = session.get("turn_id", 0)
    session.setdefault("cache_keys", {})[cache_key] = {
        "rows":    rows[:200],
        "columns": columns,
        "sql":     sql,
        "turn_id": turn_id,
    }
    # Also update last_result so follow-ups without explicit cache_key still work
    session["last_result"] = rows[:200]
    if sql:
        session["last_sql"] = sql
    save_session(workspace_id, session)


def resolve_cache(workspace_id: str, cache_key: str) -> Optional[Dict[str, Any]]:
    """Return cached entry { rows, columns, sql, turn_id } or None."""
    session = get_session(workspace_id)
    return session.get("cache_keys", {}).get(cache_key)


def get_last_result(workspace_id: str) -> Dict[str, Any]:
    """Most recent SQL rows + columns from session."""
    session = get_session(workspace_id)
    rows = session.get("last_result", [])
    columns = list(rows[0].keys()) if rows else []
    return {
        "rows":    rows,
        "columns": columns,
        "sql":     session.get("last_sql"),
        "intent":  session.get("last_intent"),
    }


# ---------------------------------------------------------------------------
# CONVERSATION / MEMORY
# ---------------------------------------------------------------------------

def append_conversation(workspace_id: str, question: str, answer: str):
    session = get_session(workspace_id)
    session.setdefault("conversation", []).extend([
        {"role": "user",      "content": question},
        {"role": "assistant", "content": str(answer)[:600]},
    ])
    save_session(workspace_id, session)


def get_conversation_for_llm(workspace_id: str, max_turns: int = 3) -> List[Dict]:
    session = get_session(workspace_id)
    return session.get("conversation", [])[-(max_turns * 2):]


def get_session_summary(workspace_id: str) -> str:
    """Last 2 turns formatted as 'role: content' lines for LLM prompts."""
    conv = get_conversation_for_llm(workspace_id, max_turns=2)
    if not conv:
        return ""
    return "\n".join(
        f"{m['role']}: {m['content'][:300]}" for m in conv
    )


# ---------------------------------------------------------------------------
# TURN COUNTER
# ---------------------------------------------------------------------------

def increment_turn(workspace_id: str) -> int:
    session = get_session(workspace_id)
    session["turn_id"] = session.get("turn_id", 0) + 1
    save_session(workspace_id, session)
    return session["turn_id"]


# ---------------------------------------------------------------------------
# EMAIL / PENDING ACTION
# ---------------------------------------------------------------------------

def save_email_state(
    workspace_id: str,
    recipients: List[Dict],
    template: Dict,
    cache_key: Optional[str] = None,
):
    session = get_session(workspace_id)
    session["recipients"]      = recipients
    session["email_templates"] = [template]
    session["pending_action"]  = "email_approval"
    if cache_key:
        session["pending_action_data"] = {"cache_key": cache_key}
    save_session(workspace_id, session)


def clear_pending_action(workspace_id: str):
    session = get_session(workspace_id)
    session["pending_action"]      = None
    session["pending_action_data"] = {}
    save_session(workspace_id, session)


# ---------------------------------------------------------------------------
# FOLLOW-UP CACHE RESOLUTION HELPER
# Used by query_service to decide whether a step needs fresh SQL or can
# reuse / filter cached rows.
# ---------------------------------------------------------------------------

def resolve_followup_strategy(
    workspace_id: str,
    question: str,
    cache_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decide what a follow-up step should do:

    Returns:
        {
          "strategy":  "use_cache_as_is" | "filter_cache" | "fresh_retrieval",
          "cached":    { rows, columns, sql } | None
        }
    """
    # If a specific cache_key is named, look it up first
    if cache_key:
        cached = resolve_cache(workspace_id, cache_key)
        if cached and cached.get("rows"):
            return {"strategy": "use_cache_as_is", "cached": cached}

    # Otherwise check last_result
    last = get_last_result(workspace_id)
    if last.get("rows"):
        # Simple heuristic: if question contains filter words, do filter_cache
        filter_words = {"filter", "only", "where", "whose", "with", "above",
                        "below", "greater", "less", "more than", "under", "over"}
        q_lower = question.lower()
        if any(w in q_lower for w in filter_words):
            return {"strategy": "filter_cache", "cached": last}
        return {"strategy": "use_cache_as_is", "cached": last}

    return {"strategy": "fresh_retrieval", "cached": None}
