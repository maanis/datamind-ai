"""
Conversation State Management

Stores and retrieves query execution results for state reuse across conversation turns.
This enables follow-up queries like "send them emails" to reference previous SQL results
without re-running expensive operations.

State Types:
- sql_results: Results from SQL queries (rows, columns, query)
- rag_results: Results from semantic search (chunks, sources)
- email_templates: Generated email templates pending approval
- recipients: Extracted recipient lists for email operations

Storage: MongoDB workspace document (conversationState field)
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from bson import ObjectId

from mongo_utils import workspaces_collection


# Maximum age for state entries (in seconds)
STATE_TTL_SECONDS = 1800  # 30 minutes


def get_conversation_state(workspace_id: str) -> Dict[str, Any]:
    """
    Get conversation state for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        Dict containing conversation state with keys:
        - last_sql_results: Most recent SQL query results
        - last_rag_results: Most recent RAG search results
        - recipients: List of extracted recipients
        - email_templates: Generated email templates pending approval
        - last_updated: Timestamp of last update
    """
    try:
        workspace = workspaces_collection.find_one(
            {"_id": ObjectId(workspace_id)},
            {"conversationState": 1}
        )
        
        if workspace and "conversationState" in workspace:
            state = workspace["conversationState"]
            
            # Check if state is expired
            last_updated = state.get("last_updated")
            if last_updated:
                if isinstance(last_updated, str):
                    last_updated = datetime.fromisoformat(last_updated)
                age = (datetime.utcnow() - last_updated).total_seconds()
                if age > STATE_TTL_SECONDS:
                    # State is stale, clear it
                    clear_conversation_state(workspace_id)
                    return _empty_state()
            
            return state
        
        return _empty_state()
    except Exception as e:
        print(f"Error getting conversation state: {str(e)}")
        return _empty_state()


def _empty_state() -> Dict[str, Any]:
    """Return empty state structure."""
    return {
        "last_sql_results": None,
        "last_rag_results": None,
        "recipients": None,
        "email_templates": None,
        "pending_action": None,
        "last_updated": None
    }


def save_conversation_state(
    workspace_id: str,
    state: Dict[str, Any]
) -> bool:
    """
    Save conversation state for a workspace.
    
    Args:
        workspace_id: Workspace ID
        state: State dictionary to save
        
    Returns:
        True if successful
    """
    try:
        # Add timestamp
        state["last_updated"] = datetime.utcnow()
        
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {"$set": {"conversationState": state}}
        )
        
        return result.modified_count > 0 or result.matched_count > 0
    except Exception as e:
        print(f"Error saving conversation state: {str(e)}")
        return False


def update_sql_results(
    workspace_id: str,
    sql_query: str,
    rows: List[Dict[str, Any]],
    columns: List[str],
    row_count: int
) -> bool:
    """
    Update SQL results in conversation state.
    
    Args:
        workspace_id: Workspace ID
        sql_query: The SQL query that was executed
        rows: Result rows
        columns: Column names
        row_count: Total row count
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["last_sql_results"] = {
        "query": sql_query,
        "rows": rows[:500],  # Limit stored rows
        "columns": columns,
        "row_count": row_count,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Auto-extract potential recipients if email-like columns exist
    recipients = _extract_potential_recipients(rows, columns)
    if recipients:
        state["recipients"] = recipients
    
    return save_conversation_state(workspace_id, state)


def update_rag_results(
    workspace_id: str,
    query: str,
    results: List[Dict[str, Any]]
) -> bool:
    """
    Update RAG results in conversation state.
    
    Args:
        workspace_id: Workspace ID
        query: The search query
        results: Search results
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["last_rag_results"] = {
        "query": query,
        "results": results[:20],  # Limit stored results
        "timestamp": datetime.utcnow().isoformat()
    }
    return save_conversation_state(workspace_id, state)


def update_recipients(
    workspace_id: str,
    recipients: List[Dict[str, Any]]
) -> bool:
    """
    Update recipients list in conversation state.
    
    Args:
        workspace_id: Workspace ID
        recipients: List of recipient dicts with 'email' and optional 'name'
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["recipients"] = recipients
    return save_conversation_state(workspace_id, state)


def update_email_templates(
    workspace_id: str,
    templates: List[Dict[str, Any]]
) -> bool:
    """
    Update email templates in conversation state.
    
    Args:
        workspace_id: Workspace ID
        templates: List of template dicts with 'subject', 'body', 'template_id'
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["email_templates"] = templates
    state["pending_action"] = "email_approval"
    return save_conversation_state(workspace_id, state)


def set_pending_action(
    workspace_id: str,
    action: Optional[str],
    action_data: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Set a pending action that requires user confirmation.
    
    Args:
        workspace_id: Workspace ID
        action: Action type ('email_approval', 'confirm_recipients', etc.)
        action_data: Additional data for the action
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["pending_action"] = action
    if action_data:
        state["pending_action_data"] = action_data
    return save_conversation_state(workspace_id, state)


def get_last_sql_results(workspace_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent SQL results from conversation state.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        SQL results dict or None
    """
    state = get_conversation_state(workspace_id)
    return state.get("last_sql_results")


def get_last_rag_results(workspace_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent RAG results from conversation state.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        RAG results dict or None
    """
    state = get_conversation_state(workspace_id)
    return state.get("last_rag_results")


def get_recipients(workspace_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    Get stored recipients from conversation state.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        Recipients list or None
    """
    state = get_conversation_state(workspace_id)
    return state.get("recipients")


def get_email_templates(workspace_id: str) -> Optional[List[Dict[str, Any]]]:
    """
    Get stored email templates from conversation state.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        Templates list or None
    """
    state = get_conversation_state(workspace_id)
    return state.get("email_templates")


def get_pending_action(workspace_id: str) -> Optional[str]:
    """
    Get pending action type.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        Action type string or None
    """
    state = get_conversation_state(workspace_id)
    return state.get("pending_action")


def clear_pending_action(workspace_id: str) -> bool:
    """
    Clear the pending action.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        True if successful
    """
    state = get_conversation_state(workspace_id)
    state["pending_action"] = None
    state.pop("pending_action_data", None)
    return save_conversation_state(workspace_id, state)


def clear_conversation_state(workspace_id: str) -> bool:
    """
    Clear all conversation state for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        True if successful
    """
    try:
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {"$set": {"conversationState": _empty_state()}}
        )
        return result.modified_count > 0 or result.matched_count > 0
    except Exception as e:
        print(f"Error clearing conversation state: {str(e)}")
        return False


def _extract_potential_recipients(
    rows: List[Dict[str, Any]],
    columns: List[str]
) -> Optional[List[Dict[str, Any]]]:
    """
    Auto-extract potential email recipients from SQL results.
    
    Looks for columns that might contain email addresses and names.
    
    Args:
        rows: SQL result rows
        columns: Column names
        
    Returns:
        List of recipient dicts or None if no email column found
    """
    import re
    
    # Find email column
    email_patterns = ["email", "e-mail", "mail", "email_address", "emailaddress"]
    email_col = None
    for col in columns:
        if col.lower().replace("_", "").replace("-", "") in [p.replace("_", "").replace("-", "") for p in email_patterns]:
            email_col = col
            break
        # Also check if column contains 'email'
        if "email" in col.lower():
            email_col = col
            break
    
    if not email_col:
        return None
    
    # Find name column
    name_patterns = ["name", "full_name", "fullname", "employee_name", "first_name", "firstname"]
    name_col = None
    for col in columns:
        col_clean = col.lower().replace("_", "").replace("-", "")
        if col_clean in [p.replace("_", "") for p in name_patterns]:
            name_col = col
            break
    
    # Extract recipients
    recipients = []
    email_regex = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    
    for row in rows:
        email = row.get(email_col)
        if email and isinstance(email, str) and email_regex.match(email.strip()):
            recipient = {"email": email.strip()}
            if name_col and row.get(name_col):
                recipient["name"] = str(row.get(name_col)).strip()
            recipients.append(recipient)
    
    return recipients if recipients else None


def check_state_reference(question: str) -> Dict[str, bool]:
    """
    Check if the question references previous conversation state.
    
    Args:
        question: User's question
        
    Returns:
        Dict with flags for state references:
        - references_sql: True if referencing SQL results
        - references_rag: True if referencing RAG results
        - references_recipients: True if referencing recipients (them, those, etc.)
        - references_email: True if email action intended
    """
    question_lower = question.lower()
    
    # Pronouns and references that indicate using previous results
    reference_patterns = [
        "them", "those", "these", "the employees", "the results",
        "the people", "the ones", "mentioned", "above", "previous",
        "same", "that list", "those records", "the data"
    ]
    
    # Email action patterns
    email_patterns = [
        "send", "email", "mail", "notify", "inform", "message",
        "termination", "promotion", "notification", "alert"
    ]
    
    references_previous = any(pattern in question_lower for pattern in reference_patterns)
    references_email = any(pattern in question_lower for pattern in email_patterns)
    
    # More specific checks
    references_sql = references_previous and not any(
        pattern in question_lower for pattern in ["document", "article", "text"]
    )
    references_rag = references_previous and any(
        pattern in question_lower for pattern in ["document", "article", "text", "content"]
    )
    
    return {
        "references_sql": references_sql,
        "references_rag": references_rag,
        "references_recipients": references_previous and references_email,
        "references_email": references_email
    }
