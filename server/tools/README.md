# Tools — README

Each file in `tools/` is a **self-contained tool** that the query service can call.

## Current Tools

| File | Purpose | Called by |
|---|---|---|
| `semantic_search.py` | Hybrid dense+BM25 search + reranking | `_handle_rag`, `_handle_hybrid` |
| `sql_tool.py` | SQL query generation prompt | `planner/planner.py` |
| `sql_executor.py` | SQLite query execution + validation | `_handle_structured`, `_handle_hybrid` |
| `clarification_tool.py` | Generate clarification questions | `_handle_clarification` |
| `refusal_handler.py` | Generate refusal / greeting / error messages | All handlers |
| `email_tool.py` | Send emails with template personalization | `_handle_email`, `_handle_action` |

---

## Tool Interface Pattern

Every tool follows this pattern so they are easy to add and swap:

```python
# tools/my_new_tool.py

def my_tool(
    workspace_id: str,
    query: str,
    **kwargs
) -> dict:
    """
    Brief description of what this tool does.
    
    Args:
        workspace_id: Always required for workspace isolation
        query: The user query or processed input
        
    Returns:
        Dict with at minimum: { "success": bool, "result": ... }
    """
    # ... implementation
    return {"success": True, "result": ...}
```

Then register it in `query_service.py`:

```python
# In the appropriate _handle_* method:
from tools.my_new_tool import my_tool

result = my_tool(workspace_id=workspace_id, query=search_query)
emitter.emit("Running my new tool...", tool="my_new_tool")
```

And add the tool name to the `StreamEvent.events.tool` enum in Node.js:
```js
// nodejs-server/models/StreamEvent.js
tool: {
    enum: [...existing..., 'my_new_tool']
}
```

---

## Workspace Isolation

**Every tool MUST scope its operations to `workspace_id`.**

- `semantic_search`: collection = `ws_{workspace_id}`
- `sql_executor`: DB path = `sqlite_data/{userId}/data_{workspaceId}.db`
- Never query across workspaces

---

## Adding a Web Search Tool (example)

```python
# tools/web_search_tool.py

import requests

def web_search(workspace_id: str, query: str, max_results: int = 5) -> dict:
    """
    Search the web for additional context.
    Only call this when workspace data is insufficient.
    """
    # ... call search API
    return {
        "success": True,
        "results": [...],
        "source": "web"
    }
```

Then in `planner/intent_types.py`, add `"web_search"` as a valid intent,
and handle it in `query_service.py` in a new `_handle_web_search()` method.

---

## Streaming Events

Each tool should emit a stream event when it starts and when it completes:

```python
emitter.emit("🔍 Searching web...", tool="web_search_tool")
result = web_search(workspace_id, query)
emitter.emit(f"Found {len(result['results'])} results", tool="web_search_tool")
```

The frontend sees these as live progress indicators.

---

## Email Tool

The email tool (`email_tool.py`) integrates with conversation state to send emails.

### Usage

```python
from tools.email_tool import send_email, preview_email

# Recipients format
recipients = [
    {"email": "john@example.com", "name": "John Doe"},
    {"email": "jane@example.com", "name": "Jane Smith"}
]

# Preview emails before sending
preview = preview_email(
    workspace_id=workspace_id,
    recipients=recipients,
    subject="Important Update",
    body="Dear {{name}}, ..."
)

# Send emails
result = send_email(
    workspace_id=workspace_id,
    recipients=recipients,
    subject="Important Update",
    body="Dear {{name}}, This is to inform you...",
    emit_callback=emitter.emit  # For progress events
)

# Result format
# {
#     "success": True,
#     "sent": 5,
#     "failed": 1,
#     "errors": [...],
#     "message": "Sent 5 emails. 1 failed."
# }
```

### Template Personalization

Use `{{name}}` and `{{email}}` placeholders in subject and body.
If recipient has no name, defaults to "Dear Employee" or "Valued Team Member".

### Workflow Integration

1. User query → SQL results → Recipients auto-extracted
2. User says "send them emails" → Email intent detected
3. LLM generates template → User previews
4. User confirms → Emails sent in batch
5. Progress events streamed to frontend

### Required Environment Variables

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=your-email@gmail.com
```
