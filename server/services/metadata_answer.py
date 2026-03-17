"""
services/metadata_answer.py

Metadata-first query answering.

Before hitting SQL or RAG, check if the query can be answered
directly from the MongoDB document metadata (stats stored at ingestion time).

MongoDB document stores:
  metadata.number_of_rows
  metadata.number_of_columns
  metadata.column_names
  metadata.inferred_column_types
  metadata.numeric_stats.{col}.{min,max,mean}
  metadata.unique_value_count.{col}
  metadata.sample_rows

metadataForQuery stores:
  summary, description, keywords, columns, type, tableName

This avoids spinning up SQLite/Qdrant for trivial stat questions.

Examples that SHORT-CIRCUIT:
  "how many rows in employees.csv?"      → metadata.number_of_rows
  "what columns does the orders table have?" → metadata.column_names
  "what's the max total_amount?"          → metadata.numeric_stats.total_amount.max
  "what cities are in the data?"          → metadata.unique_value_count.city (if <=15 unique)
  "what's the average delivery time?"     → metadata.numeric_stats.delivery_time_minutes.mean
  "what info do you have?"               → workspace overview from all document summaries
  "what can you help me with?"           → workspace overview from all document summaries

Returns None if metadata is NOT sufficient to answer (caller should proceed normally).
"""

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# PATTERN MATCHERS
# ---------------------------------------------------------------------------

_ROW_COUNT_RE = re.compile(
    r"\b(how many|number of|count of|total)\s+(rows?|records?|entries?|items?)\b",
    re.IGNORECASE
)

_COL_LIST_RE = re.compile(
    r"\b(what|which|list|show|give)\s+(columns?|fields?|attributes?|headers?)\b",
    re.IGNORECASE
)

_SCHEMA_RE = re.compile(
    r"\b(schema|structure|describe|what.+look.+like|overview)\b",
    re.IGNORECASE
)

_NUMERIC_STAT_RE = re.compile(
    r"\b(max(imum)?|min(imum)?|avg|average|mean|total|sum)\b",
    re.IGNORECASE
)

_UNIQUE_VALUES_RE = re.compile(
    r"\b(unique|distinct|different|all|what|which)\s+(values?|options?|types?|categories?|cities?|statuses?)\b",
    re.IGNORECASE
)

_DATASET_INFO_RE = re.compile(
    r"\b(what.*(data|dataset|file|table)|tell me about|describe|summarize)\b",
    re.IGNORECASE
)

# Catches: "what info do you have", "what can you help with", "what do you know",
# "tell me about my data", "what topics", "overview", "what's in my workspace" etc.
_WORKSPACE_OVERVIEW_RE = re.compile(
    r"("
    r"what.{0,30}(info|information|data|know|help|assist|topics?|documents?|files?|content)|"
    r"tell\s+me\s+(about|what).{0,30}(data|documents?|files?|workspace|uploaded)|"
    r"what.{0,20}(uploaded|available|workspace)|"
    r"(overview|summary)\s+of.{0,20}(data|documents?|workspace)|"
    r"what\s+can\s+(you|i).{0,20}(ask|help|do|find|query)|"
    r"what.{0,10}(in|inside).{0,20}(workspace|data|documents?)|"
    r"help\s+me\s+(understand|explore|navigate)|"
    r"show\s+me\s+what.{0,20}(have|got|available)|"
    r"what\s+topics|"
    r"what\s+do\s+you\s+(have|know)|"
    r"what\s+information"
    r")",
    re.IGNORECASE
)


def _find_numeric_stat(question: str, numeric_stats: Dict[str, Any]) -> Optional[str]:
    """Try to find a specific numeric stat (min/max/mean) for a column mentioned in question."""
    if not numeric_stats:
        return None

    q = question.lower()

    stat_type = None
    if re.search(r"\b(max(imum)?|highest|largest|most|top)\b", q):
        stat_type = "max"
    elif re.search(r"\b(min(imum)?|lowest|smallest|least|bottom)\b", q):
        stat_type = "min"
    elif re.search(r"\b(avg|average|mean)\b", q):
        stat_type = "mean"
    elif re.search(r"\b(total|sum)\b", q):
        stat_type = "total"

    if not stat_type:
        return None

    for col_name, stats in numeric_stats.items():
        col_words = col_name.lower().replace("_", " ").split()
        if any(word in q for word in col_words if len(word) > 2):
            if stat_type == "total" and "mean" in stats:
                val = stats.get("mean", stats.get("max"))
                if val is not None:
                    return f"The average (mean) **{col_name.replace('_', ' ')}** is **{val:,.2f}**."
            elif stat_type in stats:
                val = stats[stat_type]
                if val is not None:
                    label_map = {"max": "maximum", "min": "minimum", "mean": "average"}
                    label = label_map.get(stat_type, stat_type)
                    return f"The {label} **{col_name.replace('_', ' ')}** is **{val:,.2f}**."

    return None


def _find_unique_values(question: str, unique_value_count: Dict[str, int], col_values: Optional[Dict[str, List]] = None) -> Optional[str]:
    """Return unique value info for a column if mentioned in the question."""
    if not unique_value_count:
        return None

    q = question.lower()

    for col_name, count in unique_value_count.items():
        col_words = col_name.lower().replace("_", " ").split()
        if any(word in q for word in col_words if len(word) > 2):
            if col_values and col_name in col_values:
                vals = col_values[col_name]
                if len(vals) <= 20:
                    formatted = ", ".join(f"**{v}**" for v in vals)
                    return f"The **{col_name.replace('_', ' ')}** column has {count} unique value(s): {formatted}."
            return f"The **{col_name.replace('_', ' ')}** column has **{count}** unique value(s)."

    return None


# ---------------------------------------------------------------------------
# WORKSPACE OVERVIEW BUILDER  (multi-document, no LLM)
# ---------------------------------------------------------------------------

def _build_workspace_overview(metadata_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Build a rich workspace overview from all document summaries.

    This is what powers the "what info do you have?" response.
    No LLM call — purely from MongoDB-stored summaries and keywords.

    Strategy per document:
      - PDF/TXT  → use summary + keywords as bullet points
      - CSV/XLSX → use summary + column names + row count
    """
    if not metadata_list:
        return None

    doc_sections = []

    for entry in metadata_list:
        mfq = entry if not entry.get("metadataForQuery") else entry.get("metadataForQuery", entry)
        meta = entry.get("metadata") or {}

        file_name = entry.get("fileName") or entry.get("file_name", "Unknown file")
        storage_mode = entry.get("storageMode") or entry.get("type", "rag")
        summary = mfq.get("summary") or meta.get("summary", "")
        keywords = mfq.get("keywords") or []
        columns = mfq.get("columns") or meta.get("column_names") or []
        num_rows = meta.get("number_of_rows")

        if not summary and not keywords and not columns:
            continue

        lines = [f"**{file_name}**"]

        if storage_mode in ("sqlite", "hybrid") and columns:
            # Structured data — show schema info
            if num_rows:
                lines.append(f"  *{num_rows:,} rows × {len(columns)} columns*")
            if summary:
                lines.append(f"  {summary}")
            # Show columns as bullet points (cap at 8)
            shown_cols = columns[:8]
            for col in shown_cols:
                lines.append(f"  * `{col}`")
            if len(columns) > 8:
                lines.append(f"  * *...and {len(columns) - 8} more columns*")
        else:
            # Unstructured (PDF/TXT/JSON) — use summary + keyword bullets
            if summary:
                lines.append(f"  {summary}")
            if keywords:
                # Group keywords into topic bullets (every 3-4 keywords = one bullet)
                kw_bullets = _keywords_to_bullets(keywords, file_name, summary)
                for b in kw_bullets:
                    lines.append(f"  * {b}")

        doc_sections.append("\n".join(lines))

    if not doc_sections:
        return None

    intro = "I can help you with information from the following documents in your workspace:\n\n"
    numbered = "\n\n".join(
        f"{i + 1}. {section}" for i, section in enumerate(doc_sections)
    )
    outro = "\n\nFeel free to ask specific questions about any of these, or I can go deeper into any topic."

    return intro + numbered + outro


def _keywords_to_bullets(keywords: List[str], file_name: str, summary: str) -> List[str]:
    """
    Convert a flat keyword list into readable topic bullets.

    Groups semantically related keywords together so the output reads
    like the "DataMind AI" example rather than a raw keyword dump.
    """
    if not keywords:
        return []

    # Try to infer topic groupings from the keywords
    # Simple approach: group every 3-4 into a bullet summarizing them
    bullets = []
    chunk_size = 3
    for i in range(0, min(len(keywords), 15), chunk_size):
        group = keywords[i:i + chunk_size]
        # Format: "Topic A, Topic B, and Topic C"
        if len(group) == 1:
            bullets.append(group[0])
        elif len(group) == 2:
            bullets.append(f"{group[0]} and {group[1]}")
        else:
            bullets.append(f"{', '.join(group[:-1])}, and {group[-1]}")

    return bullets[:5]  # cap at 5 bullets per document


# ---------------------------------------------------------------------------
# MAIN PUBLIC FUNCTION
# ---------------------------------------------------------------------------

def try_metadata_answer(
    question: str,
    metadata_list: List[Dict[str, Any]]
) -> Optional[str]:
    """
    Try to answer the question purely from stored document metadata.

    Args:
        question: User's question
        metadata_list: List of metadataForQuery entries (with full metadata attached)

    Returns:
        Answer string if metadata is sufficient, None otherwise.
    """
    if not metadata_list or not question:
        return None

    q = question.strip().lower()

    # ------------------------------------------------------------------
    # PATTERN 0: Workspace overview — "what info do you have?",
    #            "what can you help with?", "what topics do you cover?" etc.
    #
    # STRICT rules — must explicitly reference the workspace/data/documents,
    # NOT just any short question. This prevents "in detail?", "tell me more",
    # "what is pricing?" from being intercepted here instead of going to RAG.
    # ------------------------------------------------------------------

    # Explicit follow-up phrases that should NEVER trigger overview
    _FOLLOWUP_SKIP = re.compile(
        r"\b(in detail|more detail|tell me more|elaborate|explain more|"
        r"go deeper|expand|what (is|are|does|do)|how (does|do|is|are)|"
        r"pricing|features?|architecture|stack|tech|use case|example|"
        r"compare|difference|versus|vs\b)\b",
        re.IGNORECASE
    )

    is_explicit_workspace_query = bool(_WORKSPACE_OVERVIEW_RE.search(q))
    is_followup_or_specific = bool(_FOLLOWUP_SKIP.search(q))

    if is_explicit_workspace_query and not is_followup_or_specific:
        overview = _build_workspace_overview(metadata_list)
        if overview:
            return overview

    # Find the best matching document for single-doc queries
    target_meta = None
    for entry in metadata_list:
        if entry.get("metadata") or entry.get("metadataForQuery"):
            target_meta = entry
            break
    if not target_meta:
        target_meta = metadata_list[0]

    meta = target_meta.get("metadata") or {}
    mfq = target_meta.get("metadataForQuery") or target_meta

    num_rows = meta.get("number_of_rows")
    num_cols = meta.get("number_of_columns")
    column_names = meta.get("column_names") or mfq.get("columns", [])
    numeric_stats = meta.get("numeric_stats", {})
    unique_value_count = meta.get("unique_value_count", {})
    file_name = target_meta.get("fileName") or target_meta.get("file_name", "this dataset")
    summary = mfq.get("summary") or meta.get("summary", "")

    # ------------------------------------------------------------------
    # PATTERN 1: How many rows / records?
    # ------------------------------------------------------------------
    if _ROW_COUNT_RE.search(question):
        if num_rows is not None:
            return (
                f"The dataset **{file_name}** contains **{num_rows:,} row(s)**"
                + (f" across **{num_cols}** column(s)." if num_cols else ".")
            )

    # ------------------------------------------------------------------
    # PATTERN 2: What columns / fields?
    # ------------------------------------------------------------------
    if _COL_LIST_RE.search(question):
        if column_names:
            formatted = ", ".join(f"`{c}`" for c in column_names)
            return (
                f"**{file_name}** has **{len(column_names)} column(s)**:\n\n{formatted}"
            )

    # ------------------------------------------------------------------
    # PATTERN 3: Schema / structure / describe / overview
    # ------------------------------------------------------------------
    if _SCHEMA_RE.search(question) and not re.search(r"\b(why|how|explain|what does)\b", q):
        if column_names and num_rows:
            cols_str = ", ".join(f"`{c}`" for c in column_names)
            answer = f"**{file_name}** — {num_rows:,} rows, {num_cols or len(column_names)} columns.\n\n**Columns:** {cols_str}"
            if summary:
                answer += f"\n\n**Summary:** {summary}"
            return answer

    # ------------------------------------------------------------------
    # PATTERN 4: Numeric stats (max/min/avg of a column)
    # ------------------------------------------------------------------
    if _NUMERIC_STAT_RE.search(question):
        stat_answer = _find_numeric_stat(question, numeric_stats)
        if stat_answer:
            return stat_answer

    # ------------------------------------------------------------------
    # PATTERN 5: Unique / distinct values of a column
    # ------------------------------------------------------------------
    if _UNIQUE_VALUES_RE.search(question):
        unique_answer = _find_unique_values(question, unique_value_count)
        if unique_answer:
            return unique_answer

    # ------------------------------------------------------------------
    # PATTERN 6: General "what is this dataset / tell me about your data"
    # ------------------------------------------------------------------
    if _DATASET_INFO_RE.search(question) and len(q.split()) <= 10:
        if summary:
            answer = f"**{file_name}**\n\n{summary}"
            if num_rows:
                answer += f"\n\n📊 **{num_rows:,} rows** × **{num_cols or '?'} columns**"
            if column_names:
                cols_str = ", ".join(f"`{c}`" for c in column_names[:10])
                if len(column_names) > 10:
                    cols_str += f" *(+{len(column_names) - 10} more)*"
                answer += f"\n**Columns:** {cols_str}"
            return answer

    return None