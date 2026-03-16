"""
ingestion/helpers.py

Improved ingestion pipeline for all document types.

WHAT CHANGED vs original:
- Hierarchical chunking: splits text into small child chunks + stores parent chunk
  → Retrieve on small chunks (precise match), return parent to LLM (full context)
  → No LLM cost, just storage. Big quality improvement.
- Semantic chunking for plain text: splits on meaning shifts (embedding similarity),
  not just fixed token count. Uses embedding model (cheap, not LLM).
- PDF: switched to pymupdf (fitz) for layout-aware extraction + section-aware chunking
- JSON: dual storage — raw record stored alongside NL summary for embedding
- CSV/Excel: unchanged (SQLite path works well)

USAGE:
    from ingestion.helpers import (
        chunk_text_hierarchical,
        chunk_text_semantic,
        ingest_plain_text,
        ingest_json,
        ingest_pdf,
    )
"""

import re
import io
import json
from typing import List, Tuple, Optional, Any, Dict

import numpy as np

# ---------------------------------------------------------------------------
# HIERARCHICAL CHUNKING
# ---------------------------------------------------------------------------

def chunk_text_hierarchical(
    text: str,
    child_size: int = 400,
    parent_size: int = 1200,
    overlap: int = 100
) -> Tuple[List[str], List[str]]:
    """
    Hierarchical chunking: each child chunk has a corresponding parent chunk.
    
    Why this works:
    - We EMBED and RETRIEVE on small child chunks (precise, specific matching)
    - We RETURN the parent chunk to the LLM (full context, no boundary cutoff)
    
    This is the best chunk strategy for RAG without any LLM cost.
    
    Args:
        text: Input text
        child_size: Character size for child chunks (small, for precise retrieval)
        parent_size: Character size for parent chunks (large, for LLM context)
        overlap: Character overlap between child chunks
        
    Returns:
        Tuple of (child_chunks, parent_chunks) — same length
    """
    if not text or not text.strip():
        return [], []

    # First build parent chunks (large)
    parent_chunks = _fixed_overlap_chunks(text, parent_size, overlap=50)

    child_chunks = []
    parent_for_child = []

    for parent in parent_chunks:
        # Split each parent into smaller child chunks
        children = _fixed_overlap_chunks(parent, child_size, overlap=overlap)
        child_chunks.extend(children)
        # Each child maps to its parent
        parent_for_child.extend([parent] * len(children))

    return child_chunks, parent_for_child


def _fixed_overlap_chunks(text: str, size: int, overlap: int = 100) -> List[str]:
    """Simple fixed-size chunking with overlap."""
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + size, text_len)
        chunks.append(text[start:end])
        start = end - overlap if end < text_len else end
    return chunks


# ---------------------------------------------------------------------------
# SEMANTIC CHUNKING (embedding-based, no LLM)
# ---------------------------------------------------------------------------

def chunk_text_semantic(
    text: str,
    model,                       # SentenceTransformer instance
    similarity_threshold: float = 0.5,
    max_chunk_size: int = 1000,
    min_chunk_size: int = 100,
) -> List[str]:
    """
    Semantic chunking: split on topic/meaning shifts, not fixed size.
    
    Algorithm:
    1. Split text into sentences
    2. Compute sentence embeddings
    3. Find consecutive sentence pairs where cosine similarity DROPS below threshold
       (meaning shift detected)
    4. Split at those points
    
    Much cheaper than LLM-based chunking — only embedding model calls.
    
    Args:
        text: Input text
        model: SentenceTransformer model instance
        similarity_threshold: Below this cosine similarity → new chunk boundary
        max_chunk_size: Hard cap on chunk size in characters
        min_chunk_size: Don't create chunks smaller than this
        
    Returns:
        List of semantically coherent chunks
    """
    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    if len(sentences) <= 3:
        return [text]  # Too short to bother with semantic splitting

    # Embed all sentences at once (batch is efficient)
    embeddings = model.encode(sentences, batch_size=32, show_progress_bar=False)

    # Find split points where similarity drops
    split_indices = [0]
    for i in range(len(embeddings) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        if sim < similarity_threshold:
            split_indices.append(i + 1)
    split_indices.append(len(sentences))

    # Build chunks from split points
    chunks = []
    for i in range(len(split_indices) - 1):
        chunk_sentences = sentences[split_indices[i]:split_indices[i + 1]]
        chunk_text = " ".join(chunk_sentences)

        # Hard size cap: if chunk is too big, split further with fixed overlap
        if len(chunk_text) > max_chunk_size:
            sub_chunks = _fixed_overlap_chunks(chunk_text, max_chunk_size, overlap=100)
            chunks.extend(sub_chunks)
        elif len(chunk_text) >= min_chunk_size:
            chunks.append(chunk_text)
        elif chunks:
            # Too small — append to previous chunk
            chunks[-1] = chunks[-1] + " " + chunk_text
        else:
            chunks.append(chunk_text)

    return [c for c in chunks if c.strip()]


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using regex (no NLTK dependency)."""
    # Split on ., !, ? followed by whitespace + uppercase
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    # Also split on newlines (treat paragraphs as sentence boundaries)
    result = []
    for s in sentences:
        lines = s.split('\n')
        result.extend([l.strip() for l in lines if l.strip()])
    return result


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# PLAIN TEXT INGESTION
# ---------------------------------------------------------------------------

def ingest_plain_text(
    text: str,
    model,
    use_semantic_chunking: bool = True,
) -> Tuple[List[str], List[str], List[List[float]]]:
    """
    Ingest plain text with hierarchical + optional semantic chunking.
    
    Strategy:
    1. If semantic chunking → split on meaning shifts, then hierarchical
    2. If fixed chunking   → directly hierarchical
    
    Returns:
        (child_chunks, parent_chunks, embeddings)
        child_chunks  → embed these and store in Qdrant
        parent_chunks → store as parent_text payload in Qdrant
        embeddings    → dense vectors for child_chunks
    """
    if use_semantic_chunking and len(text) > 500:
        # Semantic split first, then hierarchical within each semantic chunk
        semantic_chunks = chunk_text_semantic(text, model)
        child_chunks = []
        parent_chunks = []
        for sem_chunk in semantic_chunks:
            children, parents = chunk_text_hierarchical(sem_chunk)
            child_chunks.extend(children)
            parent_chunks.extend(parents)
    else:
        child_chunks, parent_chunks = chunk_text_hierarchical(text)

    if not child_chunks:
        return [], [], []

    embeddings = model.encode(child_chunks, batch_size=32, show_progress_bar=False).tolist()
    return child_chunks, parent_chunks, embeddings


# ---------------------------------------------------------------------------
# JSON INGESTION — dual storage (NL summary for embedding + raw record for LLM)
# ---------------------------------------------------------------------------

def ingest_json_for_rag(
    json_data: Any,
    model,
) -> Tuple[List[str], List[str], List[List[float]]]:
    """
    JSON ingestion with dual storage strategy:
    
    For each JSON record:
    - child_chunk  = NL summary ("Raj works in Engineering with salary 95000")
                     → this is what we embed and search against
    - parent_chunk = raw JSON record ({"employee": "Raj", "dept": "Engineering", ...})
                     → this is what we return to the LLM (more precise for reasoning)
    
    This is much better than embedding raw JSON key:value text, because:
    - NL summaries match natural language queries much better
    - LLM gets clean structured data to reason on
    
    Returns:
        (child_chunks, parent_chunks, embeddings)
    """
    records = _normalize_json_to_records(json_data)

    child_chunks = []    # NL summaries for embedding
    parent_chunks = []   # Raw JSON strings for LLM

    for record in records:
        if isinstance(record, dict):
            nl_summary = _dict_to_nl_summary(record)
            raw_json = json.dumps(record, ensure_ascii=False)
        else:
            nl_summary = str(record)
            raw_json = str(record)

        if nl_summary.strip():
            child_chunks.append(nl_summary)
            parent_chunks.append(raw_json)

    if not child_chunks:
        return [], [], []

    embeddings = model.encode(child_chunks, batch_size=32, show_progress_bar=False).tolist()
    return child_chunks, parent_chunks, embeddings


def _dict_to_nl_summary(record: dict, max_fields: int = 15) -> str:
    """
    Convert a dict record to a natural language summary for embedding.
    
    Example:
        {"name": "Raj", "dept": "Engineering", "salary": 95000}
        → "name is Raj, dept is Engineering, salary is 95000"
    """
    parts = []
    for k, v in list(record.items())[:max_fields]:
        if v is not None and str(v).strip():
            # Clean up key name for readability
            key_readable = str(k).replace("_", " ").replace("-", " ").strip()
            parts.append(f"{key_readable} is {v}")
    return ", ".join(parts)


def _normalize_json_to_records(data: Any) -> List[Any]:
    """Flatten JSON to a list of records."""
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        # Check if it's a wrapper with a records key
        for key in ["data", "records", "items", "results", "rows"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    else:
        return [data]


# ---------------------------------------------------------------------------
# PDF INGESTION — pymupdf (layout-aware)
# ---------------------------------------------------------------------------

def ingest_pdf(
    file_bytes: bytes,
    model,
) -> Tuple[List[str], List[str], List[List[float]]]:
    """
    PDF ingestion using pymupdf (fitz) for layout-aware extraction.
    
    Why pymupdf vs pdfplumber:
    - Preserves headings, section structure, and reading order
    - Much faster for large PDFs
    - Better table extraction (as text, not soup)
    
    Strategy:
    - Extract text per page, preserving block structure
    - Group pages into semantic sections (heading-based chunking)
    - Apply hierarchical chunking within each section
    
    Returns:
        (child_chunks, parent_chunks, embeddings)
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        # Fallback to basic extraction if pymupdf not installed
        print("WARNING [ingest_pdf]: pymupdf not installed, using basic extraction. Run: pip install pymupdf")
        return _ingest_pdf_basic(file_bytes, model)

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    sections = []
    current_section = []
    current_heading = ""

    for page_num, page in enumerate(doc):
        blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        
        for block in blocks:
            block_text = block[4].strip()
            if not block_text:
                continue

            # Heuristic: short blocks in UPPERCASE or ending without period = heading
            is_heading = (
                len(block_text) < 100 and
                (block_text.isupper() or 
                 block_text.endswith(':') or
                 (block_text == block_text.title() and len(block_text.split()) <= 8))
            )

            if is_heading and current_section:
                # Save current section
                section_text = current_heading + "\n" + " ".join(current_section)
                sections.append(section_text.strip())
                current_section = []
                current_heading = block_text + "\n"
            elif is_heading:
                current_heading = block_text + "\n"
            else:
                current_section.append(block_text)

    # Don't forget last section
    if current_section:
        section_text = current_heading + " ".join(current_section)
        sections.append(section_text.strip())

    doc.close()

    if not sections:
        return [], [], []

    # Apply hierarchical chunking to each section
    child_chunks = []
    parent_chunks = []

    for section in sections:
        # If section is short enough, use it directly as the parent chunk
        if len(section) < 1500:
            child_sub = _fixed_overlap_chunks(section, 400, overlap=80)
            child_chunks.extend(child_sub)
            parent_chunks.extend([section] * len(child_sub))
        else:
            children, parents = chunk_text_hierarchical(
                section,
                child_size=400,
                parent_size=1200,
                overlap=80
            )
            child_chunks.extend(children)
            parent_chunks.extend(parents)

    if not child_chunks:
        return [], [], []

    embeddings = model.encode(child_chunks, batch_size=32, show_progress_bar=False).tolist()
    return child_chunks, parent_chunks, embeddings


def _ingest_pdf_basic(file_bytes: bytes, model) -> Tuple[List[str], List[str], List[List[float]]]:
    """Basic PDF ingestion fallback using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts)
        return ingest_plain_text(text, model)
    except Exception as e:
        print(f"ERROR [ingest_pdf_basic]: {e}")
        return [], [], []