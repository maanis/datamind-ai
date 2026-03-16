"""
Planner for query intent classification and routing.
Split into TWO sequential LLM calls:
1. Router Call - lightweight intent classification + query rewriting
2. SQL Generator Call - SQL generation (only if intent is structured/hybrid)
"""

import json
import re
import sqlite3
import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from llm.factory import get_llm
from config import SQLITE_DIR
from mongo_utils import get_workspace_sqlite_path


# =============================================================================
# DATA CLASSES
# =============================================================================

# Valid intents that the router can return
VALID_INTENTS = ["structured", "rag", "hybrid", "greeting", "clarification", "out_of_scope", "email"]

# Confirmation patterns for follow-up detection
CONFIRMATION_PATTERNS = [
    "yes", "ok", "okay", "correct", "right", "sure", "yep", "yeah", "yup",
    "go ahead", "proceed", "confirm", "confirmed", "that's right", "that is right",
    "exactly", "affirmative", "indeed", "absolutely", "definitely"
]


@dataclass
class RouterResult:
    """Result from router (intent classification) call."""
    intent: str  # structured | rag | hybrid | greeting | clarification | out_of_scope | email
    rewritten_query: Optional[str]
    is_follow_up: bool
    clarification_question: Optional[str]
    confidence: float
    raw_response: Optional[Dict[str, Any]] = None
    action_type: Optional[str] = None  # For email intents: 'send_email', etc.
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RouterResult":
        """Create RouterResult from dictionary."""
        return cls(
            intent=data.get("intent", "rag"),
            rewritten_query=data.get("rewritten_query"),
            is_follow_up=data.get("is_follow_up", False),
            clarification_question=data.get("clarification_question"),
            confidence=data.get("confidence", 0.5),
            raw_response=data,
            action_type=data.get("action_type")
        )
    
    @classmethod
    def fallback_clarification(cls) -> "RouterResult":
        """Create fallback clarification result."""
        return cls(
            intent="clarification",
            rewritten_query=None,
            is_follow_up=False,
            clarification_question="I'm not sure I understood your question. Could you please rephrase it?",
            confidence=0.0
        )
    
    @classmethod
    def greeting(cls) -> "RouterResult":
        """Create greeting result."""
        return cls(
            intent="greeting",
            rewritten_query=None,
            is_follow_up=False,
            clarification_question=None,
            confidence=1.0
        )
    
    @classmethod
    def out_of_scope(cls, question: str) -> "RouterResult":
        """Create out of scope result."""
        return cls(
            intent="out_of_scope",
            rewritten_query=question,
            is_follow_up=False,
            clarification_question="I don't have any data loaded yet. Please upload some documents first.",
            confidence=0.9
        )
    
    @classmethod
    def email_action(cls, question: str, action_type: str = "send_email") -> "RouterResult":
        """Create email action result."""
        return cls(
            intent="email",
            rewritten_query=question,
            is_follow_up=True,  # Email actions typically reference previous results
            clarification_question=None,
            confidence=0.9,
            action_type=action_type
        )
    
    @classmethod
    def follow_up_confirmation(cls, previous_intent: str, previous_rewritten_query: str) -> "RouterResult":
        """Create follow-up confirmation result that reuses previous intent."""
        return cls(
            intent=previous_intent,
            rewritten_query=previous_rewritten_query,
            is_follow_up=True,
            clarification_question=None,
            confidence=1.0
        )


@dataclass
class PlannerResult:
    intent: str
    is_follow_up: bool
    is_complete: bool
    rewritten_query: Optional[str]
    sql_query: Optional[str]
    clarification_question: Optional[str]
    confidence: float
    clarification_options: Optional[List[str]] = None  # NEW
    raw_response: Optional[Dict[str, Any]] = None
    action_type: Optional[str] = None  # For email/action intents
    
    @classmethod
    def from_router_result(cls, router: RouterResult, sql_query: Optional[str] = None) -> "PlannerResult":
        """Create PlannerResult from RouterResult and optional SQL."""
        is_complete = router.intent not in ["clarification"] and router.confidence >= 0.4
        
        return cls(
            intent=router.intent,
            is_follow_up=router.is_follow_up,
            is_complete=is_complete,
            rewritten_query=router.rewritten_query,
            sql_query=sql_query,
            clarification_question=router.clarification_question,
            confidence=router.confidence,
            raw_response=router.raw_response,
            action_type=getattr(router, 'action_type', None)
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlannerResult":
        """Create PlannerResult from dictionary."""
        return cls(
            intent=data.get("intent", "rag"),
            is_follow_up=data.get("is_follow_up", False),
            is_complete=data.get("is_complete", True),
            rewritten_query=data.get("rewritten_query"),
            sql_query=data.get("sql_query"),
            clarification_question=data.get("clarification_question"),
            confidence=data.get("confidence", 0.5),
            raw_response=data,
            action_type=data.get("action_type")
        )
    
    @classmethod
    def fallback_semantic(cls, question: str) -> "PlannerResult":
        """Create fallback semantic result."""
        return cls(
            intent="rag",
            is_follow_up=False,
            is_complete=True,
            rewritten_query=question,
            sql_query=None,
            clarification_question=None,
            confidence=0.3
        )


# =============================================================================
# METADATA HELPERS
# =============================================================================

def prefilter_metadata(
    question: str,
    metadata_list: List[Dict[str, Any]],
    max_entries: int = 5
) -> List[Dict[str, Any]]:
    """
    Pre-filter metadataForQuery entries using keyword matching.
    
    Args:
        question: User question
        metadata_list: List of metadataForQuery entries
        max_entries: Maximum entries to return
        
    Returns:
        Filtered and scored metadata entries
    """
    if not metadata_list:
        return []
    
    question_lower = question.lower()
    question_words = set(question_lower.split())
    
    scored_entries = []
    
    for entry in metadata_list:
        score = 0
        
        # Match against keywords
        keywords = entry.get("keywords", [])
        for kw in keywords:
            if kw.lower() in question_lower:
                score += 3
            if any(kw.lower() in word for word in question_words):
                score += 1
        
        # Match against summary
        summary = entry.get("summary", "").lower()
        for word in question_words:
            if len(word) > 3 and word in summary:
                score += 2
        
        # Match against description
        description = entry.get("description", "").lower()
        for word in question_words:
            if len(word) > 3 and word in description:
                score += 1
        
        # Match against columns (for structured data)
        columns = entry.get("columns", [])
        for col in columns:
            col_lower = col.lower().replace("_", " ")
            if col_lower in question_lower:
                score += 4
            for word in question_words:
                if len(word) > 3 and word in col_lower:
                    score += 2
        
        # Match against table name
        table_name = entry.get("tableName", "")
        if table_name:
            table_lower = table_name.lower().replace("_", " ")
            for word in question_words:
                if len(word) > 3 and word in table_lower:
                    score += 2
        
        scored_entries.append((entry, score))
    
    # Sort by score descending
    scored_entries.sort(key=lambda x: x[1], reverse=True)
    
    # Take top entries
    if scored_entries[0][1] == 0:
        return [e[0] for e in scored_entries[:3]]
    
    filtered = [e[0] for e in scored_entries if e[1] > 0][:max_entries]
    
    if len(filtered) < 3:
        remaining = [e[0] for e in scored_entries if e[1] == 0]
        filtered.extend(remaining[:3 - len(filtered)])
    
    return filtered


def strip_metadata_for_router(
    metadata_list: List[Dict[str, Any]],
    max_summary_chars: int = 150
) -> List[Dict[str, Any]]:
    """
    Strip heavy fields from metadata for lightweight router call.
    
    ONLY includes: document_id, file_name, storage_mode, keywords, summary
    REMOVES: table_ddl, column_value_samples, columns (full list), schema_sample
    
    Args:
        metadata_list: List of metadataForQuery entries
        max_summary_chars: Maximum characters for summary
        
    Returns:
        Lightweight metadata entries for router
    """
    stripped = []
    
    for entry in metadata_list:
        stripped_entry = {
            "document_id": entry.get("document_id", ""),
            "file_name": entry.get("file_name", entry.get("fileName", "")),
            "storage_mode": entry.get("storageMode", entry.get("type", "rag")),
            "keywords": entry.get("keywords", [])[:8],
            "summary": entry.get("summary", "")[:max_summary_chars]
        }
        
        # Only include basic column info for routing hint (not full schema)
        if entry.get("type") == "structured" or entry.get("storageMode") in ["sqlite", "hybrid"]:
            # Just include column count and first few column names as hint
            columns = entry.get("columns", [])
            if columns:
                stripped_entry["column_count"] = len(columns)
                stripped_entry["sample_columns"] = columns[:5]  # First 5 only
        
        stripped.append(stripped_entry)
    
    return stripped


def get_rich_metadata_for_sql(
    metadata_entry: Dict[str, Any],
    workspace_id: str
) -> Dict[str, Any]:
    """
    Get rich metadata for SQL generation including live DDL.
    
    Args:
        metadata_entry: The matched metadata entry
        workspace_id: Workspace ID for database lookup
        
    Returns:
        Rich metadata with table_ddl, column_value_samples, schema_sample
    """
    table_name = metadata_entry.get("tableName")
    if not table_name:
        return metadata_entry
    
    result = dict(metadata_entry)
    
    # Get database path
    db_path = get_workspace_sqlite_path(workspace_id)
    if not db_path or not os.path.exists(db_path):
        # Try fallback path
        db_path = os.path.join(SQLITE_DIR, workspace_id, f"data_{workspace_id}.db")
    
    if not db_path or not os.path.exists(db_path):
        print(f"DEBUG [get_rich_metadata_for_sql]: Database not found for workspace {workspace_id}")
        return result
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get live DDL
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        ddl_row = cursor.fetchone()
        if ddl_row:
            result["table_ddl"] = ddl_row[0]
        
        # Get column info with types
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        result["columns_with_types"] = [
            {"name": col[1], "type": col[2]} for col in columns_info
        ]
        
        # Get sample rows (3-5 rows)
        cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 5')
        sample_rows = cursor.fetchall()
        column_names = [desc[0] for desc in cursor.description]
        result["schema_sample"] = [
            dict(zip(column_names, row)) for row in sample_rows
        ]
        
        # Get column value samples for categorical columns (text columns with limited unique values)
        column_value_samples = {}
        for col_info in columns_info:
            col_name = col_info[1]
            col_type = col_info[2].upper() if col_info[2] else ""
            
            # Only sample text/varchar columns
            if "TEXT" in col_type or "VARCHAR" in col_type or "CHAR" in col_type or not col_type:
                try:
                    cursor.execute(f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL LIMIT 20')
                    unique_values = [row[0] for row in cursor.fetchall() if row[0]]
                    if 2 <= len(unique_values) <= 15:  # Only include if reasonable number of unique values
                        column_value_samples[col_name] = unique_values
                except:
                    pass
        
        result["column_value_samples"] = column_value_samples
        
        conn.close()
        
    except Exception as e:
        print(f"DEBUG [get_rich_metadata_for_sql]: Error getting rich metadata: {e}")
    
    return result


# =============================================================================
# ROUTER CALL (CALL 1)
# =============================================================================

def build_router_prompt(
    question: str,
    memory_context: str,
    stripped_metadata: List[Dict[str, Any]]
) -> str:
    """
    Build the lightweight router prompt.
    
    Args:
        question: User question
        memory_context: Last 2 conversation messages
        stripped_metadata: Lightweight metadata (no DDL, no column samples)
        
    Returns:
        Router prompt string
    """
    # Format metadata summary
    metadata_summary_parts = []
    for i, entry in enumerate(stripped_metadata):
        parts = [f"Document {i+1}:"]
        if entry.get("file_name"):
            parts.append(f"  File: {entry['file_name']}")
        parts.append(f"  Storage: {entry.get('storage_mode', 'rag')}")
        if entry.get("summary"):
            parts.append(f"  Summary: {entry['summary']}")
        if entry.get("keywords"):
            parts.append(f"  Keywords: {', '.join(entry['keywords'])}")
        if entry.get("sample_columns"):
            parts.append(f"  Columns: {', '.join(entry['sample_columns'])} ({entry.get('column_count', 0)} total)")
        metadata_summary_parts.append("\n".join(parts))
    
    filtered_metadata_summary = "\n\n".join(metadata_summary_parts) if metadata_summary_parts else "No documents available."
    
    # Build conversation context section - check if it's SQL context from follow-up
    context_section = ""
    if memory_context:
        # Check if this is follow-up SQL context (starts with "Previous query used column")
        if "Previous query used column" in memory_context:
            context_section = f"""
IMPORTANT FOLLOW-UP CONTEXT:
{memory_context}
This is a follow-up query. USE THE SAME COLUMN from the previous query - do NOT ask for clarification about which column to use.
"""
        else:
            context_section = f"""
Previous conversation:
{memory_context}
"""
    
    prompt = f"""You are a query router. Your ONLY job is to classify the user's question and rewrite it clearly.

You have access to the following documents in this workspace:
{filtered_metadata_summary}
{context_section}
User question: {question}

Classify the question into EXACTLY ONE of these intents (NO OTHER VALUES ALLOWED):
- "structured": question asks for counts, aggregations, filters, sums, averages, comparisons, rankings, or any data retrieval from tabular data
- "rag": question asks for explanations, summaries, descriptions, or information from unstructured text documents
- "hybrid": question needs both a calculation AND contextual explanation from documents
- "email": user wants to send emails/notifications to people from previous query results (e.g., "send them emails", "notify those employees")
- "greeting": casual greeting or small talk (hi, hello, hey, etc.)
- "clarification": question is genuinely ambiguous and cannot be answered without more information
- "out_of_scope": question has no relation to any document in the workspace

CRITICAL RULES - FOLLOW EXACTLY:

1. DATASET MATCHING:
   - If user mentions a filename (e.g., "employees.csv") and it exists in the metadata → ASSUME that dataset, DO NOT ask clarification
   - If user mentions a column name and it exists in the metadata → ASSUME that column, DO NOT ask clarification
   - Treat singular/plural as equivalent: "rating" = "ratings", "salary" = "salaries", "employee" = "employees"

2. INTENT SELECTION:
   - If ANY document has storage_mode "sqlite" or "hybrid" AND the question involves numbers, counts, filters, lists, or data retrieval → "structured"
   - Words like "retrieve", "get", "show", "list", "find", "count", "how many" with tabular data → "structured"
   - "summarise", "overview", "describe my data" → "hybrid"
   - If the question references column names or asks to list/filter/count data → "structured"
   - Words like "send", "email", "notify" with pronouns "them", "those", "these people" → "email"

3. CLARIFICATION - ONLY ASK WHEN:
   - Multiple datasets have the SAME column name AND user did NOT specify which dataset
   - Query is genuinely missing critical information that cannot be inferred
   - NEVER ask clarification if user explicitly named the file or column
   - NEVER ask clarification for singular/plural variations
   - NEVER ask "is X the correct column?" if column X exists in metadata
   - NEVER ask clarification if FOLLOW-UP CONTEXT tells you which column was used previously

4. FOLLOW-UP HANDLING:
   - If FOLLOW-UP CONTEXT is provided with column info, USE that column - do NOT ask for clarification
   - If previous conversation shows assistant asked a clarification AND user's current message is a confirmation (yes, ok, correct, etc.) → mark is_follow_up=true and use the PREVIOUS rewritten_query
   - Confidence should be 1.0 for clear follow-up confirmations

5. NEVER OUTPUT THESE INVALID INTENTS: "action", "confirm", "confirmation", "approve"
   - If user says "yes", "proceed", "confirm" → this is a FOLLOW-UP, not a separate intent

Return ONLY this JSON (no markdown, no explanation):
{{"intent": "structured | rag | hybrid | email | greeting | clarification | out_of_scope", "rewritten_query": "clear standalone question with context resolved", "is_follow_up": true | false, "clarification_question": "only if intent is clarification, else null", "confidence": 0.0-1.0}}"""

    return prompt


def parse_router_response(response: str) -> Dict[str, Any]:
    """
    Parse router LLM response with robust handling of various formats.
    
    Args:
        response: Raw LLM response
        
    Returns:
        Parsed dictionary
    """
    original_response = response
    response = response.strip()
    
    # Log raw response for debugging
    print(f"DEBUG [parse_router_response]: Raw response length: {len(response)}")
    
    # Remove markdown code blocks - handle various formats
    # Handle ```json ... ```
    if "```json" in response:
        start = response.find("```json") + 7
        end = response.find("```", start)
        if end != -1:
            response = response[start:end]
    # Handle ``` ... ``` without json marker
    elif response.startswith("```"):
        response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
    # Handle trailing ```
    elif response.endswith("```"):
        response = response[:-3]
    
    response = response.strip()
    
    # Try to find JSON object in response if it has extra text
    if not response.startswith("{"):
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start != -1 and json_end > json_start:
            response = response[json_start:json_end]
    
    print(f"DEBUG [parse_router_response]: Cleaned response: {response[:500]}...")
    
    try:
        result = json.loads(response)
        
        # Validate and normalize intent
        raw_intent = result.get("intent", "rag").lower().strip()
        
        # Map invalid intents to valid ones
        intent_mapping = {
            "action": None,  # Will be handled as follow-up
            "confirm": None,
            "confirmation": None,
            "approve": None,
            "approved": None,
        }
        
        if raw_intent in intent_mapping:
            print(f"WARNING [parse_router_response]: Invalid intent '{raw_intent}' detected, treating as follow-up")
            result["intent"] = "rag"  # Default fallback, will be overridden by follow-up logic
            result["is_follow_up"] = True
        elif raw_intent not in VALID_INTENTS:
            print(f"WARNING [parse_router_response]: Unknown intent '{raw_intent}', falling back to 'rag'")
            result["intent"] = "rag"
        else:
            result["intent"] = raw_intent
        
        # Ensure boolean
        result["is_follow_up"] = bool(result.get("is_follow_up", False))
        
        # Ensure confidence is float
        try:
            result["confidence"] = float(result.get("confidence", 0.5))
            result["confidence"] = max(0.0, min(1.0, result["confidence"]))
        except (TypeError, ValueError):
            result["confidence"] = 0.5
        
        # Ensure clarification_question is proper
        if result.get("clarification_question") == "null":
            result["clarification_question"] = None
        
        print(f"DEBUG [parse_router_response]: Parsed successfully - intent={result['intent']}, confidence={result['confidence']}")
        return result
        
    except json.JSONDecodeError as e:
        print(f"WARNING [parse_router_response]: JSON decode failed: {e}")
        print(f"WARNING [parse_router_response]: Attempting regex extraction...")
        
        # Try regex extraction as fallback
        intent_match = re.search(r'"intent"\s*:\s*"([^"]+)"', original_response)
        rewritten_match = re.search(r'"rewritten_query"\s*:\s*"([^"]+)"', original_response)
        follow_up_match = re.search(r'"is_follow_up"\s*:\s*(true|false)', original_response, re.IGNORECASE)
        confidence_match = re.search(r'"confidence"\s*:\s*([0-9.]+)', original_response)
        clarification_match = re.search(r'"clarification_question"\s*:\s*"([^"]+)"', original_response)
        
        intent = intent_match.group(1).lower() if intent_match else "rag"
        
        # Validate intent from regex extraction
        if intent not in VALID_INTENTS:
            print(f"WARNING [parse_router_response]: Invalid intent from regex '{intent}', using 'rag'")
            intent = "rag"
        
        result = {
            "intent": intent,
            "rewritten_query": rewritten_match.group(1) if rewritten_match else None,
            "is_follow_up": follow_up_match.group(1).lower() == "true" if follow_up_match else False,
            "clarification_question": clarification_match.group(1) if clarification_match else None,
            "confidence": float(confidence_match.group(1)) if confidence_match else 0.3
        }
        
        print(f"DEBUG [parse_router_response]: Regex extraction result - intent={result['intent']}")
        return result


def _is_confirmation_message(question: str) -> bool:
    """
    Check if the question is a simple confirmation message.
    
    Args:
        question: User's question
        
    Returns:
        True if question appears to be a confirmation
    """
    question_clean = question.lower().strip().rstrip("!.,?")
    
    # Check exact matches
    if question_clean in CONFIRMATION_PATTERNS:
        return True
    
    # Check if question starts with confirmation word and is short
    words = question_clean.split()
    if len(words) <= 3 and words[0] in ["yes", "ok", "okay", "sure", "yep", "yeah", "correct", "right"]:
        return True
    
    return False


def _extract_previous_context(memory_context: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract previous intent and rewritten query from memory context.
    
    Args:
        memory_context: Formatted memory context string
        
    Returns:
        Tuple of (previous_intent, previous_rewritten_query)
    """
    if not memory_context:
        return None, None
    
    # Look for assistant's last clarification question
    # If the last assistant message was asking for clarification, this might be a confirmation
    lines = memory_context.strip().split("\n")
    
    previous_user_query = None
    assistant_asked_clarification = False
    
    for line in lines:
        if line.startswith("user:"):
            previous_user_query = line[5:].strip()
        elif line.startswith("assistant:"):
            assistant_msg = line[10:].strip().lower()
            # Check if assistant was asking a clarification question
            if any(phrase in assistant_msg for phrase in [
                "could you", "can you", "would you", "please confirm", "please specify",
                "which", "is it", "do you mean", "?"
            ]):
                assistant_asked_clarification = True
    
    if assistant_asked_clarification and previous_user_query:
        return "structured", previous_user_query  # Assume structured for now, can be refined
    
    return None, previous_user_query


def route_query(
    question: str,
    memory_context: str,
    filtered_metadata: List[Dict[str, Any]],
    llm_provider: Optional[str] = None
) -> RouterResult:
    """
    CALL 1: Lightweight intent classification and query rewriting.
    
    This call receives stripped metadata (no DDL, no column samples).
    Its ONLY job is to classify intent and rewrite the query.
    
    Args:
        question: User's question
        memory_context: Last 2 conversation messages formatted
        filtered_metadata: Pre-filtered metadata list
        llm_provider: Override LLM provider
        
    Returns:
        RouterResult with intent and rewritten query
    """
    print(f"\n{'='*60}")
    print(f"DEBUG [route_query]: CALL 1 - Router/Intent Classification")
    print(f"DEBUG [route_query]: Question = '{question}'")
    print(f"DEBUG [route_query]: Metadata entries = {len(filtered_metadata)}")
    
    # Quick check for obvious greetings
    greeting_patterns = ["hi", "hello", "hey", "good morning", "good evening", "howdy", "sup", "yo"]
    question_clean = question.lower().strip().rstrip("!.,?")
    if question_clean in greeting_patterns or (len(question.split()) <= 2 and any(g in question_clean for g in greeting_patterns)):
        print(f"DEBUG [route_query]: Detected greeting pattern")
        return RouterResult.greeting()
    
    # Check for confirmation messages (yes, ok, correct, etc.)
    if _is_confirmation_message(question):
        print(f"DEBUG [route_query]: Detected confirmation message")
        prev_intent, prev_query = _extract_previous_context(memory_context)
        
        if prev_query:
            print(f"DEBUG [route_query]: Reusing previous query: {prev_query}")
            # Determine intent based on metadata
            has_structured = any(
                m.get("type") == "structured" or m.get("storageMode") in ["sqlite", "hybrid"]
                for m in filtered_metadata
            )
            intent = "structured" if has_structured else "rag"
            return RouterResult.follow_up_confirmation(intent, prev_query)
        else:
            print(f"DEBUG [route_query]: No previous query found, treating as unclear")
            # No previous context to confirm, ask what they want
            return RouterResult(
                intent="clarification",
                rewritten_query=None,
                is_follow_up=False,
                clarification_question="I'm not sure what you're confirming. Could you please rephrase your question?",
                confidence=0.3
            )
    
    # If no metadata, return out of scope
    if not filtered_metadata:
        print(f"DEBUG [route_query]: No metadata available, returning out_of_scope")
        return RouterResult.out_of_scope(question)
    
    # Strip metadata for lightweight router call
    stripped_metadata = strip_metadata_for_router(filtered_metadata)
    print(f"DEBUG [route_query]: Stripped metadata for router:")
    for i, m in enumerate(stripped_metadata):
        print(f"  [{i}] file={m.get('file_name')}, storage={m.get('storage_mode')}, cols={m.get('sample_columns', [])[:3]}")
    
    # Build prompt
    prompt = build_router_prompt(question, memory_context, stripped_metadata)
    print(f"\nDEBUG [route_query]: ROUTER PROMPT ({len(prompt)} chars):\n{'-'*40}\n{prompt[:2500]}...\n{'-'*40}")
    
    # Call LLM with increased max_tokens
    try:
        llm = get_llm(provider=llm_provider, temperature=0.1, max_tokens=8000)
        print(f"DEBUG [route_query]: Calling LLM (max_tokens=8000)...")
        
        response = llm.generate(prompt)
        print(f"\nDEBUG [route_query]: RAW RESPONSE (len={len(response) if response else 0}):\n{'-'*40}\n{response}\n{'-'*40}")
        
        if not response:
            print(f"DEBUG [route_query]: Empty response, returning fallback")
            return RouterResult.fallback_clarification()
        
        # Parse response
        parsed = parse_router_response(response)
        print(f"\nDEBUG [route_query]: PARSED RESULT:")
        print(f"  intent = {parsed.get('intent')}")
        print(f"  confidence = {parsed.get('confidence')}")
        print(f"  is_follow_up = {parsed.get('is_follow_up')}")
        print(f"  rewritten_query = {parsed.get('rewritten_query')}")
        print(f"  clarification_question = {parsed.get('clarification_question')}")
        
        return RouterResult.from_dict(parsed)
        
    except Exception as e:
        print(f"DEBUG [route_query]: ERROR - {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Retry with stricter prompt
        print(f"DEBUG [route_query]: Retrying with stricter prompt...")
        try:
            strict_prompt = f"""{prompt}

IMPORTANT: Return ONLY valid JSON. No explanation. No markdown. Just the JSON object."""
            
            llm = get_llm(provider=llm_provider, temperature=0.1, max_tokens=8000)
            response = llm.generate(strict_prompt)
            print(f"DEBUG [route_query]: Retry response: {response[:300]}...")
            
            if response:
                parsed = parse_router_response(response)
                return RouterResult.from_dict(parsed)
        except Exception as retry_error:
            print(f"DEBUG [route_query]: Retry failed - {str(retry_error)}")
        
        return RouterResult.fallback_clarification()


# =============================================================================
# SQL GENERATOR CALL (CALL 2)
# =============================================================================

def build_sql_prompt_multi(rewritten_query, rich_metadata_list):
    """
    Build SQL prompt with ALL structured tables.
    LLM decides which table to query, or signals ambiguity.
    """
    tables_section = ""
    for meta in rich_metadata_list:
        table_name = meta.get("tableName", "")
        file_name = meta.get("fileName", table_name)
        ddl = meta.get("table_ddl", f"Table: {table_name}")
        col_samples = meta.get("column_value_samples", {})
        schema_sample = meta.get("schema_sample", [])

        samples_str = ""
        if col_samples:
            samples_str = "\n".join(
                f"  - {col}: {', '.join(str(v) for v in vals[:5])}"
                for col, vals in col_samples.items()
            )

        data_sample_str = ""
        if schema_sample:
            data_sample_str = "\n".join(
                f"  Row {i+1}: " + ", ".join(f"{k}: {v}" for k, v in list(r.items())[:5])
                for i, r in enumerate(schema_sample[:3])
            )

        tables_section += f"""
---
File: {file_name}
Table: {table_name}
Schema:
{ddl}
Column value samples:
{samples_str or "  None"}
Data samples:
{data_sample_str or "  None"}
"""

    prompt = f"""You are a SQL query generator for SQLite.

The user wants to know: {rewritten_query}

You have access to the following tables:
{tables_section}

Your job:
1. Decide which single table best answers the user's question
2. If you can clearly identify the right table → write the SQL query
3. If AMBIGUOUS (multiple tables have identical or very similar column names and you cannot determine which one the user means) → respond with ONLY this JSON:
   {{"ambiguous": true, "reason": "brief reason", "options": ["file1.csv", "file2.csv"]}}

Rules:
- SQLite only, SELECT only
- Use only columns from the chosen table
- No markdown, no backticks
- If not ambiguous: return ONLY the raw SQL query
- If ambiguous: return ONLY the JSON above, nothing else

IMPORTANT TEXT SEARCH RULES:
- When searching for names, persons, or text values, ALWAYS use LIKE with wildcards: LIKE '%search_term%'
- NEVER use exact equality (=) for name/person searches
- Example: WHERE "Party_2" LIKE '%vikash kumar%' (NOT WHERE "Party_2" = 'vikash kumar')
- For partial matches: LIKE '%partial_name%'
- Use COLLATE NOCASE for case-insensitive matching when needed"""

    return prompt


def clean_sql_response(response: str) -> str:
    """
    Clean SQL response by removing markdown and extra formatting.
    
    Args:
        response: Raw SQL response
        
    Returns:
        Cleaned SQL string
    """
    response = response.strip()
    
    # Remove markdown code blocks
    if response.startswith("```sql"):
        response = response[6:]
    elif response.startswith("```"):
        response = response[3:]
    
    if response.endswith("```"):
        response = response[:-3]
    
    response = response.strip()
    
    # Remove any leading "sql" text
    if response.lower().startswith("sql"):
        response = response[3:].strip()
    
    # Ensure it starts with SELECT
    if not response.upper().startswith("SELECT"):
        # Try to find SELECT in the response
        select_idx = response.upper().find("SELECT")
        if select_idx != -1:
            response = response[select_idx:]
    
    return response.strip()


def generate_sql(rewritten_query, metadata_entry, workspace_id, llm_provider=None):
    """
    Generate SQL query for the given rewritten query.
    
    Args:
        rewritten_query: The rewritten user query
        metadata_entry: Metadata entry or list of entries
        workspace_id: Workspace ID for database access
        llm_provider: LLM provider override
        
    Returns:
        SQL query string, ambiguity dict, or None
    """
    # Get ALL structured metadata, not just one
    # metadata_entry here becomes a list
    if isinstance(metadata_entry, list):
        rich_metadata_list = [get_rich_metadata_for_sql(m, workspace_id) for m in metadata_entry]
    else:
        rich_metadata_list = [get_rich_metadata_for_sql(metadata_entry, workspace_id)]

    prompt = build_sql_prompt_multi(rewritten_query, rich_metadata_list)
    
    print(f"DEBUG [generate_sql]: Prompt length = {len(prompt)} chars")
    
    # Use higher max_tokens to ensure full SQL is returned
    llm = get_llm(provider=llm_provider, temperature=0.0, max_tokens=8000)
    response = llm.generate(prompt)
    
    if not response:
        print(f"WARNING [generate_sql]: Empty response from LLM")
        return None
    
    response = response.strip()
    print(f"DEBUG [generate_sql]: Raw response (len={len(response)}): {response[:500]}...")

    # Check if LLM returned ambiguity signal
    if response.startswith("{") and "ambiguous" in response:
        try:
            data = json.loads(response)
            if data.get("ambiguous"):
                print(f"DEBUG [generate_sql]: Ambiguous query detected - {data.get('reason')}")
                # Return special signal — query_service will handle it
                return {"ambiguous": True, "options": data.get("options", [])}
        except json.JSONDecodeError:
            pass

    sql = clean_sql_response(response)
    print(f"DEBUG [generate_sql]: Cleaned SQL: {sql[:200]}...")
    
    if not sql.upper().startswith("SELECT"):
        print(f"WARNING [generate_sql]: SQL does not start with SELECT")
        return None
    return sql

# =============================================================================
# MAIN PLANNER FUNCTION (combines both calls)
# =============================================================================

def find_all_structured_metadata(metadata_list):
    """Return ALL structured docs, not just first one."""
    return [
        entry for entry in metadata_list
        if entry.get("type") == "structured" or
           entry.get("storageMode") in ["sqlite", "hybrid"]
    ]


def planner_decision(
    question: str,
    memory_context: str,
    metadata_list: List[Dict[str, Any]],
    llm_provider: Optional[str] = None,
    workspace_id: Optional[str] = None
) -> PlannerResult:
    """
    Main planner function - orchestrates the two-call flow.
    
    Step 1: Call route_query() → get intent, rewritten_query, confidence
    Step 2: If confidence < 0.6 → return clarification
    Step 3: If intent is structured/hybrid → call generate_sql()
    Step 4: Return combined PlannerResult
    
    Args:
        question: User's question
        memory_context: Last 2 conversation messages formatted
        metadata_list: Full metadataForQuery list
        llm_provider: Override LLM provider
        workspace_id: Workspace ID (needed for SQL generation)
        
    Returns:
        PlannerResult with routing decision and optional SQL
    """
    print(f"\n{'='*60}")
    print(f"DEBUG [planner_decision]: Starting 2-call planner flow...")
    print(f"DEBUG [planner_decision]: Question = '{question}'")
    print(f"DEBUG [planner_decision]: Metadata entries = {len(metadata_list) if metadata_list else 0}")
    
    # Pre-filter metadata
    filtered_metadata = prefilter_metadata(question, metadata_list, max_entries=5)
    print(f"DEBUG [planner_decision]: Filtered to {len(filtered_metadata)} entries")
    
    # =========================================================================
    # CALL 1: Router (intent classification)
    # =========================================================================
    router_result = route_query(
        question=question,
        memory_context=memory_context,
        filtered_metadata=filtered_metadata,
        llm_provider=llm_provider
    )
    
    print(f"\nDEBUG [planner_decision]: Router result:")
    print(f"  intent = {router_result.intent}")
    print(f"  confidence = {router_result.confidence}")
    print(f"  is_follow_up = {router_result.is_follow_up}")
    
    # =========================================================================
    # CONFIDENCE GATE
    # =========================================================================
    if router_result.confidence < 0.4 and router_result.intent not in ["greeting", "out_of_scope"]:
        print(f"DEBUG [planner_decision]: Low confidence ({router_result.confidence}), returning clarification")
        return PlannerResult(
            intent="clarification",
            is_follow_up=router_result.is_follow_up,
            is_complete=False,
            rewritten_query=router_result.rewritten_query,
            sql_query=None,
            clarification_question=router_result.clarification_question or "Could you please provide more details about what you're looking for?",
            confidence=router_result.confidence
        )
    
    # =========================================================================
    # HANDLE NON-STRUCTURED INTENTS
    # =========================================================================
    if router_result.intent in ["greeting", "out_of_scope", "clarification", "rag"]:
        print(f"DEBUG [planner_decision]: Non-structured intent '{router_result.intent}', skipping SQL generation")
        return PlannerResult.from_router_result(router_result, sql_query=None)
    
    # Handle email intent
    if router_result.intent == "email":
        print(f"DEBUG [planner_decision]: Email intent detected, passing through")
        return PlannerResult.from_router_result(router_result, sql_query=None)
    
    # =========================================================================
    # CALL 2: SQL Generation (for structured/hybrid)
    # =========================================================================
    if router_result.intent in ["structured", "hybrid"]:
        print(f"\nDEBUG [planner_decision]: Intent is {router_result.intent}, proceeding to SQL generation...")
        
        # Find structured metadata
        structured_meta = find_all_structured_metadata(filtered_metadata)
        
        if not structured_meta:
            print(f"DEBUG [planner_decision]: No structured metadata found, falling back to RAG")
            return PlannerResult(
                intent="rag",
                is_follow_up=router_result.is_follow_up,
                is_complete=True,
                rewritten_query=router_result.rewritten_query or question,
                sql_query=None,
                clarification_question=None,
                confidence=router_result.confidence
            )
        
        if not workspace_id:
            print(f"DEBUG [planner_decision]: No workspace_id provided, cannot generate SQL")
            return PlannerResult.from_router_result(router_result, sql_query=None)
        
        # Generate SQL
        sql_query = generate_sql(
            rewritten_query=router_result.rewritten_query or question,
            metadata_entry=structured_meta,
            workspace_id=workspace_id,
            llm_provider=llm_provider
        )
        
        print(f"\nDEBUG [planner_decision]: SQL generated: {sql_query}")
        
        # Handle ambiguous result from SQL generator
        if isinstance(sql_query, dict) and sql_query.get("ambiguous"):
            print(f"DEBUG [planner_decision]: SQL generator returned ambiguous result")
            options = sql_query.get("options", [])
            reason = sql_query.get("reason", "Multiple tables could match your query.")
            
            # Build clarification message
            if options:
                clarification_msg = f"{reason} Available options: {', '.join(options)}. Which one would you like to query?"
            else:
                clarification_msg = f"{reason} Could you please specify which dataset you want to query?"
            
            return PlannerResult(
                intent="clarification",
                is_follow_up=router_result.is_follow_up,
                is_complete=False,
                rewritten_query=router_result.rewritten_query,
                sql_query=None,
                clarification_question=clarification_msg,
                confidence=0.5,
                clarification_options=options if options else None
            )
        
        if not sql_query and router_result.intent == "structured":
            # SQL generation failed for structured intent - fall back to clarification
            print(f"DEBUG [planner_decision]: SQL generation failed, returning clarification")
            return PlannerResult(
                intent="clarification",
                is_follow_up=router_result.is_follow_up,
                is_complete=False,
                rewritten_query=router_result.rewritten_query,
                sql_query=None,
                clarification_question="I understood your question but couldn't generate a proper query. Could you rephrase it?",
                confidence=0.4
            )
        
        return PlannerResult.from_router_result(router_result, sql_query=sql_query)
    
    # Default fallback
    print(f"DEBUG [planner_decision]: Unexpected intent '{router_result.intent}', falling back to RAG")
    return PlannerResult.fallback_semantic(question)
