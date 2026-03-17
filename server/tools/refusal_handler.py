"""
Refusal handler for out-of-scope queries.
Generates polite refusal messages when queries cannot be answered.
"""

from typing import Optional, List, Dict, Any

from llm.factory import get_llm


def generate_refusal(
    question: str,
    metadata_summaries: Optional[List[str]] = None,
    llm_provider: Optional[str] = None
) -> str:
    """
    Generate a polite refusal message for out-of-scope queries.
    
    Args:
        question: User's question
        metadata_summaries: Optional list of data summaries to suggest
        llm_provider: Override LLM provider
        
    Returns:
        Refusal message string
    """
    print(f"DEBUG [generate_refusal]: Question - '{question}'")
    print(f"DEBUG [generate_refusal]: Available summaries - {metadata_summaries}")
    
    # Build context about available data
    data_context = ""
    if metadata_summaries:
        data_context = f"\n\nI have access to data about: {', '.join(metadata_summaries[:3])}"
    
    llm = get_llm(provider=llm_provider, temperature=0.3, max_tokens=400)
    
    prompt = f"""You are a helpful AI assistant. The user asked a question that cannot be answered with the available data.

User Question: {question}
{data_context}

Generate a polite, brief (2-3 sentences) response that:
1. Acknowledges you cannot answer this specific question
2. Briefly explains why (data doesn't cover this topic)
3. Suggests what you CAN help with based on available data

Be friendly but concise. Do not apologize excessively."""

    try:
        response = llm.generate(prompt)
        return response if response else get_default_refusal(metadata_summaries)
    except Exception:
        return get_default_refusal(metadata_summaries)


def get_default_refusal(metadata_summaries: Optional[List[str]] = None) -> str:
    """
    Get default refusal message when LLM is unavailable.
    
    Args:
        metadata_summaries: Optional list of data summaries
        
    Returns:
        Default refusal message
    """
    base_message = "I don't have the information needed to answer that question."
    
    if metadata_summaries:
        suggestions = ", ".join(metadata_summaries[:3])
        return f"{base_message} Based on your uploaded data, I can help with questions about: {suggestions}."
    
    return f"{base_message} Try asking about your uploaded documents or data."


def handle_no_data() -> str:
    """
    Handle case when no data is available in workspace.
    
    Returns:
        Message encouraging data upload
    """
    return "I don't have any data loaded yet. Please upload some documents or data files, and then I'll be happy to help answer your questions about them."


def handle_sql_error(error_message: str) -> str:
    """
    Handle SQL execution errors with user-friendly message.
    
    Args:
        error_message: Technical error message
        
    Returns:
        User-friendly error message
    """
    # Check for common SQL errors
    if "no such column" in error_message.lower():
        return "I couldn't find one of the columns needed for that query. Could you rephrase your question or check the column names?"
    
    if "no such table" in error_message.lower():
        return "I couldn't find the table needed for that query. The data might not be loaded yet."
    
    if "syntax error" in error_message.lower():
        return "I had trouble understanding that query. Could you try rephrasing your question?"
    
    if "blocked" in error_message.lower() or "not allowed" in error_message.lower():
        return "That type of operation isn't supported. I can only perform read operations on your data."
    
    # Generic fallback
    return "I encountered an issue while querying your data. Could you try rephrasing your question?"


def handle_clarification_needed(
    question: str,
    clarification_question: str
) -> Dict[str, Any]:
    """
    Format clarification response.
    
    Args:
        question: Original user question
        clarification_question: Question to ask user
        
    Returns:
        Response dict with clarification
    """
    return {
        "answer": clarification_question,
        "intent": "clarification",
        "needs_input": True,
        "original_question": question
    }


def handle_greeting(
    question: str,
    llm_provider: Optional[str] = None
) -> str:
    """
    Handle greeting/chitchat messages.
    
    Args:
        question: User's greeting
        llm_provider: Override LLM provider
        
    Returns:
        Greeting response
    """
    print(f"DEBUG [handle_greeting]: Processing greeting - '{question}'")
    try:
        llm = get_llm(provider=llm_provider, temperature=0.5, max_tokens=300)
        
        prompt = f"""You are a friendly AI assistant for a data platform.
The user said: {question}

Respond warmly in 1-2 sentences. Offer to help with their data questions."""
        
        response = llm.generate(prompt)
        return response if response else "Hello! I'm here to help you explore and analyze your data. What would you like to know?"
    except Exception:
        return "Hello! I'm here to help you explore and analyze your data. What would you like to know?"
