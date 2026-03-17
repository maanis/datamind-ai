"""
Memory management for conversation history.
Stores conversation in workspace document.
"""

from typing import List, Dict, Any, Optional
from bson import ObjectId

from mongo_utils import workspaces_collection


def get_conversation_memory(workspace_id: str) -> List[Dict[str, str]]:
    """
    Get conversation memory for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        List of message dicts with role and content
    """
    try:
        workspace = workspaces_collection.find_one(
            {"_id": ObjectId(workspace_id)},
            {"conversationMemory": 1}
        )
        
        if workspace and "conversationMemory" in workspace:
            messages = workspace["conversationMemory"].get("messages", [])
            return messages
        
        return []
    except Exception as e:
        print(f"Error getting conversation memory: {str(e)}")
        return []


def save_conversation_memory(
    workspace_id: str,
    messages: List[Dict[str, str]],
    max_messages: int = 6
) -> bool:
    """
    Save conversation memory for a workspace.
    Automatically trims to max_messages.
    
    Args:
        workspace_id: Workspace ID
        messages: List of message dicts
        max_messages: Maximum messages to keep
        
    Returns:
        True if successful
    """
    try:
        # Trim messages
        if len(messages) > max_messages:
            messages = messages[-max_messages:]
        
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {
                "$set": {
                    "conversationMemory": {
                        "messages": messages
                    }
                }
            }
        )
        
        return result.modified_count > 0 or result.matched_count > 0
    except Exception as e:
        print(f"Error saving conversation memory: {str(e)}")
        return False


def clear_conversation_memory(workspace_id: str) -> bool:
    """
    Clear conversation memory for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        True if successful
    """
    try:
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {
                "$set": {
                    "conversationMemory": {
                        "messages": []
                    }
                }
            }
        )
        
        return result.modified_count > 0 or result.matched_count > 0
    except Exception as e:
        print(f"Error clearing conversation memory: {str(e)}")
        return False


def format_memory_for_llm(
    messages: List[Dict[str, str]],
    max_messages: int = 3
) -> str:
    """
    Format conversation memory for LLM context.
    
    Args:
        messages: List of message dicts
        max_messages: Maximum messages to include
        
    Returns:
        Formatted string for LLM context
    """
    if not messages:
        return ""
    
    recent = messages[-max_messages:]
    
    formatted = []
    for msg in recent:
        role = msg.get("role", "user").capitalize()
        content = msg.get("content", "")
        # Truncate long messages
        if len(content) > 300:
            content = content[:300] + "..."
        formatted.append(f"{role}: {content}")
    
    return "\n".join(formatted)


def format_memory_for_classification(
    messages: List[Dict[str, str]],
    max_messages: int = 2
) -> str:
    """
    Format memory for classification (minimal context).
    
    Args:
        messages: List of message dicts
        max_messages: Maximum messages to include
        
    Returns:
        Formatted string for classifier
    """
    if not messages:
        return ""
    
    recent = messages[-max_messages:]
    
    formatted = []
    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Very brief for classification
        if len(content) > 150:
            content = content[:150] + "..."
        formatted.append(f"{role}: {content}")
    
    return "\n".join(formatted)


def get_last_assistant_response(messages: List[Dict[str, str]]) -> Optional[str]:
    """
    Get the last assistant response from memory.
    
    Args:
        messages: List of message dicts
        
    Returns:
        Last assistant response or None
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content")
    return None
