"""
planner/multi_step_planner.py

Multi-step query planner.

Single LLM call (temp=0) that decides:
  - Is this a single-intent query, or does it need multiple steps?
  - For each step: intent, query text, depends_on[], cache_key

Output schema:
{
  "is_multi_step": false,
  "steps": [
    {
      "step_id": 1,
      "intent": "structured | rag | hybrid | email | metadata",
      "query": "...",
      "depends_on": [],            # step_ids that must finish first
      "cache_key": "high_performers",   # name for caching this step's result
      "uses_cache_key": null       # for email: which cache to pull recipients from
    }
  ]
}

Execution batches are derived from the DAG:
  Batch 1  = steps where depends_on = []          → run in parallel
  Batch 2  = steps whose deps are all in done set → run in parallel
  ...

Single-step queries return is_multi_step=false with one step.
The single-step path still goes through the same execution engine.
"""

import json
import re
from typing import Any, Dict, List, Optional

from llm.factory import get_llm


# ---------------------------------------------------------------------------
# FOLLOW-UP / CONTEXTUAL REPLY DETECTION
# ---------------------------------------------------------------------------

# Pronouns and explicit references that mean "the previous results/person"
_FOLLOWUP_RE = re.compile(
    r'\b(him(e?)|her|his|he|she|they|them|those|these|'
    r'the results?|the data|the list|the (above|previous|last)|'
    r'those (people|employees|users|customers|records|names?)|'
    r'email (him(e?)|her|them|those|these)|'
    r'send (to |)(him(e?)|her|them|those|these)|'
    r'the (above|previous|last) (results?|query|data|list))\b',
    re.IGNORECASE,
)

_FRESH_QUESTION_RE = re.compile(
    r'^(who|what|where|when|why|how|which|show|find|get|list|'
    r'give|tell|can you tell|could you|please)\b',
    re.IGNORECASE,
)

# Confirmation words (user is replying to a clarification question)
_CONFIRMATION_RE = re.compile(
    r'^(yes|yeah|yep|ok|okay|sure|correct|right|exactly|'
    r'confirmed?|proceed|go ahead|send|do it)[\s!.,]*$',
    re.IGNORECASE,
)


def is_explicit_followup(question: str) -> bool:
    """True if question references prior results via pronoun or explicit phrase."""
    return bool(_FOLLOWUP_RE.search(question))


def is_contextual_reply(question: str) -> bool:
    """
    True when the message is almost certainly a reply to the previous turn
    rather than a new independent question.  Criteria:
      - Pure confirmation word ("yes", "ok", "send it")
      - Very short (≤4 words) — likely answering a clarification question
      - Contains follow-up pronoun
    """
    stripped = question.strip()
    if _CONFIRMATION_RE.match(stripped):
        return True
    if len(stripped.split()) <= 4 and not _FRESH_QUESTION_RE.match(stripped):
        return True
    if is_explicit_followup(stripped):
        return True
    return False


# ---------------------------------------------------------------------------
# PROMPT
# ---------------------------------------------------------------------------

_SYSTEM = """You are a query planning assistant for a RAG data platform.

Given a user's question and the workspace metadata, output a JSON execution plan.

INTENTS:
  structured  – counts, filters, aggregations, rankings from tabular data (CSV/Excel)
  rag         – explanations, summaries from unstructured documents (PDF, TXT)
  hybrid      – needs both SQL numbers AND document context
  email       – draft + stage an email for recipients from a previous step
  metadata    – question answerable from dataset stats alone (row counts, column lists, min/max/avg)

MULTI-STEP RULES:
  - Use is_multi_step=true when the question asks for TWO OR MORE logically separate retrievals
    e.g. "get top performers AND bottom performers, then email both groups separately"
  - Use is_multi_step=false for single retrievals even if they need SQL + answer generation
  - email steps MUST have depends_on = [step_id of the structured step that provides recipients]
  - email steps MUST set uses_cache_key = cache_key of the step they depend on

CACHE KEYS:
  - Give every structured/rag step a short snake_case cache_key describing its result
    e.g. "high_performers", "terminated_employees", "cloud_computing_summary"
  - email steps do NOT need a cache_key, only uses_cache_key

OUTPUT — return ONLY this JSON, no markdown, no explanation:
{
  "is_multi_step": true | false,
  "steps": [
    {
      "step_id": 1,
      "intent": "structured",
      "query": "standalone question for this step",
      "depends_on": [],
      "cache_key": "high_performers",
      "uses_cache_key": null
    }
  ]
}"""


def _build_prompt(question: str, memory_context: str, metadata_summary: str, 
                  is_followup: bool = False) -> str:
    """
    Build prompt for the multi-step planner.
    
    Args:
        question: User's question
        memory_context: Context from previous turns (entities, SQL, results)
        metadata_summary: Summary of available datasets
        is_followup: Whether this is a follow-up question with pronouns
    """
    ctx = ""
    if memory_context:
        if is_followup:
            ctx = f"""
IMPORTANT FOLLOW-UP CONTEXT (resolve pronouns using this):
{memory_context}

When you see pronouns like "his", "her", "them", "their", "those", "these":
- Replace them with the actual entities/values from the previous context
- Use the same SQL WHERE conditions as the previous query but with the new SELECT columns
"""
        else:
            ctx = f"\nPrevious conversation:\n{memory_context}\n"
    
    return (
        f"Workspace documents:\n{metadata_summary}\n"
        f"{ctx}\n"
        f"User question: {question}\n\n"
        f"Output the JSON execution plan:"
    )


def _summarise_metadata(metadata_list: List[Dict]) -> str:
    parts = []
    for m in metadata_list[:5]:
        name    = m.get("fileName") or m.get("file_name", "unknown")
        mode    = m.get("storageMode") or m.get("type", "rag")
        summary = (m.get("summary") or "")[:120]
        cols    = m.get("columns", [])[:6]
        col_str = ", ".join(cols) if cols else ""
        line    = f"  - {name} [{mode}]"
        if summary:
            line += f": {summary}"
        if col_str:
            line += f" | columns: {col_str}"
        parts.append(line)
    return "\n".join(parts) or "No documents available."


# ---------------------------------------------------------------------------
# PARSE
# ---------------------------------------------------------------------------

_VALID_INTENTS = {"structured", "rag", "hybrid", "email", "metadata",
                  "greeting", "clarification", "out_of_scope"}


def _parse_plan(raw: str) -> Optional[Dict[str, Any]]:
    raw = raw.strip()
    # Strip markdown fences
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    # Find JSON object
    if not raw.startswith("{"):
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Regex fallback
        intent_m = re.search(r'"intent"\s*:\s*"(\w+)"', raw)
        if intent_m:
            intent = intent_m.group(1)
            if intent not in _VALID_INTENTS:
                intent = "rag"
            return {
                "is_multi_step": False,
                "steps": [{
                    "step_id": 1, "intent": intent,
                    "query": "", "depends_on": [],
                    "cache_key": "result", "uses_cache_key": None
                }]
            }
        return None

    # Normalise
    steps = data.get("steps", [])
    clean_steps = []
    for i, s in enumerate(steps):
        intent = str(s.get("intent", "rag")).lower()
        if intent not in _VALID_INTENTS:
            intent = "rag"
        clean_steps.append({
            "step_id":       int(s.get("step_id", i + 1)),
            "intent":        intent,
            "query":         str(s.get("query", "")),
            "depends_on":    [int(x) for x in s.get("depends_on", [])],
            "cache_key":     s.get("cache_key") or f"step_{i+1}_result",
            "uses_cache_key": s.get("uses_cache_key"),
        })

    is_multi = bool(data.get("is_multi_step", len(clean_steps) > 1))
    return {"is_multi_step": is_multi, "steps": clean_steps}


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

# Pronouns that mean "the previously retrieved person"
_PRONOUN_RE = re.compile(
    r'''\b(him|her|them|those|these|the\s+employee|the\s+person)\b''',
    re.IGNORECASE,
)


def _try_email_name_heuristic(
    question: str,
    explicit_followup: bool,
) -> Optional[Dict[str, Any]]:
    """
    Detect "email/draft/send [person name]" and return a guaranteed 2-step plan.
    Bypasses LLM for this common pattern — avoids truncation + wrong intent.

    Name extraction rules:
      - Look for "for/to/email [Name]" where Name is not a stopword
      - Stop capture at conjunctions (and/or/but/once...)
      - If only pronouns found (him/her/them) and there IS prior context → None
        (let the LLM plan use uses_cache_key from prior structured step)
    """
    q_lower = question.lower()
    email_words = ("send", "email", "draft", "write", "compose", "prepare", "mail")
    if not any(w in q_lower for w in email_words):
        return None

    _STOP_NAMES = {
        "me","us","him","her","them","all","the","this","that","it","an","a",
        "and","or","but","now","once","when","after","then","so","please","their",
        "our","your","his","hers","its","email","mail","draft","write","compose",
        "for","to","send","promotion","notification","update","results","data",
        "of","termination",
    }

    def _extract_name(q: str) -> Optional[str]:
        pats = [
            r'\b(?:for)\s+([A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)',
            r'\b(?:to)\s+([A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)',
            r'\b(?:email|mail)\s+([A-Za-z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]+)?)',
        ]
        for pat in pats:
            m = re.search(pat, q, re.IGNORECASE)
            if not m:
                continue
            words = m.group(1).strip().split()
            clean = []
            for w in words[:2]:
                if w.lower() in _STOP_NAMES:
                    break
                clean.append(w)
            name = ' '.join(clean)
            if len(name) >= 3 and name.lower() not in _STOP_NAMES:
                return name
        return None

    person_name = _extract_name(question)
    has_pronoun = bool(_PRONOUN_RE.search(question))

    if person_name:
        print(f"DEBUG [heuristic]: email for named person '{person_name}'")
        ck = re.sub(r'[^a-z0-9]+', '_', person_name.lower()) + "_email_data"
        return {
            "is_multi_step": True,
            "steps": [
                {
                    "step_id":        1,
                    "intent":         "structured",
                    "query":          f"Get name and email address of {person_name} from the employees dataset",
                    "is_follow_up":   False,
                    "depends_on":     [],
                    "cache_key":      ck,
                    "uses_cache_key": None,
                },
                {
                    "step_id":        2,
                    "intent":         "email",
                    "query":          question,
                    "is_follow_up":   True,
                    "depends_on":     [1],
                    "cache_key":      "email_draft",
                    "uses_cache_key": ck,
                },
            ],
        }

    # Pronoun-only: let LLM plan handle it via uses_cache_key
    return None


def _extract_sql_context(memory_context: str) -> Optional[str]:
    """
    Extract SQL WHERE clause context from memory_context.
    This helps the router know which column was used in the previous query.
    
    Returns:
        String like "Party_2 column contains 'vikash kumar...'" or None
    """
    if not memory_context:
        return None
    
    # Look for SQL: line
    sql_match = re.search(r'SQL used:\s*(.+?)(?:\n|$)', memory_context, re.IGNORECASE)
    if not sql_match:
        return None
    
    sql = sql_match.group(1).strip()
    
    # Extract WHERE clause column and value
    where_match = re.search(r'WHERE\s+["\']?(\w+)["\']?\s+(?:LIKE|=)\s+[\'"]?%?([^%\'\"]+)', sql, re.IGNORECASE)
    if where_match:
        col_name = where_match.group(1)
        value = where_match.group(2).strip()
        return f"Previous query used column '{col_name}' to find '{value}'"
    
    return None


def plan_query(
    question: str,
    memory_context: str,
    metadata_list: List[Dict],
    llm_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call the multi-step planner LLM and return the execution plan.

    Returns:
        {
          "is_multi_step": bool,
          "steps": [ { step_id, intent, query, depends_on, cache_key, uses_cache_key, followup_sql_context } ]
        }
    Falls back to a single-step RAG plan on any error.
    """
    # Check if this is a follow-up question
    is_followup = is_explicit_followup(question) or is_contextual_reply(question)
    
    # Extract SQL context for follow-ups (which column was used)
    followup_sql_context = None
    if is_followup and memory_context:
        followup_sql_context = _extract_sql_context(memory_context)
        print(f"DEBUG [multi_step_planner]: extracted SQL context: {followup_sql_context}")
    
    metadata_summary = _summarise_metadata(metadata_list)
    user_msg = _build_prompt(question, memory_context, metadata_summary, is_followup)

    full_prompt = f"{_SYSTEM}\n\n{user_msg}"
    
    if is_followup and memory_context:
        print(f"DEBUG [multi_step_planner]: detected follow-up, injecting context")

    try:
        llm = get_llm(provider=llm_provider, temperature=0.0, max_tokens=8000)
        raw = llm.generate(full_prompt)
        print(f"DEBUG [multi_step_planner]: raw=\n{raw[:800]}")

        plan = _parse_plan(raw)
        if plan and plan.get("steps"):
            # Attach follow-up SQL context to each step for the router
            if followup_sql_context:
                for step in plan["steps"]:
                    step["followup_sql_context"] = followup_sql_context
            return plan

        print("WARNING [multi_step_planner]: parse failed, using fallback")
    except Exception as e:
        print(f"WARNING [multi_step_planner]: LLM error — {e}")

    # Fallback: single RAG step
    return {
        "is_multi_step": False,
        "steps": [{
            "step_id": 1, "intent": "rag",
            "query": question, "depends_on": [],
            "cache_key": "result", "uses_cache_key": None,
            "followup_sql_context": followup_sql_context
        }]
    }


# ---------------------------------------------------------------------------
# DAG BATCH BUILDER
# ---------------------------------------------------------------------------

def build_execution_batches(steps: List[Dict]) -> List[List[Dict]]:
    """
    Topological sort steps into parallel execution batches.

    Batch 1: steps with depends_on = []
    Batch 2: steps whose every dependency is in the completed set
    ...

    Returns list of batches (each batch = list of step dicts).
    """
    remaining = {s["step_id"]: s for s in steps}
    completed  = set()
    batches    = []

    while remaining:
        # Find steps whose dependencies are all done
        ready = [
            s for sid, s in remaining.items()
            if all(dep in completed for dep in s["depends_on"])
        ]
        if not ready:
            # Circular dependency or bad plan — flush remaining
            ready = list(remaining.values())

        batches.append(ready)
        for s in ready:
            completed.add(s["step_id"])
            del remaining[s["step_id"]]

    return batches
