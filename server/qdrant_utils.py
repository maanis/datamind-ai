"""
Qdrant utilities for multi-tenant RAG platform.

UPGRADED (vs original):
- Hybrid retrieval: dense vectors + BM25 sparse vectors (relevance-first, not just similarity)
- Flashrank reranker: local cross-encoder reranking after retrieval (no API cost, ~50ms)
- Hierarchical chunking: stores parent_text alongside child chunk for richer LLM context
- RRF fusion: Reciprocal Rank Fusion to merge dense + sparse results

Why hybrid?
  Dense-only = finds "topically close" chunks, misses exact keyword matches
  BM25-only  = finds exact terms, misses semantic meaning
  Hybrid+RRF = best of both; reranker then picks the most RELEVANT ones
"""

import uuid
import re
import math
from collections import Counter
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import (
    Distance, VectorParams, SparseVectorParams,
    PointStruct, Filter, FieldCondition, MatchValue,
    UpdateStatus, SparseVector
)

from config import QDRANT_URL

# ---------------------------------------------------------------------------
# Qdrant client (singleton)
# ---------------------------------------------------------------------------
qdrant_client = QdrantClient(url=QDRANT_URL)


# ===========================================================================
# BM25 ENCODER — pure Python, no external API
# ===========================================================================

class BM25Encoder:
    """
    Lightweight BM25 encoder that produces sparse vectors for Qdrant.
    
    BM25 assigns higher weight to:
    - Terms that appear frequently in THIS chunk (TF)
    - Terms that appear rarely across ALL chunks (IDF)
    
    This catches "exact keyword" queries that dense semantic search misses.
    E.g. "revenue Q3 2023" → BM25 will find chunks with exact "Q3 2023"
         even if dense search returns generic "financial performance" chunks.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1       # TF saturation: prevents single term dominating
        self.b = b         # Length normalization: 0=no norm, 1=full norm
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[int, float] = {}
        self._next_idx = 0

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\b[a-z0-9]{2,}\b', text.lower())

    def _get_or_add(self, term: str) -> int:
        if term not in self.vocab:
            self.vocab[term] = self._next_idx
            self._next_idx += 1
        return self.vocab[term]

    def fit(self, corpus: List[str]):
        """Fit BM25 on a list of text chunks to compute IDF weights."""
        N = len(corpus)
        df: Dict[int, int] = {}
        for doc in corpus:
            for token in set(self._tokenize(doc)):
                idx = self._get_or_add(token)
                df[idx] = df.get(idx, 0) + 1
        for idx, freq in df.items():
            self.idf[idx] = math.log((N - freq + 0.5) / (freq + 0.5) + 1)

    def encode_document(self, text: str, avg_doc_len: float = 500.0) -> SparseVector:
        """Encode a chunk into a BM25 sparse vector for indexing."""
        tokens = self._tokenize(text)
        doc_len = len(tokens)
        tf = Counter(tokens)
        indices, values = [], []
        for term, count in tf.items():
            idx = self._get_or_add(term)
            idf = self.idf.get(idx, math.log(2))
            tf_score = (count * (self.k1 + 1)) / (
                count + self.k1 * (1 - self.b + self.b * doc_len / max(avg_doc_len, 1))
            )
            score = idf * tf_score
            if score > 0:
                indices.append(idx)
                values.append(float(score))
        return SparseVector(indices=indices, values=values)

    def encode_query(self, text: str) -> SparseVector:
        """Encode a query into a BM25 sparse vector for searching."""
        indices, values = [], []
        for term in set(self._tokenize(text)):
            idx = self._get_or_add(term)
            idf = self.idf.get(idx, math.log(2))
            if idf > 0:
                indices.append(idx)
                values.append(float(idf))
        return SparseVector(indices=indices, values=values)


# Per-collection BM25 encoder cache (in-memory)
_bm25_encoders: Dict[str, BM25Encoder] = {}


def get_bm25_encoder(collection_name: str) -> BM25Encoder:
    if collection_name not in _bm25_encoders:
        _bm25_encoders[collection_name] = BM25Encoder()
    return _bm25_encoders[collection_name]


# ===========================================================================
# FLASHRANK RERANKER — local cross-encoder, no API cost
# ===========================================================================

_reranker = None


def _get_reranker():
    """
    Lazy-load Flashrank reranker.
    
    Cross-encoders score query+chunk TOGETHER (not independently like vectors),
    so they catch relevance that vector similarity misses.
    ~50-100ms per rerank call — much faster than an LLM rerank.
    """
    global _reranker
    if _reranker is None:
        try:
            from flashrank import Ranker
            _reranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir="/tmp/flashrank")
            print("DEBUG [reranker]: Flashrank reranker loaded")
        except ImportError:
            print("WARNING [reranker]: flashrank not installed. Run: pip install flashrank")
            _reranker = False
    return _reranker


def rerank_results(query: str, results: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Rerank candidates using local Flashrank cross-encoder.
    Falls back to original order if flashrank is unavailable.
    """
    if not results:
        return results
    ranker = _get_reranker()
    if not ranker:
        return results[:top_k]
    try:
        from flashrank import RerankRequest
        passages = [{"id": i, "text": r.get("text", "")} for i, r in enumerate(results)]
        reranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        out = []
        for item in reranked[:top_k]:
            original = results[item["id"]]
            original["rerank_score"] = float(item["score"])
            out.append(original)
        return out
    except Exception as e:
        print(f"WARNING [reranker]: Reranking failed ({e}), returning original order")
        return results[:top_k]


# ===========================================================================
# COLLECTION MANAGEMENT
# ===========================================================================

def get_collection_name(workspace_id: str) -> str:
    """Generate Qdrant collection name for a workspace: ws_<workspaceId>"""
    return f"ws_{workspace_id}"


def create_collection_if_not_exists(collection_name: str, vector_size: int) -> bool:
    """
    Create a Qdrant collection with BOTH dense and sparse vector configs.
    
    Dense  → semantic similarity (cosine)
    Sparse → BM25 keyword matching
    
    Returns True if created, False if already existed.
    """
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name in existing:
            return False

        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config={"dense": VectorParams(size=vector_size, distance=Distance.COSINE)},
            sparse_vectors_config={"sparse": SparseVectorParams()}
        )

        # Payload indexes for fast workspace/document filtering
        qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="documentId",
            field_schema=models.PayloadSchemaType.KEYWORD
        )
        qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="workspaceId",
            field_schema=models.PayloadSchemaType.KEYWORD
        )

        print(f"DEBUG [create_collection]: Created '{collection_name}' with dense+sparse vectors")
        return True

    except Exception as e:
        print(f"Error creating collection {collection_name}: {str(e)}")
        raise


# ===========================================================================
# VECTOR UPSERT
# ===========================================================================

def upsert_vectors(
    collection_name: str,
    embeddings: List[List[float]],
    chunks: List[str],
    workspace_id: str,
    document_id: str,
    vector_size: int,
    batch_size: int = 100,
    parent_chunks: Optional[List[str]] = None   # NEW: hierarchical chunking support
) -> int:
    """
    Upsert vectors with BOTH dense embeddings and BM25 sparse vectors.
    
    Each point stores:
    - dense vector: semantic embedding for semantic search
    - sparse vector: BM25 weights for keyword search
    - text: the child chunk (small, precise — used for matching)
    - parent_text: the parent chunk (larger context — returned to LLM)
    
    The hierarchical chunking (parent_chunks) improves answer quality:
    We RETRIEVE on small chunks (precise match) but RETURN the parent
    chunk to the LLM (full context). No extra LLM cost.
    
    Args:
        parent_chunks: If provided, these are the larger parent chunks.
                      Must be same length as chunks, or None.
    """
    create_collection_if_not_exists(collection_name, vector_size)

    # Fit BM25 on this document's chunks
    encoder = get_bm25_encoder(collection_name)
    encoder.fit(chunks)
    avg_doc_len = sum(len(c.split()) for c in chunks) / max(len(chunks), 1)

    points = []
    for i, (embedding, chunk) in enumerate(zip(embeddings, chunks)):
        sparse_vec = encoder.encode_document(chunk, avg_doc_len=avg_doc_len)
        parent_text = parent_chunks[i] if parent_chunks and i < len(parent_chunks) else chunk

        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": embedding, "sparse": sparse_vec},
            payload={
                "workspaceId": workspace_id,
                "documentId": document_id,
                "chunkIndex": i,
                "text": chunk,              # Small chunk for matching
                "parent_text": parent_text, # Parent chunk for LLM context
            }
        ))

    total_upserted = 0
    for i in range(0, len(points), batch_size):
        result = qdrant_client.upsert(
            collection_name=collection_name,
            points=points[i:i + batch_size],
            wait=True
        )
        if result.status == UpdateStatus.COMPLETED:
            total_upserted += len(points[i:i + batch_size])

    return total_upserted


# ===========================================================================
# HYBRID SEARCH
# ===========================================================================

def search_vectors(
    collection_name: str,
    query_embedding: List[float],
    query_text: str = "",
    top_k: int = 5,
    document_id: Optional[str] = None,
    use_reranker: bool = True
) -> List[Dict[str, Any]]:
    """
    Full RAG retrieval pipeline (7 steps, optimised for accuracy + low latency):

    Step 1 - Dense search:   embed query -> top 20 small chunks (semantic)
    Step 2 - BM25 search:    tokenize query -> top 20 small chunks (keyword)
    Step 3 - RRF fusion:     merge + deduplicate -> top 20 ranked small chunks
    Step 4 - Rerank:         cross-encoder over 20 small chunks -> top 5 by relevance
    Step 5 - Parent expand:  swap each top-5 small chunk for its parent_text (~800 tok)
    Step 6 - Dedup parents:  drop duplicate parents (same parent shared by 2 small chunks)
    Step 7 - Return:         up to top_k unique parent chunks sent to LLM

    Why rerank on small chunks then expand to parent?
      Small chunks (200 tok) give the reranker a precise match signal.
      Parent chunks (800 tok) give the LLM full surrounding context.
      No extra LLM call needed — purely embedding + cross-encoder work.
    """
    # guard: collection must exist
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name not in existing:
            return []
    except Exception as e:
        print(f"Error checking collections: {e}")
        return []

    query_filter = None
    if document_id:
        query_filter = Filter(
            must=[FieldCondition(key="documentId", match=MatchValue(value=document_id))]
        )

    # Always fetch 20 small-chunk candidates so reranker has enough signal
    CANDIDATE_K = 20

    # Step 1: Dense semantic search
    try:
        dense_hits = qdrant_client.query_points(
            collection_name=collection_name,
            query=query_embedding,
            using="dense",
            query_filter=query_filter,
            limit=CANDIDATE_K,
            with_payload=True
        ).points
    except Exception as e:
        print(f"WARNING [search]: dense search failed ({e})")
        dense_hits = []

    # Step 2: BM25 sparse keyword search
    sparse_hits = []
    if query_text:
        try:
            encoder = get_bm25_encoder(collection_name)
            sparse_vec = encoder.encode_query(query_text)
            if sparse_vec.indices:
                sparse_hits = qdrant_client.query_points(
                    collection_name=collection_name,
                    query=sparse_vec,
                    using="sparse",
                    query_filter=query_filter,
                    limit=CANDIDATE_K,
                    with_payload=True
                ).points
        except Exception as e:
            print(f"DEBUG [search]: BM25 search failed ({e}), dense only")

    # Step 3: RRF fusion -> top 20 small chunks (deduped by id)
    fused_hits = _reciprocal_rank_fusion(dense_hits, sparse_hits)[:CANDIDATE_K]

    if not fused_hits:
        return []

    # Materialise small-chunk records — keep small chunk text for reranking
    small_chunks: List[Dict[str, Any]] = []
    for hit in fused_hits:
        payload = hit.payload if hasattr(hit, "payload") else {}
        small_chunks.append({
            "id":          str(hit.id),
            "text":        payload.get("text", ""),           # small chunk -> for reranker
            "parent_text": payload.get("parent_text") or payload.get("text", ""),
            "score":       getattr(hit, "score", 0.0),
            "documentId":  payload.get("documentId", ""),
            "chunkIndex":  payload.get("chunkIndex", 0),
        })

    # Step 4: Rerank small chunks -> top 5
    if use_reranker and query_text:
        top5 = _rerank_small_chunks(query_text, small_chunks, top_k=top_k)
    else:
        top5 = small_chunks[:top_k]

    # Steps 5 & 6: Expand to parent chunks + deduplicate
    return _expand_and_deduplicate(top5)


def _rerank_small_chunks(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Run Flashrank cross-encoder on the 20 small chunks.
    Returns top_k scored by true relevance (not vector similarity).
    Reranker sees small chunk text for precise match signal.
    Falls back to RRF order if flashrank unavailable.
    """
    ranker = _get_reranker()
    if not ranker:
        return candidates[:top_k]
    try:
        from flashrank import RerankRequest
        # Feed small chunk text — precise, avoids noise from long parent text
        passages = [{"id": i, "text": c["text"]} for i, c in enumerate(candidates)]
        reranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        out = []
        for item in reranked[:top_k]:
            orig = candidates[item["id"]].copy()
            orig["rerank_score"] = float(item["score"])
            out.append(orig)
        return out
    except Exception as e:
        print(f"WARNING [reranker]: failed ({e}), using RRF order")
        return candidates[:top_k]


def _expand_and_deduplicate(
    top5: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Expand each top-5 small chunk to its parent_text.
    Deduplicate: if two small chunks share the same parent_text,
    only keep the one with the higher rerank_score.
    The 'text' field in the returned records is the parent chunk
    (what gets sent to the LLM for context).
    """
    seen: Dict[str, float] = {}    # parent fingerprint -> best score so far
    results: List[Dict[str, Any]] = []

    for chunk in top5:
        parent = chunk.get("parent_text") or chunk.get("text", "")
        fingerprint = parent[:120].strip()   # first 120 chars as cheap key
        score = chunk.get("rerank_score", chunk.get("score", 0.0))

        if fingerprint not in seen:
            seen[fingerprint] = score
            results.append({
                "id":           chunk["id"],
                "text":         parent,             # parent chunk -> LLM context
                "matched_chunk": chunk["text"],     # small chunk that triggered match
                "score":        chunk["score"],
                "rerank_score": score,
                "documentId":   chunk["documentId"],
                "chunkIndex":   chunk["chunkIndex"],
            })
        else:
            # Same parent already in results — update score if this one is higher
            if score > seen[fingerprint]:
                seen[fingerprint] = score
                for r in results:
                    if r["text"][:120].strip() == fingerprint:
                        r["rerank_score"] = score
                        break

    return results


def _reciprocal_rank_fusion(dense_hits: list, sparse_hits: list, k: int = 60) -> list:
    """
    Merge dense + sparse results using Reciprocal Rank Fusion.
    
    score(doc) = 1/(k + rank_in_dense) + 1/(k + rank_in_sparse)
    
    Documents appearing in BOTH lists get a higher combined score —
    they are both semantically AND keyword relevant.
    """
    scores: Dict[str, float] = {}
    hit_map: Dict[str, Any] = {}

    for rank, hit in enumerate(dense_hits):
        hid = str(hit.id)
        scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank + 1)
        hit_map[hid] = hit

    for rank, hit in enumerate(sparse_hits):
        hid = str(hit.id)
        scores[hid] = scores.get(hid, 0.0) + 1.0 / (k + rank + 1)
        hit_map[hid] = hit

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [hit_map[i] for i in sorted_ids]


# ===========================================================================
# DELETION HELPERS (unchanged from original)
# ===========================================================================

def delete_document_vectors(collection_name: str, document_id: str) -> bool:
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name not in existing:
            return True
        result = qdrant_client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="documentId", match=MatchValue(value=document_id))]
            ),
            wait=True
        )
        return result.status == UpdateStatus.COMPLETED
    except Exception as e:
        print(f"Error deleting vectors for document {document_id}: {str(e)}")
        return False


def delete_collection(collection_name: str) -> bool:
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name not in existing:
            return True
        qdrant_client.delete_collection(collection_name=collection_name)
        return True
    except Exception as e:
        print(f"Error deleting collection {collection_name}: {str(e)}")
        return False


def get_collection_info(collection_name: str) -> Optional[Dict[str, Any]]:
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name not in existing:
            return None
        info = qdrant_client.get_collection(collection_name=collection_name)
        return {
            "name": collection_name,
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "status": str(info.status)
        }
    except Exception as e:
        print(f"Error getting collection info: {str(e)}")
        return None


def count_document_vectors(collection_name: str, document_id: str) -> int:
    try:
        existing = [c.name for c in qdrant_client.get_collections().collections]
        if collection_name not in existing:
            return 0
        result = qdrant_client.count(
            collection_name=collection_name,
            count_filter=Filter(
                must=[FieldCondition(key="documentId", match=MatchValue(value=document_id))]
            ),
            exact=True
        )
        return result.count
    except Exception as e:
        print(f"Error counting vectors: {str(e)}")
        return 0
