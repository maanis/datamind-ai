"""
Clarification tool for ambiguous queries.
"""

from typing import List, Dict, Any, Optional

from llm.factory import get_llm


def generate_clarification(
    question: str,
    tables_metadata: Optional[List[Dict[str, Any]]] = None,
    ambiguity_reason: Optional[str] = None,
    llm_provider: Optional[str] = None
) -> str:
    """
    Generate a clarification question when user query is ambiguous.
    
    Args:
        question: Original user question
        tables_metadata: Available tables for context
        ambiguity_reason: Why clarification is needed
        llm_provider: Override LLM provider
        
    Returns:
        Clarification question string
    """
    # Build context about available data
    data_context = ""
    if tables_metadata:
        table_names = [t.get("name", "unknown") for t in tables_metadata]
        data_context = f"Available tables: {', '.join(table_names)}"
    else:
        data_context = "Only unstructured document search is available."
    
    prompt = f"""You are a helpful assistant. The user asked a question that needs clarification.

User Question: {question}

Available Data: {data_context}
{f"Reason for clarification: {ambiguity_reason}" if ambiguity_reason else ""}

Generate a brief, friendly clarification question to help understand what the user needs.
Keep it concise (1-2 sentences max).
If multiple tables exist, ask which data source they want to query.

Respond with just the clarification question, nothing else."""

    llm = get_llm(provider=llm_provider, max_tokens=150)
    response = llm.generate(prompt)
    
    return response.strip()


def generate_table_selection_prompt(
    question: str,
    tables_metadata: List[Dict[str, Any]]
) -> str:
    """
    Generate a prompt asking user to select which table to query.
    
    Args:
        question: Original user question
        tables_metadata: Available tables
        
    Returns:
        Selection prompt string
    """
    if not tables_metadata:
        return "No structured data available. I'll search your documents instead."
    
    if len(tables_metadata) == 1:
        return ""  # No need to ask if only one table
    
    table_list = []
    for i, table in enumerate(tables_metadata, 1):
        name = table.get("name", "unknown")
        columns = table.get("columns", [])[:5]  # First 5 columns
        row_count = table.get("row_count", "?")
        
        col_str = ", ".join(columns)
        if len(table.get("columns", [])) > 5:
            col_str += "..."
            
        table_list.append(f"{i}. **{name}** ({row_count} rows) - Columns: {col_str}")
    
    return f"""I found multiple data tables that might be relevant to your question:

{chr(10).join(table_list)}

Which table would you like me to query? You can say the table name or number."""


def needs_clarification(
    question: str,
    tables_metadata: Optional[List[Dict[str, Any]]] = None
) -> tuple[bool, Optional[str]]:
    """
    Check if a question needs clarification.
    
    Args:
        question: User question
        tables_metadata: Available tables
        
    Returns:
        Tuple of (needs_clarification, reason)
    """
    question_lower = question.lower().strip()
    
    # Too short questions often need clarification
    if len(question_lower.split()) < 3:
        return True, "Question is too brief"
    
    # Multiple tables and ambiguous reference
    if tables_metadata and len(tables_metadata) > 1:
        # Check if question mentions a specific table
        table_names = [t.get("name", "").lower() for t in tables_metadata]
        question_mentions_table = any(name in question_lower for name in table_names)
        
        # Check for structured query keywords without table reference
        structured_keywords = ["how many", "count", "total", "list", "show"]
        has_structured_intent = any(kw in question_lower for kw in structured_keywords)
        
        if has_structured_intent and not question_mentions_table:
            return True, "Multiple tables available, unclear which to query"
    
    return False, None
