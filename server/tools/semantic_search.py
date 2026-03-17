"""
tools/semantic_search.py

Semantic search tool — wraps the 7-step hybrid RAG pipeline in qdrant_utils.

Pipeline (all inside search_vectors):
  1. Embed query (dense)
  2. Dense search -> top 20 small chunks
  3. BM25 search  -> top 20 small chunks
  4. RRF fusion   -> top 20 deduplicated
  5. Rerank       -> top 5 by cross-encoder
  6. Parent expand -> swap small chunk for parent_text
  7. Dedup parents -> drop repeated parents

This file only handles: embedding the query + calling search_vectors + formatting output.
"""

from typing import List, Dict, Any, Optional
from sentence_transformers import SentenceTransformer

from config import DEVICE
from qdrant_utils import get_collection_name, search_vectors

# Minimum rerank score to consider a result meaningful.
# Below this threshold the retrieval has not found relevant content
# and the caller should return an out-of-scope message instead of
# hallucinating from irrelevant chunks.
RELEVANCE_THRESHOLD = 0.35

# Shared embedding model (lazy-loaded once at first call)
_embedding_model = None


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(
            "sentence-transformers/all-MiniLM-L6-v2",
            device=DEVICE
        )
    return _embedding_model


def semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    document_id: Optional[str] = None,
    use_reranker: bool = True
) -> List[Dict[str, Any]]:
    """
    Run the full hybrid RAG retrieval pipeline for a workspace.

    Embeds the query, calls search_vectors (which runs the full
    7-step pipeline: dense + BM25 + RRF + rerank + parent expand + dedup),
    and returns up to top_k results where each result's 'text' field
    is the parent chunk content ready for the LLM.

    Args:
        workspace_id:  Workspace ID for Qdrant collection lookup
        query:         User query (used for both embedding and BM25/reranker)
        top_k:         Max results to return (default 5)
        document_id:   Optional filter to a specific document
        use_reranker:  Whether to apply cross-encoder reranking (default True)

    Returns:
        List of result dicts, each with:
          text         - parent chunk content (sent to LLM)
          matched_chunk - small chunk that triggered the match
          score        - RRF/vector similarity score
          rerank_score - cross-encoder relevance score (if reranker ran)
          documentId   - source document
    """
    model = _get_embedding_model()
    query_embedding = model.encode([query])[0].tolist()
    collection_name = get_collection_name(workspace_id)

    return search_vectors(
        collection_name=collection_name,
        query_embedding=query_embedding,
        query_text=query,
        top_k=top_k,
        document_id=document_id,
        use_reranker=use_reranker,
    )


def is_retrieval_meaningful(results: List[Dict[str, Any]]) -> bool:
    """
    Return True only if at least one result has a rerank/similarity score
    above RELEVANCE_THRESHOLD.

    Use this BEFORE passing results to the LLM. If False, the query is
    out-of-scope for the uploaded documents and the system should return
    a safe "no information found" message instead of hallucinating.
    """
    if not results:
        return False
    best = max(
        r.get("rerank_score", r.get("score", 0.0))
        for r in results
    )
    return best >= RELEVANCE_THRESHOLD


def format_search_results(results: List[Dict[str, Any]], max_chars: int = 2000) -> str:
    """
    Format parent-chunk results into a context string for the LLM prompt.

    Each result's 'text' is already the parent chunk (expanded in search_vectors).
    Chunks are ordered by rerank_score descending.
    Truncates at max_chars to respect token limits.

    Args:
        results:   Output of semantic_search()
        max_chars: Hard cap on total context length sent to LLM

    Returns:
        Formatted multi-chunk context string
    """
    if not results:
        return "No relevant documents found."

    # Sort by best available score (rerank_score preferred)
    sorted_results = sorted(
        results,
        key=lambda r: r.get("rerank_score", r.get("score", 0.0)),
        reverse=True,
    )

    parts = []
    total = 0

    for i, result in enumerate(sorted_results):
        text = result.get("text", "")   # parent chunk content
        if not text:
            continue

        # Trim individual chunk if it would push over the limit
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining].rstrip() + "..."

        score = result.get("rerank_score", result.get("score", 0.0))
        parts.append(f"[{i + 1}] (score: {score:.2f})\n{text}")
        total += len(text)

    return "\n\n".join(parts)
