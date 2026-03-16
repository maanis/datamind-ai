"""
Query intent classifier.
Uses rule-based signals + LLM for classification.
"""

from typing import List, Dict, Any, Optional

from router.intent_types import (
    IntentType, 
    ClassificationResult,
    STRUCTURED_KEYWORDS,
    SEMANTIC_KEYWORDS,
    GREETING_PATTERNS
)
from llm.factory import get_llm


def _check_rule_based_signals(question: str) -> Dict[str, float]:
    """
    Check rule-based signals for initial classification hints.
    
    Args:
        question: User's question
        
    Returns:
        Dictionary with signal scores for each intent type
    """
    question_lower = question.lower().strip()
    
    scores = {
        "semantic": 0.0,
        "structured": 0.0,
        "greeting": 0.0
    }
    
    # Check for greeting patterns
    for pattern in GREETING_PATTERNS:
        if question_lower.startswith(pattern) or question_lower == pattern:
            scores["greeting"] += 0.8
            break
    
    # Check for structured keywords
    for keyword in STRUCTURED_KEYWORDS:
        if keyword in question_lower:
            scores["structured"] += 0.15
    
    # Check for semantic keywords
    for keyword in SEMANTIC_KEYWORDS:
        if keyword in question_lower:
            scores["semantic"] += 0.15
    
    # Contains numbers often indicates structured query
    if any(char.isdigit() for char in question):
        scores["structured"] += 0.1
    
    # Question marks and longer questions lean semantic
    if "?" in question and len(question.split()) > 10:
        scores["semantic"] += 0.1
    
    # Cap scores at 1.0
    return {k: min(v, 1.0) for k, v in scores.items()}


def _build_classification_prompt(
    question: str,
    memory_context: str,
    tables_metadata: List[Dict[str, Any]]
) -> str:
    """
    Build prompt for LLM classification.
    
    Args:
        question: Current user question
        memory_context: Last 2-3 messages for context
        tables_metadata: Available tables with schema info
        
    Returns:
        Classification prompt
    """
    tables_info = ""
    if tables_metadata:
        for table in tables_metadata:
            tables_info += f"\n- Table: {table.get('name', 'unknown')}"
            if table.get("columns"):
                tables_info += f"\n  Columns: {', '.join(table['columns'][:10])}"
            if table.get("row_count"):
                tables_info += f"\n  Rows: {table['row_count']}"
    else:
        tables_info = "\nNo structured tables available."
    
    prompt = f"""You are a query classifier for a RAG system.

Classify the user's intent into ONE of these categories:
- "semantic": Question about meaning, explanation, or unstructured content
- "structured": Question requiring data aggregation, counting, filtering (needs SQL)
- "hybrid": Question needs both text search AND data aggregation
- "clarification": Question is ambiguous or missing critical information
- "greeting": Simple greeting or chitchat

Available Data:
- Unstructured text documents (searchable via embeddings)
{tables_info}

Recent Conversation:
{memory_context if memory_context else "No prior context."}

Current Question: {question}

Respond with JSON only:
{{"intent": "<category>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}"""

    return prompt


def classify_intent(
    question: str,
    memory_context: Optional[str] = None,
    tables_metadata: Optional[List[Dict[str, Any]]] = None,
    llm_provider: Optional[str] = None
) -> ClassificationResult:
    """
    Classify the intent of a user question.
    Uses rule-based signals + LLM for final classification.
    
    Args:
        question: User's question
        memory_context: Last 2-3 conversation messages
        tables_metadata: Available structured tables info
        llm_provider: Override LLM provider
        
    Returns:
        ClassificationResult with intent and confidence
    """
    # Quick rule-based check
    signals = _check_rule_based_signals(question)
    
    # Fast path for obvious greetings
    if signals["greeting"] >= 0.8:
        return ClassificationResult(
            intent=IntentType.GREETING,
            confidence=0.95,
            reasoning="Detected greeting pattern"
        )
    
    # Fast path for highly confident structured signals
    if signals["structured"] >= 0.6 and tables_metadata:
        # Still use LLM but hint towards structured
        pass
    
    # Use LLM for classification
    try:
        llm = get_llm(provider=llm_provider, max_tokens=200)
        
        prompt = _build_classification_prompt(
            question=question,
            memory_context=memory_context or "",
            tables_metadata=tables_metadata or []
        )
        
        result = llm.generate_json(prompt)
        
        intent_str = result.get("intent", "semantic").lower()
        confidence = float(result.get("confidence", 0.7))
        reasoning = result.get("reasoning", "")
        
        # Map string to IntentType
        intent_map = {
            "semantic": IntentType.SEMANTIC,
            "structured": IntentType.STRUCTURED,
            "hybrid": IntentType.HYBRID,
            "clarification": IntentType.CLARIFICATION,
            "greeting": IntentType.GREETING
        }
        
        intent = intent_map.get(intent_str, IntentType.SEMANTIC)
        
        # Adjust confidence based on rule signals
        if intent == IntentType.STRUCTURED and signals["structured"] > 0:
            confidence = min(confidence + 0.1, 1.0)
        if intent == IntentType.SEMANTIC and signals["semantic"] > 0:
            confidence = min(confidence + 0.1, 1.0)
        
        # If structured but no tables available, fallback to semantic
        if intent == IntentType.STRUCTURED and not tables_metadata:
            intent = IntentType.SEMANTIC
            reasoning = "No structured tables available, falling back to semantic search"
        
        return ClassificationResult(
            intent=intent,
            confidence=confidence,
            reasoning=reasoning
        )
        
    except Exception as e:
        # Fallback to rule-based classification on LLM failure
        if signals["structured"] > signals["semantic"]:
            intent = IntentType.STRUCTURED if tables_metadata else IntentType.SEMANTIC
        else:
            intent = IntentType.SEMANTIC
            
        return ClassificationResult(
            intent=intent,
            confidence=0.5,
            reasoning=f"LLM classification failed, using rule-based fallback: {str(e)}"
        )
