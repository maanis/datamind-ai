"""
router/fast_router.py — v4

Single-purpose: classify intent + detect if this is a workflow continuation.
Uses a ~400 char prompt. Returns structured RouterDecision.

Key rules:
- LLM NEVER controls flow — it only returns intent + is_continuation
- new_workflow_needed=True → caller cancels active workflow, starts fresh
- is_continuation=True  → caller resumes active workflow at next_step
- Memory facts injected as 2-3 bullet points, NOT full conversation
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from llm.factory import get_llm


VALID_INTENTS = {
    "structured",   # SQL / tabular data query
    "rag",          # unstructured document search
    "hybrid",       # needs both SQL + RAG
    "email",        # draft/send email
    "metadata",     # answered from stats (row count, columns)
    "greeting",     # small talk
    "clarification", # genuinely ambiguous
}

# Words that signal the user is continuing an active workflow
_CONTINUATION_RE = re.compile(
    r"\b(yes|send|confirm|ok|okay|sure|yep|yeah|proceed|go ahead|"
    r"do it|send it|looks good|change|update|modify|remove|add|"
    r"make it|rewrite|fix|no|cancel|stop|abort)\b",
    re.IGNORECASE,
)

# Words that clearly signal a NEW independent query (override continuation)
_FRESH_QUERY_RE = re.compile(
    r"^(get|show|find|list|give|fetch|what|who|how many|count|"
    r"which|display|select|retrieve|tell me|can you|could you)\b",
    re.IGNORECASE,
)


@dataclass
class RouterDecision:
    intent: str              # one of VALID_INTENTS
    is_continuation: bool    # True = resume active workflow
    new_workflow_needed: bool # True = cancel active wf, start fresh
    rewritten_query: str     # clean standalone question
    confidence: float        # 0.0 - 1.0


# ---------------------------------------------------------------------------
# PROMPT  (kept deliberately short — ~400 chars system)
# ---------------------------------------------------------------------------

_SYSTEM = """Classify the user query. Output ONLY JSON, no explanation.

Intents:
structured=tabular/CSV data query, rag=document search, hybrid=both,
email=draft or send email, metadata=stats only (row count/columns),
greeting=small talk, clarification=truly ambiguous

Rules:
- Any question about rows/columns/filters/counts/rankings → structured
- "send email" / "email them" / "notify" → email
- If active_workflow exists AND message looks like a reply (yes/no/change/send) → is_continuation=true
- Fresh data question while workflow active → new_workflow_needed=true, is_continuation=false
- Default uncertain → rag"""

def _build_prompt(question: str, datasets_hint: str,
                  memory_facts: str, active_workflow: Optional[str]) -> str:
    wf_line = f"\nActive workflow: {active_workflow}" if active_workflow else ""
    facts_line = f"\nRelevant context:\n{memory_facts}" if memory_facts else ""
    return (
        f"Datasets: {datasets_hint}{wf_line}{facts_line}\n\n"
        f"Query: {question}\n\n"
        f'Output JSON: {{"intent":"...","is_continuation":bool,"new_workflow_needed":bool,'
        f'"rewritten_query":"...","confidence":0.0}}'
    )


# ---------------------------------------------------------------------------
# HEURISTIC PRE-CHECKS (no LLM needed)
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|howdy|sup|yo|good\s*(morning|afternoon|evening|night)|"
    r"what'?s\s*up|how\s*are\s*you|greetings|namaste|hola|hii+|heyy+)[\s!?.]*$",
    re.IGNORECASE,
)


def _is_greeting(q: str) -> bool:
    return bool(_GREETING_RE.match(q.strip()))


def _is_workflow_reply(q: str, has_active_workflow: bool) -> bool:
    """Short messages that are clearly replies to a pending workflow step."""
    if not has_active_workflow:
        return False
    stripped = q.strip().lower().rstrip("!.,?")
    # Pure confirmation/cancellation words (≤4 words)
    if len(stripped.split()) <= 4 and bool(_CONTINUATION_RE.search(stripped)):
        # Make sure it's not a fresh data question
        if not _FRESH_QUERY_RE.match(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# PARSE
# ---------------------------------------------------------------------------

def _parse(raw: str, question: str) -> RouterDecision:
    raw = raw.strip()
    # Strip markdown fences
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # Find JSON object
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Regex fallback
        intent_m = re.search(r'"intent"\s*:\s*"(\w+)"', raw)
        conf_m   = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
        rq_m     = re.search(r'"rewritten_query"\s*:\s*"([^"]+)"', raw)
        data = {
            "intent":              intent_m.group(1) if intent_m else "rag",
            "is_continuation":     False,
            "new_workflow_needed": False,
            "rewritten_query":     rq_m.group(1) if rq_m else question,
            "confidence":          float(conf_m.group(1)) if conf_m else 0.4,
        }

    intent = data.get("intent", "rag").lower()
    if intent not in VALID_INTENTS:
        intent = "rag"

    return RouterDecision(
        intent              = intent,
        is_continuation     = bool(data.get("is_continuation", False)),
        new_workflow_needed = bool(data.get("new_workflow_needed", False)),
        rewritten_query     = str(data.get("rewritten_query") or question).strip(),
        confidence          = float(data.get("confidence", 0.5)),
    )


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def fast_route(
    question: str,
    datasets_hint: str,      # compact: "employees.csv[hybrid], orders.csv[hybrid]"
    memory_facts: str,       # from Mem0 retrieve_relevant_facts()
    active_workflow: Optional[str],  # e.g. "email_workflow" or None
    llm_provider: Optional[str] = None,
) -> RouterDecision:
    """
    Classify intent using a small LLM call.
    Heuristics run first (greetings, pure confirmations) to skip LLM entirely.
    """
    has_active_wf = active_workflow is not None

    # Heuristic: greeting
    if _is_greeting(question):
        return RouterDecision(
            intent="greeting",
            is_continuation=False,
            new_workflow_needed=False,
            rewritten_query=question,
            confidence=1.0,
        )

    # Heuristic: short workflow reply (yes/send/no/cancel/change subject)
    if _is_workflow_reply(question, has_active_wf):
        return RouterDecision(
            intent="email" if active_workflow and "email" in active_workflow else "structured",
            is_continuation=True,
            new_workflow_needed=False,
            rewritten_query=question,
            confidence=1.0,
        )

    # LLM classification
    prompt = _SYSTEM + "\n\n" + _build_prompt(
        question, datasets_hint, memory_facts, active_workflow
    )

    try:
        llm = get_llm(provider=llm_provider, temperature=0.0, max_tokens=200)
        raw = llm.generate(prompt)
        decision = _parse(raw, question)
        print(
            f"DEBUG [router]: intent={decision.intent} "
            f"cont={decision.is_continuation} "
            f"new_wf={decision.new_workflow_needed} "
            f"conf={decision.confidence:.2f}"
        )
        return decision
    except Exception as e:
        print(f"WARNING [router]: LLM failed ({e}) — defaulting to rag")
        return RouterDecision(
            intent="rag",
            is_continuation=False,
            new_workflow_needed=False,
            rewritten_query=question,
            confidence=0.3,
        )
