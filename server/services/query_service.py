"""
services/query_service.py — Optimized v5

LLM Call Budget per query type:
  Simple structured query:  2 LLM calls (unified_route + answer)   ~3-5s
  RAG query:                2 LLM calls (unified_route + answer)   ~3-5s
  Email draft:              3 LLM calls (unified_route + subject + body)  ~5-7s
  Multi-step query:         2+ LLM calls (unified_route + answer per step)

Was: 5+ LLM calls → 30-40s
Now: 2 LLM calls  → 3-6s

Key changes from v4:
  1. unified_router replaces fast_router + multi_step_planner + planner (3→1 call)
  2. Mem0 removed from hot path → called async AFTER response is returned
  3. conversation_window (pure Redis) replaces Mem0 retrieval in prompt context
  4. Cache check uses column-level diff, not string matching
  5. SQL is pre-generated inside unified_route — no separate SQL LLM call
"""

import json
import re
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from router.unified_router import unified_route, UnifiedDecision
from tools.semantic_search import semantic_search, format_search_results, is_retrieval_meaningful
from tools.sql_executor import execute_sql, SQLResult
from tools.email_tool import send_email
from tools.refusal_handler import handle_greeting
from llm.factory import get_llm
from services.conversation_window import (
    build_history_for_prompt, save_turn, check_window_for_answer,
)
from services.memory_service import (
    get_workflow, save_workflow, complete_workflow, cancel_workflow,
    store_cache_in_workflow, get_cached_result, set_email_pending,
    increment_turn, save_last_turn, store_turn_facts,
)
from services.metadata_answer import try_metadata_answer
from mongo_utils import get_workspace_metadata_for_query
from planner.multi_step_planner import build_execution_batches


# ---------------------------------------------------------------------------
# GREETING FAST-PATH
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(hi+|hey+|hello+|howdy|sup|yo|good\s*(morning|afternoon|evening|night)|"
    r"what\'?s\s*up|how\s*are\s*you|greetings|namaste|hola|hii+|heyy+)[\s!?.]*$",
    re.IGNORECASE,
)

# Short social/courtesy phrases that need no data lookup
# Core small talk phrases that anchor the match
_SMALL_TALK_CORES = [
    "thank you so much", "thank you very much", "thank you",
    "thanks a lot", "thanks so much", "thanks man", "thanks bro",
    "thanks dude", "thanks buddy", "thanks mate", "thanks",
    "thank u", "thx", "ty",
    "sounds good", "makes sense", "got it", "see you",
    "ok cool", "okay cool", "alright",
    "okay", "ok",
    "perfect", "awesome", "wonderful", "excellent",
    "great job", "nice one", "well done", "good job",
    "cool", "great", "nice", "good",
    "bye bye", "goodbye", "bye",
    "see ya", "cya",
    "lol", "haha", "wow",
]

# Filler words allowed AFTER the core (e.g. "thanks man", "ok bro")
_FILLER_SUFFIX_RE = re.compile(
    r"^(man|bro|dude|buddy|mate|sir|boss|chief|friend|"
    r"so much|a lot|very much|for that|for this|for everything|"
    r"[!?.,]{0,3})$",
    re.IGNORECASE,
)


def _is_small_talk(text: str) -> bool:
    """
    Returns True if text is small talk / courtesy phrase.
    Handles filler words: "thanks man", "thank you bro", "great job!"
    Hard cap at 5 words to avoid catching real queries.
    """
    stripped = text.strip().rstrip("!?.")
    words = stripped.split()

    if len(words) > 5:
        return False

    lower = stripped.lower()

    # Check each core phrase (longest first for greediness)
    for core in _SMALL_TALK_CORES:
        if lower == core:
            return True
        if lower.startswith(core + " "):
            remainder = lower[len(core):].strip()
            if _FILLER_SUFFIX_RE.match(remainder):
                return True

    return False


_SMALL_TALK_RESPONSES = {
    "thanks": "You're welcome! Feel free to ask anything about your data.",
    "thank you": "You're welcome! Let me know if you have more questions.",
    "ok": "Got it! Ask me anything else.",
    "okay": "Got it! Ask me anything else.",
    "cool": "😊 Ask away whenever you're ready!",
    "great": "😊 Let me know what else I can help with!",
    "perfect": "😊 Let me know what else you'd like to know!",
    "awesome": "😊 Happy to help! Ask me anything about your data.",
    "bye": "Goodbye! Come back anytime you need data insights.",
    "goodbye": "Goodbye! Come back anytime.",
    "sounds good": "Great! Let me know what you need next.",
    "got it": "Great! What else would you like to know?",
}

_GREETINGS = [
    "Hey! I\'m your workspace assistant. Ask me anything about your data!",
    "Hello! Ready to help you explore your data. What would you like to know?",
    "Hi there! What questions do you have about your workspace?",
    "Hey! What can I help you find today?",
]
_gc = 0


def _instant_greeting(q: str) -> Optional[str]:
    global _gc
    stripped = q.strip()

    # Greeting check
    if _GREETING_RE.match(stripped):
        r = _GREETINGS[_gc % len(_GREETINGS)]
        _gc += 1
        return r

    # Small talk / courtesy check — handles filler words like "thanks man", "thank you bro"
    if _is_small_talk(stripped):
        lower = stripped.lower().rstrip("!?.")
        for key, response in _SMALL_TALK_RESPONSES.items():
            if lower.startswith(key):
                return response
        return "You're welcome! Let me know if you have more questions." 

    return None


# ---------------------------------------------------------------------------
# SSE EMITTER
# ---------------------------------------------------------------------------

_EMOJIS = {
    "memory": "🧠", "router": "🔀", "workflow": "🗂️",
    "metadata": "📋", "sql_gen": "⚙️", "sql_exec": "⚡",
    "search": "🔍", "rerank": "📊", "llm": "✍️",
    "email": "📧", "cache": "⚡", "action": "✅",
    "done": "✅", "error": "❌", "system": "💬",
}


class Emitter:
    def __init__(self, session_id, callback_url):
        self.session_id = session_id
        self.callback_url = callback_url
        self.on = bool(session_id and callback_url)

    def emit(self, msg: str, tool: str = "system", data: Optional[Dict] = None):
        if not self.on:
            return
        emoji = _EMOJIS.get(tool, "💬")
        try:
            requests.post(self.callback_url,
                          json={"type": "step", "message": f"{emoji} {msg}",
                                "tool": tool, "data": data or {}}, timeout=2)
        except Exception:
            pass

    def done(self, final_answer: str, intent: str):
        if not self.on:
            return
        try:
            requests.post(self.callback_url,
                          json={"type": "done", "message": "✅ Answer ready",
                                "tool": "done", "status": "completed",
                                "finalAnswer": final_answer, "intent": intent}, timeout=2)
        except Exception:
            pass

    def error(self, msg: str):
        if not self.on:
            return
        try:
            requests.post(self.callback_url,
                          json={"type": "error", "message": f"❌ {msg}",
                                "tool": "error", "status": "error"}, timeout=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# RESPONSE BUILDERS
# ---------------------------------------------------------------------------

def _single(intent, message, turn_id=0, session_id=None, data=None,
            email_draft=None, clarification_question=None, sql_used=None,
            sources=None, cache_used=False, latency_ms=0, metadata_answered=False):
    return {
        "success": True, "intent": intent, "turn_id": turn_id,
        "session_id": session_id, "is_multi_step": False,
        "response": {"message": message, "data": data, "email_draft": email_draft,
                     "clarification_question": clarification_question},
        "meta": {"source_files": [], "cache_used": cache_used, "sql_used": sql_used,
                 "chunks_used": sources or [], "metadata_answered": metadata_answered,
                 "latency_ms": latency_ms},
    }


def _multi(steps, turn_id, session_id, n_batches, latency_ms):
    n_q = sum(1 for s in steps if s.get("intent") in ("structured", "hybrid", "rag", "metadata"))
    n_e = sum(1 for s in steps if s.get("intent") == "email")
    parts = []
    if n_q: parts.append(f"{n_q} quer{'y' if n_q == 1 else 'ies'} run")
    if n_e: parts.append(f"{n_e} email draft{'s' if n_e > 1 else ''} ready for review")
    summary = "Done — " + ", ".join(parts) + "." if parts else "Done."
    return {
        "success": True, "intent": "multi_step", "turn_id": turn_id,
        "session_id": session_id, "is_multi_step": True, "steps": steps,
        "response": {"message": summary, "data": None, "email_draft": None},
        "meta": {"steps_executed": len(steps), "parallel_batches": n_batches,
                 "latency_ms": latency_ms},
    }


def _step_result(step_id, intent, message, data=None, email_draft=None,
                 sql_used=None, sources=None, cache_used=False):
    r = {"step_id": step_id, "intent": intent, "response": {"message": message}}
    if data is not None:        r["response"]["data"] = data
    if email_draft is not None: r["response"]["email_draft"] = email_draft
    if sql_used:   r["sql_used"] = sql_used
    if sources:    r["sources"] = sources
    if cache_used: r["cache_used"] = True
    return r


# ---------------------------------------------------------------------------
# QUERY SERVICE
# ---------------------------------------------------------------------------

class QueryService:

    def __init__(self, llm_provider: Optional[str] = None):
        self.llm_provider = llm_provider

    def handle_query(self, workspace_id, question, document_id=None,
                     stream=False, session_id=None, event_callback_url=None):
        start = time.time()
        emitter = Emitter(session_id, event_callback_url)
        lms = lambda: int((time.time() - start) * 1000)
        effective_session_id = session_id or workspace_id

        print(f"\n{'='*60}")
        print(f"[QueryService] workspace={workspace_id}  q='{question}'")

        # =================================================================
        # STEP 0: GREETING FAST-PATH (regex, 0ms, no LLM)
        # =================================================================
        instant = _instant_greeting(question)
        if instant:
            emitter.done(instant, "greeting")
            return _single("greeting", instant, latency_ms=lms())

        # =================================================================
        # STEP 1: WORKFLOW STATE (Redis, ~5ms)
        # =================================================================
        wf = get_workflow(workspace_id)
        active_wf_type = wf["type"] if wf["active"] else None

        # Pending email action (no LLM needed for confirm/cancel)
        if wf.get("pending_action") == "email_approval":
            return self._handle_email_action(
                workspace_id, question, wf, emitter,
                session_id, effective_session_id, start
            )

        # =================================================================
        # STEP 2: METADATA FAST-PATH (no LLM)
        # Skip for follow-up/elaboration queries — they need RAG with context
        # =================================================================
        metadata_list = get_workspace_metadata_for_query(workspace_id)
        if document_id:
            metadata_list = [m for m in metadata_list if m.get("document_id") == document_id]

        # Detect follow-up elaboration queries that should go to RAG, not metadata
        _is_followup_elaboration = bool(re.search(
            r"^(in detail|more detail|tell me more|elaborate|explain more|"
            r"go deeper|expand on|more about|more info|can you tell me more|"
            r"and\?|details\?|detail\?|more\?|elaborate\?)",
            question.strip(), re.IGNORECASE
        )) or (len(question.split()) <= 4 and re.search(
            r"\b(more|detail|deeper|elaborate|expand|further)\b",
            question, re.IGNORECASE
        ))

        if not _is_followup_elaboration:
            meta_answer = try_metadata_answer(question, metadata_list)
            if meta_answer:
                emitter.emit("Answered from metadata stats!", tool="cache")
                turn_id = increment_turn(workspace_id)
                emitter.done(meta_answer, "metadata")
                self._async_save_turn(effective_session_id, question, question, "metadata")
                return _single("structured", meta_answer, turn_id=turn_id,
                               session_id=session_id, cache_used=True,
                               metadata_answered=True, latency_ms=lms())

        # =================================================================
        # STEP 3: WINDOW CACHE CHECK (pure Redis, ~5ms, no LLM)
        # Only for structured column-level follow-ups, skip for elaboration
        # =================================================================
        if not _is_followup_elaboration:
            window_answer = check_window_for_answer(question, effective_session_id)
            if window_answer:
                emitter.emit("Answered from conversation cache!", tool="cache")
                turn_id = increment_turn(workspace_id)
                emitter.done(window_answer, "structured")
                return _single("structured", window_answer, turn_id=turn_id,
                               session_id=session_id, cache_used=True,
                               metadata_answered=True, latency_ms=lms())

        # =================================================================
        # STEP 4: UNIFIED ROUTE — ONE LLM CALL
        #   Replaces: fast_router + multi_step_planner + planner + sql_gen
        #   Returns:  intent + SQL + execution plan
        # =================================================================
        emitter.emit("Analyzing query...", tool="router")
        conversation_history = build_history_for_prompt(effective_session_id)

        decision = unified_route(
            question=question,
            metadata_list=metadata_list,
            workspace_id=workspace_id,
            conversation_history=conversation_history,
            active_workflow=active_wf_type,
            llm_provider=self.llm_provider,
        )

        print(f"DEBUG [qs]: intent={decision.intent} multi={decision.is_multi_step} "
              f"cont={decision.is_continuation} sql={'yes' if decision.sql else 'no'} "
              f"conf={decision.confidence:.2f}")

        # Workflow collision guard
        if decision.new_workflow_needed and wf["active"]:
            emitter.emit("Previous workflow cancelled — starting fresh", tool="workflow")
            cancel_workflow(workspace_id, reason="new independent query")
            wf = get_workflow(workspace_id)

        # =================================================================
        # STEP 5: SPECIAL INTENTS (no further LLM for these)
        # =================================================================
        if decision.intent == "greeting":
            resp = handle_greeting(question, self.llm_provider)
            emitter.done(resp, "greeting")
            return _single("greeting", resp, session_id=session_id, latency_ms=lms())

        if decision.intent == "out_of_scope":
            resp = "I don't have relevant data for that. Please upload documents to your workspace first."
            emitter.done(resp, "out_of_scope")
            return _single("out_of_scope", resp, session_id=session_id, latency_ms=lms())

        if decision.intent == "clarification":
            resp = self._gen_clarification(question, metadata_list, conversation_history)
            emitter.done(resp, "clarification")
            return _single("clarification", resp, clarification_question=resp,
                           session_id=session_id, latency_ms=lms())

        # =================================================================
        # STEP 6: BUILD STEPS FROM DECISION
        # =================================================================
        if decision.is_multi_step and decision.steps:
            steps = decision.steps
        else:
            steps = [{
                "step_id": 1,
                "intent": decision.intent,
                "query": decision.rewritten_query,
                "sql": decision.sql,
                "rag_query": decision.rag_query or decision.rewritten_query,
                "depends_on": [],
                "cache_key": "result",
                "uses_cache_key": None,
                "followup_sql_context": None,
            }]

        # =================================================================
        # STEP 7: EXECUTE STEPS (DAG-based, parallel where possible)
        # =================================================================
        batches = build_execution_batches(steps)
        step_results = {}
        n_batches = len(batches)

        for b_no, batch in enumerate(batches, 1):
            emitter.emit(
                f"Running batch {b_no}/{n_batches} "
                f"({len(batch)} step{'s' if len(batch) > 1 else ''})...",
                tool="workflow"
            )
            if len(batch) == 1:
                s = batch[0]
                res = self._execute_step(s, workspace_id, document_id,
                                         metadata_list, emitter, step_results)
                step_results[s["step_id"]] = res
            else:
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futs = {
                        pool.submit(self._execute_step, s, workspace_id, document_id,
                                    metadata_list, emitter, step_results): s
                        for s in batch
                    }
                    for fut in as_completed(futs):
                        s = futs[fut]
                        try:
                            res = fut.result()
                        except Exception as e:
                            res = _step_result(s["step_id"], s["intent"], f"Step failed: {e}")
                        step_results[s["step_id"]] = res

        # =================================================================
        # STEP 8: BUILD & RETURN RESPONSE
        # =================================================================
        ordered = [step_results[s["step_id"]] for s in steps if s["step_id"] in step_results]
        is_multi = decision.is_multi_step and len(steps) > 1
        turn_id = increment_turn(workspace_id)

        if not is_multi:
            single = ordered[0] if ordered else {}
            rb = single.get("response", {})
            result = _single(
                intent=single.get("intent", decision.intent),
                message=rb.get("message", ""),
                turn_id=turn_id, session_id=session_id,
                data=rb.get("data"), email_draft=rb.get("email_draft"),
                sql_used=single.get("sql_used"), sources=single.get("sources"),
                cache_used=single.get("cache_used", False), latency_ms=lms(),
            )
        else:
            result = _multi(ordered, turn_id, session_id, n_batches, lms())

        emitter.done(result["response"]["message"], result["intent"])

        # =================================================================
        # STEP 9: ASYNC PERSIST (never blocks response)
        # =================================================================
        self._async_persist(
            workspace_id=workspace_id,
            session_id=effective_session_id,
            question=question,
            decision=decision,
            ordered=ordered,
        )

        return result

    # -----------------------------------------------------------------------
    # STEP EXECUTOR
    # -----------------------------------------------------------------------

    def _execute_step(self, step, workspace_id, document_id,
                      metadata_list, emitter, completed):
        sid = step["step_id"]
        intent = step["intent"]
        query = step.get("query", "")
        ck = step.get("cache_key")
        uck = step.get("uses_cache_key")

        print(f"DEBUG [step {sid}]: {intent} | '{query[:70]}'")

        if intent == "metadata":
            ans = try_metadata_answer(query, metadata_list)
            if ans:
                return _step_result(sid, "structured", ans, cache_used=True)

        if intent in ("structured", "hybrid"):
            if uck:
                cached = get_cached_result(workspace_id, uck)
                if cached and cached.get("rows"):
                    emitter.emit(f"[step {sid}] Using cached result...", tool="cache")
                    rows = cached["rows"]
                    ans = self._structured_answer(query, cached.get("sql"), rows)
                    return _step_result(sid, "structured", ans, data=rows[:100],
                                        sql_used=cached.get("sql"), cache_used=True)
            return self._run_structured(sid, query, workspace_id, metadata_list, ck, emitter)

        if intent == "rag":
            rag_query = step.get("rag_query") or query
            return self._run_rag(sid, rag_query, workspace_id, document_id, emitter)

        if intent == "email":
            return self._run_email(sid, query, workspace_id, uck, completed, emitter)

        return self._run_rag(sid, query, workspace_id, document_id, emitter)

    # -----------------------------------------------------------------------
    # STRUCTURED — uses pre-generated SQL, only falls back to LLM if SQL fails
    # -----------------------------------------------------------------------

    def _run_structured(self, step_id, query, workspace_id, metadata_list,
                         cache_key, emitter):
        """
        Structured query execution.
        SQL is always generated by planner.planner (dedicated, reliable).
        The unified_router only classifies intent — never generates SQL.
        """
        from planner.planner import planner_decision
        from services.conversation_window import build_history_for_prompt

        emitter.emit(f"[step {step_id}] Generating SQL...", tool="sql_gen")

        # Pass conversation history so planner can resolve pronouns in SQL
        conv_context = build_history_for_prompt(workspace_id)

        pr = planner_decision(
            question=query,
            memory_context=conv_context,
            metadata_list=metadata_list,
            llm_provider=self.llm_provider,
            workspace_id=workspace_id,
        )
        sql = pr.sql_query

        if not sql:
            return _step_result(step_id, "structured",
                                "Couldn't generate SQL for that query. Try rephrasing, e.g. 'show me employees with rating above 4'.")

        if isinstance(sql, dict) and sql.get("ambiguous"):
            opts = sql.get("options", [])
            return _step_result(step_id, "structured",
                                f"Multiple tables could match — which did you mean? {', '.join(opts)}")

        emitter.emit(f"[step {step_id}] Executing SQL...", tool="sql_exec", data={"sql": sql})
        result = execute_sql(workspace_id, sql)

        if not result.success:
            return _step_result(step_id, "structured",
                                f"SQL error: {result.error}\n\nSQL attempted: `{sql}`", sql_used=sql)

        emitter.emit(f"[step {step_id}] Generating answer ({result.row_count} rows)...", tool="llm")
        rows = result.rows
        ans = self._structured_answer(query, sql, rows)

        if cache_key:
            store_cache_in_workflow(workspace_id, cache_key, rows, result.columns, sql)

        return _step_result(step_id, "structured", ans, data=rows[:100], sql_used=sql)

    # -----------------------------------------------------------------------
    # RAG
    # -----------------------------------------------------------------------

    def _run_rag(self, step_id, query, workspace_id, document_id, emitter):
        emitter.emit(f"[step {step_id}] Searching documents...", tool="search")
        # search_vectors already ran: dense+BM25 -> top20 -> rerank -> top5 -> parent expand
        results = semantic_search(workspace_id=workspace_id, query=query,
                                  top_k=5, document_id=document_id, use_reranker=True)

        if not results:
            return _step_result(step_id, "rag",
                                "I couldn't find relevant information about this in the "
                                "uploaded workspace documents. Please ask questions related "
                                "to the provided documents.")

        # Score gate — if best chunk score is too low, query is out-of-scope
        if not is_retrieval_meaningful(results):
            return _step_result(step_id, "rag",
                                "I couldn't find relevant information about this in the "
                                "uploaded workspace documents. Please ask questions related "
                                "to the provided documents.")

        # results[*]["text"] is already the parent chunk content
        context = format_search_results(results, max_chars=2000)

        emitter.emit(f"[step {step_id}] Generating answer...", tool="llm")
        prompt = (
            f"You are a helpful assistant. The user asked: \"{query}\"\n\n"
            f"Use ONLY the context below to answer. Do not hallucinate or add outside knowledge.\n\n"
            f"Context:\n{context}\n\n"
            f"Instructions:\n"
            f"1. Answer the question directly and naturally.\n"
            f"2. Use **bold** for key terms, names, or important values.\n"
            f"3. Use bullet points if listing multiple items.\n"
            f"4. If context doesn\'t contain the answer, say so clearly.\n"
            f"5. End with ONE short relevant follow-up question the user might naturally want to ask.\n"
            f"   Format: \'Would you like to [specific next step]?\'\n"
            f"   Keep it specific to what was just answered — never generic.\n\n"
            f"Answer:"
        )
        llm = get_llm(provider=self.llm_provider, temperature=0.3, max_tokens=2000)
        answer = llm.generate(prompt)
        sources = [{"text": r.get("matched_chunk", r["text"])[:200],
                    "score": float(r.get("rerank_score", r.get("score", 0)))}
                   for r in results[:3]]
        return _step_result(step_id, "rag", answer, sources=sources)

    # -----------------------------------------------------------------------
    # EMAIL DRAFT
    # -----------------------------------------------------------------------

    def _run_email(self, step_id, query, workspace_id, uses_cache_key, completed, emitter):
        emitter.emit(f"[step {step_id}] Finding recipients...", tool="email")
        recipients = []

        if uses_cache_key:
            cached = get_cached_result(workspace_id, uses_cache_key)
            if cached and cached.get("rows"):
                recipients = self._extract_recipients(cached["rows"])

        if not recipients:
            for res in completed.values():
                rows = res.get("response", {}).get("data") or []
                if rows:
                    r = self._extract_recipients(rows)
                    if r:
                        recipients = r
                        break

        if not recipients:
            wf = get_workflow(workspace_id)
            sql_res = wf["outputs"].get("sql_result")
            if sql_res and sql_res.get("rows"):
                recipients = self._extract_recipients(sql_res["rows"])

        if not recipients:
            return _step_result(step_id, "email",
                                "No recipient emails found. Run a data query first "
                                "(e.g. 'show employees with highest rating'), then ask me to email them.")

        emitter.emit(f"[step {step_id}] Drafting email for {len(recipients)} recipient(s)...", tool="llm")
        draft = self._gen_email_draft(query, recipients)
        set_email_pending(workspace_id, recipients, draft)

        email_draft = {"subject": draft["subject"], "body": draft["body"],
                       "recipients": recipients, "editable": True}
        msg = (
            f"📧 Email draft ready for **{len(recipients)} recipient(s)**:\n\n"
            f"**Subject:** {draft['subject']}\n\n"
            f"---\n{draft['body']}\n---\n\n"
            f"Reply **'send'** to send, or tell me changes you'd like."
        )
        return _step_result(step_id, "email", msg, email_draft=email_draft)

    # -----------------------------------------------------------------------
    # EMAIL ACTION (confirm / edit / cancel)
    # -----------------------------------------------------------------------

    def _handle_email_action(self, workspace_id, question, wf, emitter,
                              session_id, effective_session_id, start):
        lms = lambda: int((time.time() - start) * 1000)
        turn_id = increment_turn(workspace_id)

        q = question.lower().strip().rstrip("!.,?")
        confirm_re = re.compile(
            r"\b(yes|send|confirm|proceed|ok|okay|sure|yep|yeah|looks good|go ahead|do it|send it)\b",
            re.IGNORECASE)
        cancel_re = re.compile(
            r"\b(no|cancel|stop|abort|nevermind|never mind|don'?t send)\b",
            re.IGNORECASE)

        recipients = wf["outputs"].get("recipients", [])
        draft = wf["outputs"].get("email_draft")

        # Cancel
        if cancel_re.search(q):
            cancel_workflow(workspace_id, reason="user cancelled")
            msg = "Email cancelled. Let me know if you'd like to do something else."
            emitter.done(msg, "email")
            self._async_save_turn(effective_session_id, question, question, "email")
            return _single("email", msg, turn_id=turn_id, session_id=session_id, latency_ms=lms())

        # Confirm → send
        if confirm_re.search(q):
            if not recipients or not draft:
                cancel_workflow(workspace_id, reason="lost email state")
                msg = "I lost the email data. Please draft again."
                emitter.done(msg, "email")
                return _single("email", msg, turn_id=turn_id, session_id=session_id, latency_ms=lms())

            emitter.emit(f"Sending to {len(recipients)} recipient(s)...", tool="email")
            result = send_email(
                workspace_id=workspace_id, recipients=recipients,
                subject=draft["subject"], body=draft["body"],
                emit_callback=lambda _, m: emitter.emit(m, tool="email"),
            )
            complete_workflow(workspace_id)
            msg = (
                f"✅ Email sent to **{result['sent']}** recipient(s)!"
                if result.get("success") and result.get("failed", 0) == 0
                else f"Sent **{result.get('sent', 0)}**, {result.get('failed', 0)} failed."
            )
            emitter.done(msg, "email")
            self._async_save_turn(effective_session_id, question, question, "email")
            return _single("email", msg, turn_id=turn_id, session_id=session_id, latency_ms=lms())

        # Edit request → regenerate draft
        if not recipients:
            cancel_workflow(workspace_id, reason="lost recipients")
            msg = "I lost the recipient list. Please run your data query again."
            emitter.done(msg, "email")
            return _single("email", msg, turn_id=turn_id, session_id=session_id, latency_ms=lms())

        emitter.emit("Updating email draft...", tool="llm")
        edit_ctx = question
        if draft:
            edit_ctx = (f"Current email subject: {draft['subject']}. "
                        f"User change request: {question}")
        new_draft = self._gen_email_draft(edit_ctx, recipients)

        wf_fresh = get_workflow(workspace_id)
        wf_fresh["outputs"]["email_draft"] = new_draft
        save_workflow(workspace_id, wf_fresh)

        preview = {"subject": new_draft["subject"], "body": new_draft["body"],
                   "recipients": recipients, "editable": True}
        msg = (
            f"Updated draft:\n\n"
            f"**Subject:** {new_draft['subject']}\n\n"
            f"---\n{new_draft['body']}\n---\n\n"
            f"Reply **'send'** to send, or tell me more changes."
        )
        emitter.done(msg, "email")
        self._async_save_turn(effective_session_id, question, question, "email")
        return _single("email", msg, email_draft=preview,
                       turn_id=turn_id, session_id=session_id, latency_ms=lms())

    # -----------------------------------------------------------------------
    # ANSWER GENERATORS
    # -----------------------------------------------------------------------

    def _structured_answer(self, question, sql, rows) -> str:
        formatted = self._fmt_rows(rows)
        row_count = len(rows)
        prompt = (
            f"You are a helpful data assistant. The user asked: \"{question}\"\n\n"
            f"SQL executed: {sql or 'N/A'}\n"
            f"Results ({row_count} record(s)):\n{formatted}\n\n"
            f"Instructions:\n"
            f"1. Answer the question directly and naturally in 1-2 sentences.\n"
            f"2. Present the data clearly — use a numbered list or table if multiple records.\n"
            f"3. Highlight key values (names, numbers, rankings) in **bold**.\n"
            f"4. If no rows found, say so clearly.\n"
            f"5. End with ONE short relevant follow-up question the user might want to ask next.\n"
            f"   Format: \'Would you like to [specific action related to this data]?\'\n"
            f"   Example: \'Would you like me to draft an email to these employees?\'\n"
            f"   Example: \'Would you like to see more details about any of them?\'\n"
            f"   Keep it specific to what was just returned — never generic.\n\n"
            f"Answer:"
        )
        llm = get_llm(provider=self.llm_provider, temperature=0.3, max_tokens=2000)
        return llm.generate(prompt)

    def _gen_email_draft(self, request, recipients) -> Dict:
        r0 = recipients[0] if recipients else {}
        name = r0.get("name", "the recipient")
        llm = get_llm(provider=self.llm_provider, temperature=0.3, max_tokens=150)
        try:
            subject = llm.generate(
                f"Write a professional email subject line for: {request}\n"
                f"Output ONLY the subject. No quotes. No trailing punctuation."
            ).strip().strip('"\'')
        except Exception:
            subject = "Important Update"
        try:
            body_llm = get_llm(provider=self.llm_provider, temperature=0.3, max_tokens=1000)
            body = body_llm.generate(
                f"Write a professional email body for: {request}\n"
                f"Recipient: {name}\n"
                f"Use {{{{name}}}} as salutation placeholder.\n"
                f"Be concise. End with: Best regards,\nManagement\n"
                f"Output ONLY the body text. No subject. No JSON. No markdown."
            ).strip()
        except Exception:
            body = f"Dear {{{{name}}}},\n\nThis is an important update.\n\nBest regards,\nManagement"
        if "{{name}}" not in body and "{name}" not in body:
            body = "Dear {{name}},\n\n" + body
        return {"subject": subject, "body": body}

    def _gen_clarification(self, question, metadata_list, conversation_history) -> str:
        summaries = [m.get("summary", "")[:80] for m in metadata_list[:3] if m.get("summary")]
        data_desc = "; ".join(summaries) or "No data loaded"
        ctx = f"\nContext:\n{conversation_history}" if conversation_history else ""
        prompt = (
            f"Data: {data_desc}{ctx}\n\n"
            f"User asked: {question}\n\n"
            f"Ask ONE short clarifying question. Be brief."
        )
        try:
            llm = get_llm(provider=self.llm_provider, temperature=0.1, max_tokens=100)
            return llm.generate(prompt).strip()
        except Exception:
            return "Could you rephrase what you're looking for?"

    # -----------------------------------------------------------------------
    # ASYNC PERSIST (runs in background, never blocks response)
    # -----------------------------------------------------------------------

    def _async_persist(self, workspace_id, session_id, question, decision, ordered):
        def _do():
            try:
                sql_used = ""
                result_rows = []
                result_columns = []
                for r in ordered:
                    if r.get("sql_used") and not sql_used:
                        sql_used = r["sql_used"]
                    data = r.get("response", {}).get("data")
                    if data and isinstance(data, list) and not result_rows:
                        result_rows = data
                        if data and isinstance(data[0], dict):
                            result_columns = list(data[0].keys())

                entities = self._extract_entities(question)
                combined = " | ".join(
                    r.get("response", {}).get("message", "")[:200] for r in ordered
                )

                # 1. Redis conversation window (~5ms)
                save_turn(
                    session_id=session_id,
                    query=question,
                    rewritten_query=decision.rewritten_query,
                    intent=decision.intent,
                    sql=sql_used,
                    columns=result_columns,
                    result_preview=result_rows[:3],
                    entities=entities,
                )

                # 2. Legacy last_turn (for email workflow)
                result_preview_str = ""
                if result_rows and isinstance(result_rows[0], dict):
                    result_preview_str = ", ".join(
                        f"{k}: {v}" for k, v in list(result_rows[0].items())[:5]
                    )
                save_last_turn(
                    workspace_id, decision.intent, combined[:200],
                    query=question, sql=sql_used, entities=entities,
                    result_preview=result_preview_str
                )

                # 3. Mem0 long-term (slow — background is fine)
                store_turn_facts(
                    workspace_id=workspace_id,
                    question=question,
                    answer=combined,
                    intent=decision.intent,
                    dataset="",
                    columns=result_columns,
                    sql_query=sql_used,
                )
            except Exception as e:
                print(f"WARNING [persist]: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _async_save_turn(self, session_id, query, rewritten, intent):
        def _do():
            try:
                save_turn(session_id=session_id, query=query,
                          rewritten_query=rewritten, intent=intent)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    # -----------------------------------------------------------------------
    # HELPERS
    # -----------------------------------------------------------------------

    def _extract_entities(self, question: str) -> List[str]:
        entities = []
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", question)
        entities.extend(quoted)
        prep_pattern = (r"(?:of|for|about|named|called)\s+([A-Za-z][a-zA-Z\s/]+?)"
                        r"(?:\s+(?:in|from|column|table|dataset|where|and|or)|$)")
        for m in re.findall(prep_pattern, question, re.IGNORECASE):
            cleaned = m.strip()
            if len(cleaned) > 2 and cleaned.lower() not in ("the", "this", "that", "his", "her", "their"):
                entities.append(cleaned)
        seen, unique = set(), []
        for e in entities:
            e_lower = e.lower().strip()
            if e_lower not in seen and len(e_lower) > 1:
                seen.add(e_lower)
                unique.append(e.strip())
        return unique[:10]

    def _extract_recipients(self, rows) -> List[Dict]:
        if not rows:
            return []
        keys = list(rows[0].keys())
        email_col = next((c for c in keys if "email" in c.lower()), None)
        name_col = next((c for c in keys
                         if "name" in c.lower() and "email" not in c.lower()), None)
        if not email_col:
            return []
        out = []
        for row in rows:
            email = row.get(email_col)
            if email and "@" in str(email):
                r = {"email": str(email).strip()}
                if name_col and row.get(name_col):
                    r["name"] = str(row[name_col]).strip()
                out.append(r)
        return out

    def _fmt_rows(self, rows, max_rows=50) -> str:
        if not rows:
            return "No records found."
        seen, unique = set(), []
        for row in rows:
            key = tuple(sorted((k, str(v)) for k, v in row.items()))
            if key not in seen:
                seen.add(key)
                unique.append(row)
        lines = [
            "- " + " | ".join(f"{k}: {v}" for k, v in row.items()
                               if v is not None and str(v).strip())
            for row in unique[:max_rows]
        ]
        result = "\n".join(l for l in lines if l.strip())
        if len(unique) > max_rows:
            result += f"\n... and {len(unique) - max_rows} more records"
        return result