from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class IngestRequest(BaseModel):
    text: str
    api_key: str
    input_type: str = "text"   # "text" | "json" | "csv" | "excel"


class Query(BaseModel):
    question: str
    api_key: str


class MultiTenantQuery(BaseModel):
    """Query for multi-tenant RAG system."""
    question: str
    workspace_id: str
    document_id: Optional[str] = None  # Optional filter by document


class Source(BaseModel):
    id: str
    text: str
    score: float
    source: str

class AnswerResponse(BaseModel):
    answer: str
    sources: List[Source]

class EmbedRequest(BaseModel):
    chunks: list[str]
    meeting_id: str

class IngestRequestWithId(BaseModel):
    id: str
    text: str


class IngestDocumentResponse(BaseModel):
    """Response from document ingestion."""
    success: bool
    document_id: str
    workspace_id: str
    vector_count: int
    storage_mode: str
    table_name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    message: str


class DeleteDocumentRequest(BaseModel):
    """Request to delete a document's vectors."""
    workspace_id: str
    document_id: str


class DeleteDocumentResponse(BaseModel):
    """Response from document deletion."""
    success: bool
    message: str


class DeleteWorkspaceRequest(BaseModel):
    """Request to delete an entire workspace."""
    workspace_id: str


class DeleteWorkspaceResponse(BaseModel):
    """Response from workspace deletion."""
    success: bool
    message: str


class DeleteAllDocumentsRequest(BaseModel):
    """Request to delete all documents from a workspace."""
    workspace_id: str


class DeleteAllDocumentsResponse(BaseModel):
    """Response from deleting all documents."""
    success: bool
    message: str


class QueryRequest(BaseModel):
    """Request for the refactored /query endpoint."""
    workspace_id: str
    question: str
    document_id: Optional[str] = None  # Optional filter by document
    stream: bool = False  # Whether to stream the final LLM answer as text/plain
    # SSE streaming fields (used by /query-stream endpoint)
    session_id: Optional[str] = None
    event_callback_url: Optional[str] = None


class QueryResponse(BaseModel):
    """Response from /query endpoint."""
    answer: str
    intent: str
    sources: Optional[List[Dict[str, Any]]] = None
    data: Optional[List[Dict[str, Any]]] = None  # For structured queries
    sql: Optional[str] = None  # SQL query if structured
    needs_input: Optional[bool] = None  # True if clarification needed


class ClearMemoryRequest(BaseModel):
    """Request to clear conversation memory."""
    workspace_id: str