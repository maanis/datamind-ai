"""
services/conversation_window.py — Optimized v5

Replaces Mem0 in the hot path. Pure Redis, ~5ms per read/write.

Stores last N turns per session with:
  - query (original + rewritten)
  - intent
  - sql used
  - columns returned
  - result preview (top 3 rows)
  - entities detected

This gives the unified router everything it needs for:
  - Pronoun resolution (him/her/them → actual entity)
  - Follow-up SQL generation (reuse same table/WHERE)
  - Cache relevance checking (do we have the columns already?)

Mem0 is still used for LONG-TERM semantic facts (across sessions)
but is now called ASYNC after the response is returned — never in the hot path.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

MAX_TURNS = 4          # Keep last 4 turns
WINDOW_TTL = 3600      # 1 hour

_redis_client = None
_fallback: Dict[str, str] = {}


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
            return c
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        c = _r.Redis(host=host, port=port, decode_responses=True)
        c.ping()
        _redis_client = c
        return c
    except Exception as e:
        print(f"WARNING [conv_window]: Redis unavailable ({e})")
        return None


def _rget(key: str) -> Optional[str]:
    c = _get_redis()
    if c:
        try:
            return c.get(key)
        except Exception:
            pass
    return _fallback.get(key)


def _rset(key: str, value: str, ttl: int = WINDOW_TTL):
    c = _get_redis()
    if c:
        try:
            c.set(key, value, ex=ttl)
            return
        except Exception:
            pass
    _fallback[key] = value


def _window_key(session_id: str) -> str:
    return f"rag:conv_window:{session_id}"


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

def get_conversation_window(session_id: str) -> List[Dict]:
    """Return last MAX_TURNS turns. Fast, no LLM."""
    raw = _rget(_window_key(session_id))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def build_history_for_prompt(session_id: str) -> str:
    """
    Build compact conversation history string to inject into the unified router prompt.
    Format that helps the LLM resolve pronouns and generate correct follow-up SQL.

    Example output:
        Turn 1: User asked "get employees with highest rating"
        → Intent: structured | SQL: SELECT name, email, rating FROM employees ORDER BY rating DESC LIMIT 5
        → Columns: [name, email, rating] | Top result: name=Alice, email=alice@co.com, rating=4.9

        Turn 2: User asked "draft an email to her"
        → Intent: email | Resolved to: Alice (alice@co.com)
    """
    turns = get_conversation_window(session_id)
    if not turns:
        return ""

    parts = []
    for i, t in enumerate(turns[-3:], 1):  # Last 3 turns only
        query = t.get("query", "")
        intent = t.get("intent", "")
        sql = t.get("sql", "")
        columns = t.get("columns", [])
        preview = t.get("result_preview", [])
        entities = t.get("entities", [])
        rewritten = t.get("rewritten_query", "")

        line = f"Turn {i}: User asked \"{query}\""
        if rewritten and rewritten != query:
            line += f"\n  → Resolved as: \"{rewritten}\""
        line += f"\n  → Intent: {intent}"
        if sql:
            line += f" | SQL: {sql[:200]}"
        if columns:
            line += f"\n  → Columns returned: [{', '.join(columns[:8])}]"
        if preview:
            line += f"\n  → Top result: {preview[0]}" if preview else ""
        if entities:
            line += f"\n  → Entities: {', '.join(str(e) for e in entities[:5])}"
        parts.append(line)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# WRITE
# ---------------------------------------------------------------------------

def save_turn(
    session_id: str,
    query: str,
    rewritten_query: str,
    intent: str,
    sql: Optional[str] = None,
    columns: Optional[List[str]] = None,
    result_preview: Optional[List[Dict]] = None,
    entities: Optional[List[str]] = None,
):
    """Append a turn to the conversation window. Call this AFTER response is built."""
    window = get_conversation_window(session_id)

    # Build preview string from first row
    preview_strs = []
    if result_preview:
        for row in result_preview[:3]:
            if isinstance(row, dict):
                preview_strs.append(", ".join(f"{k}={v}" for k, v in list(row.items())[:5]))

    turn = {
        "query": query,
        "rewritten_query": rewritten_query or query,
        "intent": intent,
        "sql": (sql or "")[:400],           # Cap SQL length
        "columns": (columns or [])[:10],
        "result_preview": preview_strs,
        "entities": (entities or [])[:8],
        "timestamp": time.time(),
    }

    window.append(turn)
    window = window[-MAX_TURNS:]            # Keep last N only

    _rset(_window_key(session_id), json.dumps(window), WINDOW_TTL)


# ---------------------------------------------------------------------------
# SMART CACHE CHECK (replaces the stale _check_answer_in_cached_results)
# ---------------------------------------------------------------------------

def check_window_for_answer(question: str, session_id: str) -> Optional[str]:
    """
    Check if the current question can be answered directly from the last turn's data.
    Uses column-level matching — NOT string similarity.

    Only answers if:
    1. Question is an explicit follow-up (pronouns/references)
    2. ALL requested columns exist in the last turn's result
    3. The question doesn't request MORE columns than what was cached

    Returns direct answer string or None.
    """
    from router.unified_router import _FRESH_QUERY_RE
    # Skip if this looks like a fresh query
    if _FRESH_QUERY_RE.match(question.strip()):
        return None

    window = get_conversation_window(session_id)
    if not window:
        return None

    last = window[-1]
    cached_columns = set(c.lower() for c in last.get("columns", []))
    preview = last.get("result_preview", [])

    if not cached_columns or not preview:
        return None

    q_lower = question.lower()

    # Detect what columns the question is asking for
    requested = _detect_requested_columns(q_lower, cached_columns)
    if not requested:
        return None

    # All requested columns must be in cache
    if not requested.issubset(cached_columns):
        return None

    # Build answer from cached preview
    entity_ctx = ""
    entities = last.get("entities", [])
    if entities:
        entity_ctx = f" for {entities[0]}"

    value_lines = []
    for col in requested:
        # Find value in preview string
        for prev_str in preview:
            for part in prev_str.split(", "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip().lower() == col:
                        value_lines.append(f"{col.replace('_', ' ').title()}: {v.strip()}")
                        break

    if value_lines:
        return f"From the previous query{entity_ctx}:\n" + "\n".join(value_lines)

    return None


def _detect_requested_columns(q_lower: str, cached_columns: set) -> set:
    """Detect which columns from cached_columns the question is asking about."""
    requested = set()

    # Direct column name mention
    for col in cached_columns:
        col_natural = col.replace("_", " ")
        if col in q_lower or col_natural in q_lower:
            requested.add(col)

    # Common patterns
    patterns = [
        (r'(?:transaction|txn)\s*(?:_|\s)?id', 'transaction_id'),
        (r'email\s*(?:address)?', 'email'),
        (r'phone\s*(?:number)?', 'phone'),
        (r'(?:employee\s*)?id', 'id'),
        (r'name', 'name'),
        (r'salary', 'salary'),
        (r'rating', 'rating'),
    ]
    import re
    for pattern, col_hint in patterns:
        if re.search(pattern, q_lower):
            for col in cached_columns:
                if col_hint in col.lower():
                    requested.add(col)

    return requested
