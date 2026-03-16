"""
router/unified_router.py — Optimized v5

ONE LLM call replaces:
  - fast_router.py        (LLM #1 — intent classification)
  - multi_step_planner.py (LLM #2 — plan generation)
  - planner.py route_query() (LLM #3 — another intent classification)
  - planner.py generate_sql() (LLM #4 — SQL generation)

For a simple structured query: 5 LLM calls → 1 LLM call + 1 answer LLM call = 2 total.

Output JSON schema:
{
  "intent": "structured|rag|hybrid|email|metadata|greeting|clarification|out_of_scope",
  "is_multi_step": false,
  "is_continuation": false,        # True if this replies to an active workflow
  "new_workflow_needed": false,    # True if fresh query should cancel active workflow
  "rewritten_query": "...",        # standalone, pronouns resolved
  "sql": "SELECT ...",             # only if intent=structured/hybrid
  "rag_query": "...",              # only if intent=rag/hybrid
  "steps": [],                     # only if is_multi_step=true
  "confidence": 0.9
}

Multi-step steps schema (same as before for compatibility):
{
  "step_id": 1,
  "intent": "structured|rag|email",
  "query": "...",
  "sql": "SELECT ...",             # pre-generated SQL for structured steps
  "depends_on": [],
  "cache_key": "high_performers",
  "uses_cache_key": null
}
"""

import json
import re
import sqlite3
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from llm.factory import get_llm

VALID_INTENTS = {
    "structured", "rag", "hybrid", "email",
    "metadata", "greeting", "clarification", "out_of_scope",
}

# ---------------------------------------------------------------------------
# HEURISTIC FAST PATHS (zero LLM cost)
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|howdy|sup|yo|good\s*(morning|afternoon|evening|night)|"
    r"what'?s\s*up|how\s*are\s*you|greetings|namaste|hola|hii+|heyy+)[\s!?.]*$",
    re.IGNORECASE,
)

_CONFIRM_RE = re.compile(
    r"^\s*(yes|send|confirm|ok|okay|sure|yep|yeah|proceed|go ahead|"
    r"do it|send it|looks good|looks fine)[!.,\s]*$",
    re.IGNORECASE,
)

_CANCEL_RE = re.compile(
    r"^\s*(no|cancel|stop|abort|nevermind|never mind|don'?t send)[!.,\s]*$",
    re.IGNORECASE,
)

_FRESH_QUERY_RE = re.compile(
    r"^(get|show|find|list|give|fetch|what|who|how many|count|"
    r"which|display|select|retrieve|tell me|can you|could you)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# RESULT DATACLASS
# ---------------------------------------------------------------------------

@dataclass
class UnifiedDecision:
    intent: str
    is_multi_step: bool
    is_continuation: bool
    new_workflow_needed: bool
    rewritten_query: str
    sql: Optional[str] = None
    rag_query: Optional[str] = None
    steps: List[Dict] = field(default_factory=list)
    confidence: float = 0.9


# ---------------------------------------------------------------------------
# METADATA HELPERS
# ---------------------------------------------------------------------------

def _build_schema_block(metadata_list: List[Dict], workspace_id: str) -> str:
    """Build a rich schema block for structured datasets. Fetches live DDL from SQLite."""
    from mongo_utils import get_workspace_sqlite_path
    from config import SQLITE_DIR

    parts = []
    for meta in metadata_list:
        mode = meta.get("storageMode") or meta.get("type", "rag")
        name = meta.get("fileName") or meta.get("file_name", "unknown")
        table = meta.get("tableName", "")

        if mode in ("sqlite", "hybrid") and table:
            # Get live DDL + samples
            db_path = get_workspace_sqlite_path(workspace_id)
            if not db_path or not os.path.exists(db_path):
                db_path = os.path.join(SQLITE_DIR, workspace_id, f"data_{workspace_id}.db")

            ddl = f"Table: {table}"
            col_samples = {}
            schema_sample = []

            if db_path and os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    row = cur.fetchone()
                    if row:
                        ddl = row[0]
                    cur.execute(f'PRAGMA table_info("{table}")')
                    col_infos = cur.fetchall()
                    cur.execute(f'SELECT * FROM "{table}" LIMIT 3')
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
                    schema_sample = [dict(zip(cols, r)) for r in rows]
                    for ci in col_infos:
                        cname, ctype = ci[1], (ci[2] or "").upper()
                        if "TEXT" in ctype or "VARCHAR" in ctype or not ctype:
                            try:
                                cur.execute(
                                    f'SELECT DISTINCT "{cname}" FROM "{table}" '
                                    f'WHERE "{cname}" IS NOT NULL LIMIT 10'
                                )
                                vals = [r[0] for r in cur.fetchall() if r[0]]
                                if 2 <= len(vals) <= 12:
                                    col_samples[cname] = vals
                            except Exception:
                                pass
                    conn.close()
                except Exception as e:
                    print(f"WARNING [unified_router]: schema fetch failed: {e}")

            sample_str = ""
            if schema_sample:
                sample_str = "\nSample rows:\n" + "\n".join(
                    "  " + ", ".join(f"{k}={v}" for k, v in list(r.items())[:6])
                    for r in schema_sample[:3]
                )
            col_sample_str = ""
            if col_samples:
                col_sample_str = "\nColumn values:\n" + "\n".join(
                    f"  {c}: {', '.join(str(v) for v in vs[:5])}"
                    for c, vs in col_samples.items()
                )

            parts.append(
                f"FILE: {name} | MODE: {mode} | TABLE: {table}\n"
                f"SCHEMA:\n{ddl}{sample_str}{col_sample_str}"
            )
        else:
            summary = (meta.get("summary") or "")[:120]
            keywords = ", ".join(meta.get("keywords", [])[:6])
            parts.append(
                f"FILE: {name} | MODE: {mode}\n"
                f"Summary: {summary}\nKeywords: {keywords}"
            )

    return "\n\n---\n\n".join(parts) or "No datasets available."


def _build_datasets_hint(metadata_list: List[Dict]) -> str:
    """Compact hint for the prompt header."""
    parts = []
    for m in metadata_list[:5]:
        name = m.get("fileName") or m.get("file_name", "?")
        mode = m.get("storageMode") or m.get("type", "rag")
        cols = m.get("columns", [])[:5]
        line = f"{name}[{mode}]"
        if cols:
            line += f"({','.join(cols)})"
        parts.append(line)
    return ", ".join(parts) or "no datasets"


# ---------------------------------------------------------------------------
# PROMPT BUILDER
# ---------------------------------------------------------------------------

_SYSTEM = """You are a query intent classifier for a RAG data platform. Your ONLY job is to classify the intent and rewrite the query. Do NOT generate SQL.

=== INTENTS ===
structured  : data retrieval from tabular/CSV data (counts, filters, rankings, aggregations, listing records)
rag         : search unstructured docs (PDF, TXT) for explanations, summaries, descriptions
hybrid      : needs both tabular data AND document context
email       : user wants to draft/send email to people from a previous query result
metadata    : answerable from dataset stats alone (row count, column list only)
greeting    : small talk only
clarification: genuinely ambiguous — ONLY when truly impossible to determine intent
out_of_scope: completely unrelated to any uploaded dataset

=== RULES ===
1. If ANY dataset has storageMode=sqlite or hybrid AND query asks about records/values/filters/rankings → structured
2. "tell me", "show me", "get me", "list", "find" with employee/data columns → structured
3. Pronouns (him/her/them/those/his) → resolve using conversation_history, mark rewritten_query with actual entity
4. is_continuation=true ONLY for active workflow replies (yes/send/cancel/ok/change)
5. new_workflow_needed=true when fresh data query arrives while workflow is active
6. is_multi_step=true ONLY for TWO truly independent fetches (e.g. "top AND bottom performers")
7. NEVER ask clarification for singular/plural or obvious column references
8. Default uncertain → rag (never structured without tabular data present)

=== OUTPUT FORMAT ===
Return ONLY this JSON, no markdown:
{
  "intent": "structured",
  "is_multi_step": false,
  "is_continuation": false,
  "new_workflow_needed": false,
  "rewritten_query": "standalone question with pronouns resolved",
  "rag_query": null,
  "steps": [],
  "confidence": 0.95
}

For multi-step steps[] (omit sql field entirely — SQL is generated separately):
{
  "step_id": 1,
  "intent": "structured",
  "query": "standalone question for this step",
  "depends_on": [],
  "cache_key": "top_performers",
  "uses_cache_key": null
}
"""


def _build_prompt(
    question: str,
    schema_block: str,
    conversation_history: str,
    active_workflow: Optional[str],
) -> str:
    wf_line = f"\nActive workflow: {active_workflow}" if active_workflow else ""
    history_section = f"\nConversation history (last 3 turns):\n{conversation_history}" if conversation_history else ""
    return (
        f"{_SYSTEM}\n\n"
        f"=== WORKSPACE SCHEMA ==={wf_line}\n{schema_block}\n"
        f"{history_section}\n\n"
        f"=== USER QUERY ===\n{question}\n\n"
        f"Return the JSON now:"
    )


# ---------------------------------------------------------------------------
# PARSER
# ---------------------------------------------------------------------------

def _parse(raw: str, question: str) -> UnifiedDecision:
    raw = raw.strip()
    # Strip markdown fences
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # Find outermost JSON object
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Regex fallback
        intent_m = re.search(r'"intent"\s*:\s*"(\w+)"', raw)
        sql_m = re.search(r'"sql"\s*:\s*"([^"]+)"', raw)
        rq_m = re.search(r'"rewritten_query"\s*:\s*"([^"]+)"', raw)
        data = {
            "intent": intent_m.group(1) if intent_m else "rag",
            "is_multi_step": False,
            "is_continuation": False,
            "new_workflow_needed": False,
            "rewritten_query": rq_m.group(1) if rq_m else question,
            "sql": sql_m.group(1) if sql_m else None,
            "rag_query": None,
            "steps": [],
            "confidence": 0.4,
        }

    intent = str(data.get("intent", "rag")).lower()
    if intent not in VALID_INTENTS:
        intent = "rag"

    # Normalize steps
    raw_steps = data.get("steps", [])
    clean_steps = []
    for i, s in enumerate(raw_steps):
        step_intent = str(s.get("intent", "structured")).lower()
        if step_intent not in VALID_INTENTS:
            step_intent = "structured"
        clean_steps.append({
            "step_id": int(s.get("step_id", i + 1)),
            "intent": step_intent,
            "query": str(s.get("query", "")),
            "sql": s.get("sql"),
            "depends_on": [int(x) for x in s.get("depends_on", [])],
            "cache_key": s.get("cache_key") or f"step_{i+1}_result",
            "uses_cache_key": s.get("uses_cache_key"),
            "followup_sql_context": None,
        })

    sql = data.get("sql")
    # Clean SQL if present
    if sql:
        sql = sql.strip().strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()
        if not sql.upper().lstrip().startswith("SELECT"):
            idx = sql.upper().find("SELECT")
            sql = sql[idx:] if idx != -1 else None

    return UnifiedDecision(
        intent=intent,
        is_multi_step=bool(data.get("is_multi_step", False)),
        is_continuation=bool(data.get("is_continuation", False)),
        new_workflow_needed=bool(data.get("new_workflow_needed", False)),
        rewritten_query=str(data.get("rewritten_query") or question).strip(),
        sql=sql,
        rag_query=data.get("rag_query"),
        steps=clean_steps,
        confidence=float(data.get("confidence", 0.7)),
    )


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def unified_route(
    question: str,
    metadata_list: List[Dict],
    workspace_id: str,
    conversation_history: str = "",      # from Redis turn window
    active_workflow: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> UnifiedDecision:
    """
    Single LLM call that classifies intent + generates SQL + builds execution plan.
    Heuristic fast-paths skip the LLM entirely for greetings and simple workflow replies.
    """

    # ── HEURISTIC: greeting ──────────────────────────────────────────────────
    if _GREETING_RE.match(question.strip()):
        return UnifiedDecision(
            intent="greeting", is_multi_step=False,
            is_continuation=False, new_workflow_needed=False,
            rewritten_query=question, confidence=1.0,
        )

    # ── HEURISTIC: short workflow reply (yes/send/cancel/change ...) ─────────
    stripped = question.strip().lower().rstrip("!.,?")
    if active_workflow and len(question.split()) <= 6:
        if _CONFIRM_RE.match(stripped) or _CANCEL_RE.match(stripped):
            return UnifiedDecision(
                intent="email" if "email" in active_workflow else "structured",
                is_multi_step=False, is_continuation=True,
                new_workflow_needed=False,
                rewritten_query=question, confidence=1.0,
            )
        # Short edit request (e.g. "change the subject to...")
        if not _FRESH_QUERY_RE.match(question):
            return UnifiedDecision(
                intent="email" if "email" in active_workflow else "structured",
                is_multi_step=False, is_continuation=True,
                new_workflow_needed=False,
                rewritten_query=question, confidence=0.9,
            )

    # ── FOLLOW-UP ELABORATION FAST-PATH ─────────────────────────────────────
    # "in detail?", "tell me more", "elaborate", "more about this" etc.
    # These need conversation history to rewrite properly → always go to RAG.
    # We rewrite using last turn from conversation_history before calling LLM.
    _ELABORATION_RE = re.compile(
        r"^(in detail|more detail|tell me more|elaborate|explain more|"
        r"go deeper|expand on|more about|more info|can you tell me more|"
        r"and\?|details\?|detail\?|more\?|elaborate\?|"
        r"what (else|more)|anything else)[\s!?.]*$",
        re.IGNORECASE
    )
    _is_elaboration = bool(_ELABORATION_RE.match(question.strip())) or (
        len(question.split()) <= 4 and
        bool(re.search(r"(more|detail|deeper|elaborate|expand|further)", question, re.IGNORECASE)) and
        not bool(re.search(r"(employees?|data|csv|pdf|table|column)", question, re.IGNORECASE))
    )

    if _is_elaboration and conversation_history:
        # Extract last query from conversation history to rewrite elaboration
        last_q_match = re.search(r'User asked "([^"]+)"', conversation_history)
        if last_q_match:
            base_query = last_q_match.group(1)
            rewritten = f"Provide detailed information about: {base_query}"
            print(f"DEBUG [unified_router]: elaboration rewrite → '{rewritten}'")
            return UnifiedDecision(
                intent="rag", is_multi_step=False,
                is_continuation=False, new_workflow_needed=False,
                rewritten_query=rewritten, confidence=0.9,
            )

    # ── KEYWORD PRE-CLASSIFIER (0ms, no LLM) ───────────────────────────────
    # Catches obvious structured/rag/email queries without any LLM cost.
    # Skipped when active_workflow exists (continuation needs LLM context).
    keyword_intent = keyword_classify(question, has_active_workflow=bool(active_workflow))
    if keyword_intent:
        print(f"DEBUG [unified_router]: keyword fast-path → intent={keyword_intent}")
        # For structured queries we still need SQL — fall through to LLM
        # For RAG and email without SQL we can return directly
        if keyword_intent == "rag":
            return UnifiedDecision(
                intent="rag", is_multi_step=False,
                is_continuation=False, new_workflow_needed=False,
                rewritten_query=question, confidence=0.85,
            )
        # structured/email: fall through to LLM so SQL gets generated
        # (We set the intent hint but still need the full LLM call for SQL)

    # ── LLM CALL ─────────────────────────────────────────────────────────────
    schema_block = _build_schema_block(metadata_list, workspace_id)
    prompt = _build_prompt(question, schema_block, conversation_history, active_workflow)

    print(f"DEBUG [unified_router]: prompt_len={len(prompt)} chars")

    try:
        llm = get_llm(provider=llm_provider, temperature=0.0, max_tokens=1500)
        raw = llm.generate(prompt)
        print(f"DEBUG [unified_router]: raw_response=\n{raw[:600]}")
        decision = _parse(raw, question)
        print(
            f"DEBUG [unified_router]: intent={decision.intent} "
            f"multi={decision.is_multi_step} cont={decision.is_continuation} "
            f"sql={'yes' if decision.sql else 'no'} conf={decision.confidence:.2f}"
        )
        return decision

    except Exception as e:
        print(f"WARNING [unified_router]: LLM failed ({e}) — defaulting to rag")
        return UnifiedDecision(
            intent="rag", is_multi_step=False,
            is_continuation=False, new_workflow_needed=False,
            rewritten_query=question, confidence=0.3,
        )


# ---------------------------------------------------------------------------
# KEYWORD PRE-CLASSIFIER  (runs before LLM, O(1) cost)
# ---------------------------------------------------------------------------

# SQL / structured data keywords
_SQL_KEYWORDS = re.compile(
    r"\b(how many|count|total|sum|average|avg|minimum|maximum|min|max|"
    r"highest|lowest|percentage|ratio|statistics|stats|group by|filter|"
    r"per|distribution|trend|ranking|rank|top \d+|bottom \d+|"
    r"most|least|greater than|less than|between|sort|order by)\b",
    re.IGNORECASE,
)

# RAG / document search keywords
_RAG_KEYWORDS = re.compile(
    r"\b(explain|describe|definition|documentation|what is|what are|"
    r"who is|who are|when did|where is|why did|why is|why are|"
    r"tell me about|information about|details (of|about)|"
    r"how does|how do|overview of|summarize|summary of|"
    r"background|history of|purpose of)\b",
    re.IGNORECASE,
)

# Email / workflow keywords
_EMAIL_KEYWORDS = re.compile(
    r"\b(send email|send an email|email them|email him|email her|"
    r"notify|schedule|remind|automation|trigger|send report|"
    r"draft email|compose email|write email)\b",
    re.IGNORECASE,
)


def keyword_classify(question: str, has_active_workflow: bool = False) -> Optional[str]:
    """
    Zero-cost keyword-based intent pre-classifier.
    Runs BEFORE the LLM call in unified_route().

    Returns an intent string if confident, None if ambiguous (→ fallback to LLM).

    Rules:
    - If BOTH sql and rag keywords present → None (hybrid, let LLM decide)
    - If active workflow → None (continuation detection needs LLM context)
    - If email keywords → "email"
    - If sql keywords only → "structured"
    - If rag keywords only → "rag"
    - Otherwise → None
    """
    # Never short-circuit when a workflow is active — continuation needs LLM
    if has_active_workflow:
        return None

    q = question.strip()
    has_sql = bool(_SQL_KEYWORDS.search(q))
    has_rag = bool(_RAG_KEYWORDS.search(q))
    has_email = bool(_EMAIL_KEYWORDS.search(q))

    if has_email:
        return "email"

    # Both SQL and RAG signals → hybrid, let LLM classify
    if has_sql and has_rag:
        return None

    if has_sql:
        return "structured"

    if has_rag:
        return "rag"

    return None