"""
services/memory_cache.py — Optimized Memory Layer

This module implements the enhanced memory architecture:

1. SHORT-TERM TURN CACHE (Redis)
   - Last 3-4 turns per session with structured data
   - TTL: 30 minutes

2. SQL RESULT CACHE (Redis)
   - Result caching with normalized query keys
   - TTL: 5-10 minutes

3. WORKSPACE INTELLIGENCE (Mem0)
   - Column mappings, frequent datasets, common patterns
   - Persisted across sessions

4. MEMORY CONTEXT COMPRESSION
   - <120 token compressed context for planner
   - Includes: recent queries, schema hints, relevant facts

5. CACHE ANSWER CHECK
   - Check if query can be answered from cached results
   - Check if partial data can be reused
"""

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

TURN_CACHE_TTL = int(os.getenv("TURN_CACHE_TTL", "1800"))       # 30 min
SQL_CACHE_TTL = int(os.getenv("SQL_CACHE_TTL", "300"))          # 5 min
WORKSPACE_INTEL_TTL = int(os.getenv("WORKSPACE_INTEL_TTL", "86400"))  # 24 hr
MAX_CACHED_TURNS = 4
MAX_CONTEXT_TOKENS = 120


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class TurnData:
    """Structured data for a single conversation turn."""
    query: str
    intent: str
    dataset_used: str
    sql_query: str
    result_preview: List[str]  # Top 3-5 values
    columns_used: List[str]
    entities_detected: List[str]
    timestamp: float
    row_count: int = 0
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "TurnData":
        return cls(
            query=d.get("query", ""),
            intent=d.get("intent", ""),
            dataset_used=d.get("dataset_used", ""),
            sql_query=d.get("sql_query", ""),
            result_preview=d.get("result_preview", []),
            columns_used=d.get("columns_used", []),
            entities_detected=d.get("entities_detected", []),
            timestamp=d.get("timestamp", time.time()),
            row_count=d.get("row_count", 0),
        )


@dataclass
class SQLCacheEntry:
    """Cached SQL query result."""
    sql: str
    rows: List[Dict]
    columns: List[str]
    dataset: str
    timestamp: float
    row_count: int
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "SQLCacheEntry":
        return cls(
            sql=d.get("sql", ""),
            rows=d.get("rows", []),
            columns=d.get("columns", []),
            dataset=d.get("dataset", ""),
            timestamp=d.get("timestamp", time.time()),
            row_count=d.get("row_count", 0),
        )


@dataclass
class WorkspaceIntelligence:
    """Learned workspace knowledge."""
    column_mappings: Dict[str, str]      # "emails" -> "email"
    frequent_datasets: List[str]         # ["employee_csv", "sales_data"]
    frequent_workflows: List[str]        # ["email_workflow", "report_query"]
    recent_queries: List[str]            # Last 5 query patterns
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "WorkspaceIntelligence":
        return cls(
            column_mappings=d.get("column_mappings", {}),
            frequent_datasets=d.get("frequent_datasets", []),
            frequent_workflows=d.get("frequent_workflows", []),
            recent_queries=d.get("recent_queries", []),
        )


# ---------------------------------------------------------------------------
# REDIS HELPERS (imported from memory_service)
# ---------------------------------------------------------------------------

_redis_client = None
_memory_fallback: Dict[str, str] = {}


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
            return _redis_client
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        c = _r.Redis(host=host, port=port, decode_responses=True)
        c.ping()
        _redis_client = c
        return _redis_client
    except Exception:
        return None


def _rget(key: str) -> Optional[str]:
    c = _get_redis()
    if c:
        try:
            return c.get(key)
        except Exception:
            pass
    return _memory_fallback.get(key)


def _rset(key: str, value: str, ttl: int):
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
# 1. SHORT-TERM TURN CACHE
# ---------------------------------------------------------------------------

def _turn_cache_key(session_id: str) -> str:
    return f"session:{session_id}:recent_turns"


def get_recent_turns(session_id: str) -> List[TurnData]:
    """Get last 3-4 turns for the session."""
    raw = _rget(_turn_cache_key(session_id))
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [TurnData.from_dict(t) for t in data]
    except Exception:
        return []


def save_turn(session_id: str, turn: TurnData):
    """Save a turn to the cache, keeping only last MAX_CACHED_TURNS."""
    turns = get_recent_turns(session_id)
    turns.append(turn)
    turns = turns[-MAX_CACHED_TURNS:]  # Keep only last 4
    
    data = [t.to_dict() for t in turns]
    _rset(_turn_cache_key(session_id), json.dumps(data), ttl=TURN_CACHE_TTL)


def clear_turn_cache(session_id: str):
    """Clear turn cache for a session."""
    _rdel(_turn_cache_key(session_id))


# ---------------------------------------------------------------------------
# 2. SQL RESULT CACHE
# ---------------------------------------------------------------------------

def _normalize_query(query: str) -> str:
    """Normalize a query string for cache key generation."""
    # Lowercase, remove extra whitespace
    q = query.lower().strip()
    q = re.sub(r'\s+', ' ', q)
    
    # Remove common filler words
    fillers = ['the', 'a', 'an', 'please', 'can', 'you', 'show', 'me', 'get', 'find']
    for f in fillers:
        q = re.sub(rf'\b{f}\b', '', q)
    
    # Remove punctuation
    q = re.sub(r'[^\w\s]', '', q)
    
    return q.strip()


def _sql_cache_key(workspace_id: str, query: str) -> str:
    """Generate cache key for SQL result."""
    normalized = _normalize_query(query)
    query_hash = hashlib.md5(normalized.encode()).hexdigest()[:12]
    return f"sql_cache:{workspace_id}:{query_hash}"


def get_sql_cache(workspace_id: str, query: str) -> Optional[SQLCacheEntry]:
    """Check if SQL result is cached for this query."""
    raw = _rget(_sql_cache_key(workspace_id, query))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        entry = SQLCacheEntry.from_dict(data)
        # Check if expired (extra safety)
        if time.time() - entry.timestamp > SQL_CACHE_TTL:
            return None
        return entry
    except Exception:
        return None


def set_sql_cache(workspace_id: str, query: str, entry: SQLCacheEntry):
    """Cache SQL result."""
    _rset(_sql_cache_key(workspace_id, query), json.dumps(entry.to_dict()), ttl=SQL_CACHE_TTL)


def clear_sql_cache(workspace_id: str):
    """Clear all SQL cache for workspace (called on data updates)."""
    # Note: In production, use scan+delete pattern
    # For now, we rely on TTL
    pass


# ---------------------------------------------------------------------------
# 3. CACHE ANSWER CHECK (Before Planner)
# ---------------------------------------------------------------------------

def check_cache_for_answer(
    query: str,
    session_id: str,
    workspace_id: str
) -> Optional[Dict[str, Any]]:
    """
    Check if query can be answered from cached results.
    
    Returns:
        {
            "answered": True,
            "answer": str,
            "source": "turn_cache" | "sql_cache",
            "data": Optional[List]
        }
        or None if not answerable from cache
    """
    query_lower = query.lower()
    
    # Check recent turns first
    turns = get_recent_turns(session_id)
    
    for turn in reversed(turns):  # Most recent first
        if _queries_match(query_lower, turn.query.lower()):
            # Exact or very similar query — return cached result
            return {
                "answered": True,
                "answer": _format_cached_answer(turn),
                "source": "turn_cache",
                "data": turn.result_preview,
                "sql_used": turn.sql_query,
            }
    
    # Check SQL cache
    sql_entry = get_sql_cache(workspace_id, query)
    if sql_entry:
        return {
            "answered": True,
            "answer": f"Here are the results ({sql_entry.row_count} rows):",
            "source": "sql_cache",
            "data": sql_entry.rows[:50],
            "sql_used": sql_entry.sql,
        }
    
    return None


def _queries_match(query1: str, query2: str) -> bool:
    """Check if two queries are semantically similar enough to reuse."""
    n1 = _normalize_query(query1)
    n2 = _normalize_query(query2)
    
    # Exact match after normalization
    if n1 == n2:
        return True
    
    # Word overlap threshold (80%)
    words1 = set(n1.split())
    words2 = set(n2.split())
    if not words1 or not words2:
        return False
    
    overlap = len(words1 & words2) / max(len(words1), len(words2))
    return overlap >= 0.8


def _format_cached_answer(turn: TurnData) -> str:
    """Format a cached turn as an answer."""
    if turn.result_preview:
        preview = ", ".join(str(v) for v in turn.result_preview[:5])
        return f"From the previous query about {turn.dataset_used}: {preview}"
    return f"Results from previous {turn.intent} query."


# ---------------------------------------------------------------------------
# 4. PARTIAL CACHE REUSE
# ---------------------------------------------------------------------------

def get_available_cached_results(session_id: str, workspace_id: str) -> Dict[str, Any]:
    """
    Get available cached results for the planner.
    
    Returns dict like:
        {
            "employees_list": {
                "columns": ["name", "email", "rating"],
                "row_count": 5,
                "dataset": "employee_csv"
            },
            "top_performers": {...}
        }
    """
    turns = get_recent_turns(session_id)
    
    available = {}
    for turn in turns:
        if turn.dataset_used and turn.result_preview:
            # Create a semantic key from the query
            key = _extract_cache_key(turn.query)
            if key:
                available[key] = {
                    "columns": turn.columns_used,
                    "row_count": turn.row_count,
                    "dataset": turn.dataset_used,
                    "query": turn.query,
                    "sql": turn.sql_query,
                }
    
    return available


def _extract_cache_key(query: str) -> Optional[str]:
    """Extract a semantic cache key from query."""
    query_lower = query.lower()
    
    # Common patterns
    patterns = [
        (r'(?:top|best|highest)\s+(\w+)', r'top_\1'),
        (r'(?:bottom|worst|lowest)\s+(\w+)', r'bottom_\1'),
        (r'employees?\s+with\s+(\w+)', r'\1_employees'),
        (r'(\w+)\s+list', r'\1_list'),
        (r'all\s+(\w+)', r'all_\1'),
    ]
    
    for pattern, replacement in patterns:
        match = re.search(pattern, query_lower)
        if match:
            return re.sub(pattern, replacement, match.group(0)).replace(' ', '_')
    
    # Fallback: first 3 significant words
    words = [w for w in query_lower.split() if len(w) > 2 and w not in 
             ('the', 'and', 'for', 'from', 'with', 'get', 'show', 'find')]
    if words:
        return '_'.join(words[:3])
    
    return None


def can_reuse_for_email(query: str, session_id: str) -> Optional[Dict]:
    """
    Check if we can reuse cached results for an email workflow.
    
    Returns the cached data if available, None otherwise.
    """
    query_lower = query.lower()
    
    # Check for email-related query
    if not re.search(r'\b(email|send|mail|notify)\b', query_lower):
        return None
    
    # Look for pronouns referencing previous results
    if re.search(r'\b(them|they|those|these|the\s+(?:employees?|users?|results?))\b', query_lower):
        turns = get_recent_turns(session_id)
        for turn in reversed(turns):
            if turn.intent == "structured" and turn.result_preview:
                return {
                    "rows": turn.result_preview,
                    "columns": turn.columns_used,
                    "dataset": turn.dataset_used,
                    "sql": turn.sql_query,
                }
    
    return None


# ---------------------------------------------------------------------------
# 5. MEMORY CONTEXT COMPRESSION
# ---------------------------------------------------------------------------

def build_compressed_context(
    query: str,
    session_id: str,
    workspace_id: str,
    memory_facts: str,
    metadata_list: List[Dict]
) -> str:
    """
    Build a compressed context string (<120 tokens) for the planner.
    
    Format:
        User Query: <query>
        
        Recent Memory:
        - <dataset> retrieved from <table>
        
        Schema:
        <table>(col1, col2, col3)
        
        Facts:
        - <relevant fact>
    """
    parts = []
    token_budget = MAX_CONTEXT_TOKENS
    
    # 1. Recent structured memory (from turns)
    turns = get_recent_turns(session_id)
    if turns:
        recent_turn = turns[-1]
        if recent_turn.dataset_used:
            line = f"{recent_turn.dataset_used} retrieved"
            if recent_turn.columns_used:
                cols = ", ".join(recent_turn.columns_used[:4])
                line += f" ({cols})"
            parts.append(f"Recent: {line}")
            token_budget -= len(line.split())
    
    # 2. Dataset schema hints (most relevant)
    if metadata_list:
        # Find most relevant dataset based on query
        best_meta = _find_relevant_dataset(query, metadata_list)
        if best_meta:
            table_name = best_meta.get("tableName") or best_meta.get("file_name", "")
            columns = best_meta.get("columns", [])[:6]
            if table_name and columns:
                schema = f"{table_name}({', '.join(columns)})"
                parts.append(f"Schema: {schema}")
                token_budget -= len(schema.split())
    
    # 3. Memory facts (truncated to fit)
    if memory_facts and token_budget > 20:
        facts_lines = memory_facts.strip().split('\n')[:2]  # Max 2 facts
        for fact in facts_lines:
            fact = fact.strip()[:100]
            if fact:
                parts.append(fact)
                token_budget -= len(fact.split())
                if token_budget <= 10:
                    break
    
    return "\n".join(parts)


def _find_relevant_dataset(query: str, metadata_list: List[Dict]) -> Optional[Dict]:
    """Find the most relevant dataset for the query."""
    query_lower = query.lower()
    
    # Score each dataset
    best_score = 0
    best_meta = None
    
    for meta in metadata_list:
        score = 0
        
        # Check filename match
        file_name = (meta.get("fileName") or meta.get("file_name", "")).lower()
        if file_name:
            file_words = set(re.findall(r'\w+', file_name))
            query_words = set(re.findall(r'\w+', query_lower))
            score += len(file_words & query_words) * 3
        
        # Check column match
        columns = meta.get("columns", [])
        for col in columns:
            if col.lower() in query_lower:
                score += 2
        
        # Check keywords match
        keywords = meta.get("keywords", [])
        for kw in keywords:
            if kw.lower() in query_lower:
                score += 1
        
        if score > best_score:
            best_score = score
            best_meta = meta
    
    return best_meta


# ---------------------------------------------------------------------------
# 6. WORKSPACE INTELLIGENCE
# ---------------------------------------------------------------------------

def _workspace_intel_key(workspace_id: str) -> str:
    return f"workspace_intel:{workspace_id}"


def get_workspace_intelligence(workspace_id: str) -> WorkspaceIntelligence:
    """Get learned workspace knowledge."""
    raw = _rget(_workspace_intel_key(workspace_id))
    if not raw:
        return WorkspaceIntelligence(
            column_mappings={},
            frequent_datasets=[],
            frequent_workflows=[],
            recent_queries=[],
        )
    try:
        data = json.loads(raw)
        return WorkspaceIntelligence.from_dict(data)
    except Exception:
        return WorkspaceIntelligence(
            column_mappings={},
            frequent_datasets=[],
            frequent_workflows=[],
            recent_queries=[],
        )


def save_workspace_intelligence(workspace_id: str, intel: WorkspaceIntelligence):
    """Save workspace intelligence."""
    _rset(_workspace_intel_key(workspace_id), json.dumps(intel.to_dict()), ttl=WORKSPACE_INTEL_TTL)


def update_workspace_intelligence(
    workspace_id: str,
    dataset_used: Optional[str] = None,
    workflow_used: Optional[str] = None,
    column_mapping: Optional[Tuple[str, str]] = None,
    query: Optional[str] = None
):
    """Update workspace intelligence with new learnings."""
    intel = get_workspace_intelligence(workspace_id)
    
    # Update frequent datasets
    if dataset_used:
        if dataset_used in intel.frequent_datasets:
            intel.frequent_datasets.remove(dataset_used)
        intel.frequent_datasets.insert(0, dataset_used)
        intel.frequent_datasets = intel.frequent_datasets[:10]  # Keep top 10
    
    # Update frequent workflows
    if workflow_used:
        if workflow_used in intel.frequent_workflows:
            intel.frequent_workflows.remove(workflow_used)
        intel.frequent_workflows.insert(0, workflow_used)
        intel.frequent_workflows = intel.frequent_workflows[:5]
    
    # Update column mappings
    if column_mapping:
        alias, actual = column_mapping
        intel.column_mappings[alias.lower()] = actual
    
    # Update recent queries
    if query:
        pattern = _extract_query_pattern(query)
        if pattern and pattern not in intel.recent_queries:
            intel.recent_queries.insert(0, pattern)
            intel.recent_queries = intel.recent_queries[:10]
    
    save_workspace_intelligence(workspace_id, intel)


def _extract_query_pattern(query: str) -> Optional[str]:
    """Extract a generalized query pattern."""
    # Replace specific values with placeholders
    pattern = query.lower()
    pattern = re.sub(r"['\"].*?['\"]", '<VAL>', pattern)
    pattern = re.sub(r'\b\d+\b', '<NUM>', pattern)
    pattern = re.sub(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', '<NAME>', query)
    
    # Simplify
    pattern = re.sub(r'\s+', ' ', pattern).strip()
    
    if len(pattern) > 10:
        return pattern[:100]
    return None


def resolve_column_alias(workspace_id: str, alias: str) -> str:
    """Resolve a column alias to actual column name using learned mappings."""
    intel = get_workspace_intelligence(workspace_id)
    return intel.column_mappings.get(alias.lower(), alias)


def get_workspace_context_for_planner(workspace_id: str) -> str:
    """Get workspace intelligence as context string for planner."""
    intel = get_workspace_intelligence(workspace_id)
    
    parts = []
    
    if intel.column_mappings:
        mappings = ", ".join(f"{k}→{v}" for k, v in list(intel.column_mappings.items())[:5])
        parts.append(f"Column aliases: {mappings}")
    
    if intel.frequent_datasets:
        parts.append(f"Frequent tables: {', '.join(intel.frequent_datasets[:3])}")
    
    return " | ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# 7. MEMORY EXTRACTION AFTER QUERY
# ---------------------------------------------------------------------------

def extract_turn_metadata(
    query: str,
    intent: str,
    sql_query: str,
    result_rows: List[Dict],
    columns: List[str],
    dataset: str,
    entities: List[str]
) -> TurnData:
    """
    Extract structured metadata from a completed query turn.
    This is stored in the turn cache for follow-up resolution.
    """
    # Extract result preview (top values for key columns)
    preview = []
    if result_rows:
        for row in result_rows[:3]:
            # Get most relevant value from row
            for key in ["name", "email", "id", "title"]:
                if key in row:
                    preview.append(str(row[key])[:50])
                    break
            else:
                # Take first value
                if row:
                    preview.append(str(list(row.values())[0])[:50])
    
    return TurnData(
        query=query,
        intent=intent,
        dataset_used=dataset,
        sql_query=sql_query,
        result_preview=preview,
        columns_used=columns,
        entities_detected=entities,
        timestamp=time.time(),
        row_count=len(result_rows) if result_rows else 0,
    )


def learn_from_turn(
    workspace_id: str,
    session_id: str,
    turn: TurnData
):
    """
    Learn from a completed turn:
    1. Save to turn cache
    2. Update workspace intelligence  
    3. Cache SQL result if applicable
    """
    # 1. Save to turn cache
    save_turn(session_id, turn)
    
    # 2. Update workspace intelligence
    update_workspace_intelligence(
        workspace_id=workspace_id,
        dataset_used=turn.dataset_used,
        workflow_used="email_workflow" if "email" in turn.intent else None,
        query=turn.query,
    )
    
    # 3. Learn column mappings
    if turn.columns_used and turn.query:
        _learn_column_mappings(workspace_id, turn.query, turn.columns_used)


def _learn_column_mappings(workspace_id: str, query: str, columns: List[str]):
    """Learn column name aliases from query patterns."""
    query_lower = query.lower()
    
    # Common alias patterns
    alias_patterns = [
        (r'\bemails?\b', 'email'),
        (r'\bratings?\b', 'rating'),
        (r'\bnames?\b', 'name'),
        (r'\bsalar(?:y|ies)\b', 'salary'),
        (r'\btitles?\b', 'title'),
        (r'\bdepartments?\b', 'department'),
        (r'\bteam\b', 'department'),
    ]
    
    for pattern, standard in alias_patterns:
        if re.search(pattern, query_lower):
            # Check if a column matches
            for col in columns:
                if standard in col.lower():
                    # User's term maps to actual column
                    match = re.search(pattern, query_lower)
                    if match:
                        alias = match.group(0)
                        if alias != col:
                            update_workspace_intelligence(
                                workspace_id, column_mapping=(alias, col)
                            )


# ---------------------------------------------------------------------------
# 8. RAG QUERY REWRITING
# ---------------------------------------------------------------------------

def rewrite_rag_query(
    query: str,
    session_id: str,
    workspace_id: str,
    memory_facts: str
) -> str:
    """
    Rewrite a RAG query for better vector search results.
    
    Enhancements:
    - Expand abbreviations
    - Add context from memory
    - Include dataset hints
    """
    expanded = query
    
    # 1. Expand common abbreviations
    abbreviations = {
        r'\bhw\b': 'homework',
        r'\bpromo\b': 'promotion',
        r'\binfo\b': 'information',
        r'\bdocs?\b': 'document',
        r'\breqs?\b': 'requirement',
        r'\bspecs?\b': 'specification',
    }
    for abbr, full in abbreviations.items():
        expanded = re.sub(abbr, full, expanded, flags=re.IGNORECASE)
    
    # 2. Add context from recent turns if pronoun detected
    turns = get_recent_turns(session_id)
    if turns and re.search(r'\b(it|they|this|that|those|these)\b', query.lower()):
        recent = turns[-1]
        if recent.dataset_used:
            expanded = f"{recent.dataset_used}: {expanded}"
    
    # 3. Add workspace-specific context
    intel = get_workspace_intelligence(workspace_id)
    if intel.frequent_datasets:
        # If query is vague, add most relevant dataset context
        if len(query.split()) <= 3:
            expanded = f"{intel.frequent_datasets[0]} {expanded}"
    
    return expanded


# ---------------------------------------------------------------------------
# 9. FAST PATH CHECKS
# ---------------------------------------------------------------------------

def should_skip_planner(
    query: str,
    session_id: str,
    workspace_id: str,
    workflow_active: bool
) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Check if we can skip the planner entirely.
    
    Returns:
        (skip: bool, reason: str, cached_answer: dict)
    """
    # 1. Check if exact answer in cache
    cached = check_cache_for_answer(query, session_id, workspace_id)
    if cached and cached.get("answered"):
        return True, "cache_hit", cached
    
    # 2. Check if follow-up with available data
    if can_reuse_for_email(query, session_id):
        return False, "email_with_cache", None
    
    # 3. Check if simple metadata question
    # (This is handled elsewhere, but we could optimize here)
    
    return False, None, None


def should_skip_rag(intent: str) -> bool:
    """Check if RAG pipeline should be skipped."""
    return intent in ("structured", "metadata", "greeting", "email")


def should_skip_sql(intent: str) -> bool:
    """Check if SQL generation should be skipped."""
    return intent in ("rag", "greeting", "email", "clarification")


# ---------------------------------------------------------------------------
# 10. CLEANUP
# ---------------------------------------------------------------------------

def clear_session_cache(session_id: str):
    """Clear all session-related caches."""
    clear_turn_cache(session_id)


def clear_workspace_cache(workspace_id: str):
    """Clear all workspace-related caches (on data update)."""
    clear_sql_cache(workspace_id)
    # Don't clear workspace intelligence - it's persistent knowledge
