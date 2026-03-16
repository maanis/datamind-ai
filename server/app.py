"""
app.py — FastAPI entry point

ENDPOINTS:
  POST /query            — Standard query (blocking or text/plain stream)
  POST /query-stream     — Streaming query with real-time pipeline events via SSE
  POST /ingest-document  — Multi-format document ingestion
  DELETE /delete-document
  DELETE /delete-workspace
  DELETE /delete-all-documents
  POST /clear-memory
  GET  /health

LLM TOGGLE:
  Set LLM_PROVIDER env var: "gemini" (production) or "ollama" (dev)
  Default is "gemini". See config.py.
"""

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from typing import Optional

from config import DEVICE
from models import (
    Query, IngestRequest, MultiTenantQuery, DeleteDocumentRequest,
    DeleteWorkspaceRequest, DeleteAllDocumentsRequest,
    QueryRequest, QueryResponse, ClearMemoryRequest
)
from routes import (
    get_answer_multi_tenant, ingest_document, delete_document,
    delete_workspace, delete_all_documents_from_workspace,
    clear_memory
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Starting FastAPI server on device: {DEVICE}")
    
    # Initialize Mem0 at startup to avoid delay on first query
    from services.memory_service import init_mem0
    init_mem0()
    
    yield
    print("Shutting down FastAPI server")


app = FastAPI(
    title="RAG Infra API",
    description="Multi-tenant RAG platform — workspace-isolated ingestion and query",
    lifespan=lifespan
)


# =============================================================================
# QUERY ENDPOINTS
# =============================================================================

@app.post("/query")
def query_endpoint(req: QueryRequest):
    """
    Standard query endpoint.
    
    - stream=false: returns JSON { answer, intent, sources?, sql?, data? }
    - stream=true:  returns text/plain streamed response (final answer only)
    
    For real-time pipeline events (what step is running), use /query-stream instead.
    """
    from services.query_service import QueryService
    
    service = QueryService()
    result = service.handle_query(
        workspace_id=req.workspace_id,
        question=req.question,
        document_id=req.document_id,
        stream=req.stream
    )

    if req.stream and hasattr(result, '__iter__') and not isinstance(result, dict):
        return StreamingResponse(result, media_type="text/plain")

    return result


@app.post("/query-stream")
def query_stream_endpoint(req: QueryRequest):
    """
    Streaming query with real-time pipeline events.
    
    Called by Node.js streamController after creating a StreamEvent session.
    Python writes pipeline events back to Node.js via event_callback_url.
    
    Body (extra fields vs /query):
      session_id: str         — SSE session ID (from Node.js)
      event_callback_url: str — Node.js endpoint to POST events to
    
    Returns: { answer, intent, ... } when complete
    """
    from services.query_service import QueryService

    service = QueryService()
    result = service.handle_query(
        workspace_id=req.workspace_id,
        question=req.question,
        document_id=req.document_id,
        stream=False,                       # Streaming events via callback, not text/plain
        session_id=getattr(req, 'session_id', None),
        event_callback_url=getattr(req, 'event_callback_url', None)
    )
    return result


# =============================================================================
# INGESTION ENDPOINTS
# =============================================================================

@app.post("/ingest-document")
async def ingest_document_endpoint(
    file: Optional[UploadFile] = File(None),
    workspaceId: str = Form(...),
    documentId: str = Form(...),
    raw_text: Optional[str] = Form(None),
    raw_json: Optional[str] = Form(None)
):
    """
    Multi-tenant document ingestion.
    Supports: CSV, Excel, JSON, PDF, TXT (file upload) or raw text/JSON.
    Called by Node.js after creating the Document record in MongoDB.
    """
    return await ingest_document(
        workspace_id=workspaceId,
        document_id=documentId,
        file=file,
        raw_text=raw_text,
        raw_json=raw_json
    )


# =============================================================================
# DELETION ENDPOINTS
# =============================================================================

@app.delete("/delete-document")
def delete_document_endpoint(req: DeleteDocumentRequest):
    """Delete a document's vectors from Qdrant (and SQLite table if exists)."""
    return delete_document(req)


@app.delete("/delete-workspace")
def delete_workspace_endpoint(req: DeleteWorkspaceRequest):
    """Delete entire workspace collection from Qdrant + SQLite database."""
    return delete_workspace(req.workspace_id)


@app.delete("/delete-all-documents")
def delete_all_documents_endpoint(req: DeleteAllDocumentsRequest):
    """Delete all documents from a workspace (vectors + SQLite tables)."""
    return delete_all_documents_from_workspace(req.workspace_id)


# =============================================================================
# MEMORY ENDPOINTS
# =============================================================================

@app.post("/clear-memory")
def clear_memory_endpoint(req: ClearMemoryRequest):
    """Clear conversation memory for a workspace."""
    return clear_memory(req.workspace_id)


# =============================================================================
# UTILITY ENDPOINTS
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check."""
    return {"status": "healthy", "device": DEVICE}


# =============================================================================
# LEGACY ENDPOINTS (kept for backward compatibility)
# =============================================================================

@app.post("/get-answer-v2")
def answer_v2_endpoint(query: MultiTenantQuery):
    """Legacy multi-tenant answer endpoint. Use /query instead."""
    return get_answer_multi_tenant(query)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
