import time
import psutil
from functools import wraps
import json
import csv
import io
import re
import pandas as pd
import requests
import sqlite3
import os

# Global constants for profiling limits
MAX_PROFILE_ROWS = 100000
MAX_UNIQUE_VALUES = 500
MAX_JSON_DEPTH = 5
MAX_JSON_ARRAY_SAMPLE = 10
MAX_FILE_SIZE_MB = 100  # 100 MB limit
MAX_TEXT_SUMMARY_CHARS = 4000
MIN_TEXT_FOR_SUMMARY = 2000

# LLM configuration
GEMINI_URL = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")

def monitor_performance(func):
    """Decorator to monitor function performance"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = psutil.Process().memory_info().rss / 1024 / 1024  # MB
        
        result = func(*args, **kwargs)
        
        end_time = time.time()
        end_memory = psutil.Process().memory_info().rss / 1024 / 1024  # MB
        
        print(f"Function {func.__name__} took {end_time - start_time:.2f}s")
        print(f"Memory used: {end_memory - start_memory:.2f} MB")
        
        return result
    return wrapper

def check_file_size(file_content: bytes, max_mb: int = MAX_FILE_SIZE_MB):
    """Check if file size exceeds limit."""
    size_mb = len(file_content) / (1024 * 1024)
    if size_mb > max_mb:
        raise ValueError(f"File size {size_mb:.2f} MB exceeds maximum allowed {max_mb} MB")

def generate_summary_with_llm(text: str) -> str | None:
    """Generate summary using LLM via factory pattern. Returns None on failure."""
    from llm.factory import get_llm
    
    prompt = f"""
You are analyzing user-uploaded data.

Provide a concise 3–5 sentence summary describing:
- What this document contains
- Its purpose
- Any obvious patterns or structure

Be neutral and factual.
Do not hallucinate.
Only use the provided text.

Text:
{text}
"""
    try:
        llm = get_llm()
        summary = llm.generate(prompt, max_tokens=500, temperature=0.2)
        return summary.strip() if summary else None
    except Exception as e:
        print(f"LLM request failed: {e}")
    return None

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Chunk text into overlapping segments for embedding."""
    if not text or not text.strip():
        return []
    
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        start = end - overlap if end < text_len else end
    return chunks

def flatten_dict(d, parent_key='', sep='.'):
    """Flatten nested dictionary using dot notation."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    items.extend(flatten_dict(item, f"{new_key}[{i}]", sep=sep).items())
                else:
                    items.append((f"{new_key}[{i}]", item))
        else:
            items.append((new_key, v))
    return dict(items)

def dict_to_semantic_text(d):
    """Convert a flattened dictionary to semantic text."""
    flat = flatten_dict(d)
    lines = [f"{k}: {v}" for k, v in flat.items()]
    return "\n".join(lines)

def normalize_json_to_records(json_data):
    """Convert JSON to list of semantic text blocks (one per record)."""
    if isinstance(json_data, dict):
        return [dict_to_semantic_text(json_data)]
    elif isinstance(json_data, list):
        return [dict_to_semantic_text(item) if isinstance(item, dict) else str(item) for item in json_data]
    else:
        return [str(json_data)]


# =============================================================================
# JSON MODE DETECTION AND PROCESSING
# =============================================================================

def json_to_semantic_sentences(
    data: any,
    parent_context: str = "",
    max_depth: int = 5,
    current_depth: int = 0
) -> list:
    """
    Recursively convert nested JSON into meaningful semantic sentences.
    Preserves parent context hierarchy.
    
    Args:
        data: JSON data (dict, list, or primitive)
        parent_context: Accumulated context from parent keys
        max_depth: Maximum recursion depth
        current_depth: Current recursion level
        
    Returns:
        List of semantic sentences
    """
    sentences = []
    
    if current_depth > max_depth:
        return sentences
    
    if isinstance(data, dict):
        # Check if this is a leaf-like object (mostly primitives)
        primitive_items = {k: v for k, v in data.items() 
                         if not isinstance(v, (dict, list)) or 
                         (isinstance(v, list) and len(v) > 0 and not isinstance(v[0], dict))}
        complex_items = {k: v for k, v in data.items() 
                        if isinstance(v, (dict, list)) and 
                        (isinstance(v, dict) or (isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict)))}
        
        # If mostly primitives, create a single sentence
        if primitive_items and len(primitive_items) >= len(complex_items):
            sentence_parts = []
            if parent_context:
                sentence_parts.append(parent_context)
            
            for k, v in primitive_items.items():
                if v is not None and str(v).strip():
                    # Format nicely based on key name
                    key_clean = k.replace('_', ' ').replace('-', ' ')
                    if isinstance(v, (int, float)):
                        sentence_parts.append(f"{key_clean} is {v}")
                    elif isinstance(v, bool):
                        sentence_parts.append(f"{key_clean} is {'yes' if v else 'no'}")
                    else:
                        sentence_parts.append(f"{key_clean}: {v}")
            
            if sentence_parts:
                sentences.append(". ".join(sentence_parts) + ".")
        
        # Process complex nested items
        for k, v in complex_items.items():
            key_clean = k.replace('_', ' ').replace('-', ' ')
            new_context = f"{parent_context} {key_clean}" if parent_context else key_clean
            sentences.extend(json_to_semantic_sentences(v, new_context.strip(), max_depth, current_depth + 1))
    
    elif isinstance(data, list):
        if not data:
            return sentences
        
        # Check if list contains objects (structured data)
        if isinstance(data[0], dict):
            for i, item in enumerate(data[:100]):  # Limit to 100 items
                sentences.extend(json_to_semantic_sentences(item, parent_context, max_depth, current_depth + 1))
        else:
            # List of primitives
            if parent_context and data:
                values_str = ", ".join(str(v) for v in data[:20])
                sentences.append(f"{parent_context} includes: {values_str}.")
    
    else:
        # Primitive value with context
        if parent_context and data is not None:
            sentences.append(f"{parent_context}: {data}.")
    
    return sentences


def detect_json_schema_pattern(data: any) -> dict:
    """
    Analyze JSON structure to detect schema patterns for mode determination.
    
    Returns:
        Dict with analysis results:
        - has_repeated_objects: bool
        - object_count: int
        - has_numeric_fields: bool
        - has_filterable_fields: bool
        - dominant_schema: list of column names if structured
        - is_relational: bool
    """
    result = {
        "has_repeated_objects": False,
        "object_count": 0,
        "has_numeric_fields": False,
        "has_filterable_fields": False,
        "dominant_schema": [],
        "is_relational": False,
        "array_paths": []
    }
    
    filterable_keywords = ['id', 'status', 'type', 'category', 'city', 'country', 
                          'state', 'code', 'name', 'date', 'year', 'month']
    numeric_keywords = ['price', 'amount', 'quantity', 'count', 'total', 'stock',
                       'salary', 'age', 'score', 'rating', 'cost', 'value']
    
    def analyze_array(arr: list, path: str = ""):
        """Analyze an array for schema patterns."""
        if not arr or not isinstance(arr[0], dict):
            return None
        
        # Get schema from first few objects
        schemas = []
        for item in arr[:10]:
            if isinstance(item, dict):
                schemas.append(set(item.keys()))
        
        if not schemas:
            return None
        
        # Find common keys (intersection)
        common_keys = schemas[0]
        for schema in schemas[1:]:
            common_keys = common_keys.intersection(schema)
        
        if len(common_keys) >= 2 and len(arr) >= 3:
            result["has_repeated_objects"] = True
            result["object_count"] += len(arr)
            result["array_paths"].append(path or "root")
            
            # Check for numeric and filterable fields
            for key in common_keys:
                key_lower = key.lower()
                if any(nk in key_lower for nk in numeric_keywords):
                    result["has_numeric_fields"] = True
                if any(fk in key_lower for fk in filterable_keywords):
                    result["has_filterable_fields"] = True
                # Check actual values
                for item in arr[:5]:
                    val = item.get(key)
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        result["has_numeric_fields"] = True
            
            return list(common_keys)
        return None
    
    def walk_structure(obj, path=""):
        """Walk JSON structure to find arrays."""
        if isinstance(obj, list):
            schema = analyze_array(obj, path)
            if schema and len(schema) > len(result["dominant_schema"]):
                result["dominant_schema"] = schema
            # Also check nested objects in array
            for i, item in enumerate(obj[:5]):
                if isinstance(item, dict):
                    for k, v in item.items():
                        walk_structure(v, f"{path}[].{k}")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                walk_structure(v, new_path)
    
    walk_structure(data)
    
    # Determine if relational
    if (result["has_repeated_objects"] and 
        result["object_count"] >= 5 and 
        len(result["dominant_schema"]) >= 3):
        result["is_relational"] = True
    
    return result


def json_heuristic_mode(data: any, profile: dict) -> str:
    """
    Fallback heuristic to determine JSON storage mode when LLM fails.
    
    Args:
        data: Parsed JSON data
        profile: Profile from profile_json()
        
    Returns:
        'rag', 'structured', or 'hybrid'
    """
    pattern = detect_json_schema_pattern(data)
    
    # Strong indicators for structured
    if (pattern["is_relational"] and 
        pattern["has_numeric_fields"] and 
        pattern["object_count"] >= 10):
        return "structured"
    
    # Indicators for hybrid
    if (pattern["has_repeated_objects"] and 
        pattern["object_count"] >= 5 and
        (pattern["has_numeric_fields"] or pattern["has_filterable_fields"])):
        return "hybrid"
    
    # Default to rag
    return "rag"


def flatten_json_to_rows(
    data: any,
    parent_keys: dict = None
) -> tuple:
    """
    Flatten nested JSON arrays into relational rows for SQLite.
    
    Args:
        data: JSON data
        parent_keys: Inherited keys from parent context
        
    Returns:
        Tuple of (rows: list of dicts, columns: list of column names)
    """
    parent_keys = parent_keys or {}
    
    pattern = detect_json_schema_pattern(data)
    
    if not pattern["dominant_schema"]:
        return [], []
    
    rows = []
    columns = set()
    
    def extract_rows(obj, inherited: dict, path: str = ""):
        """Recursively extract rows from nested structure."""
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            # This is an array of objects - extract as rows
            for item in obj:
                row = {**inherited}
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        # Check if nested array has rows to extract
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            # Recursively extract nested arrays
                            extract_rows(v, {**row, **{kk: vv for kk, vv in item.items() 
                                                       if not isinstance(vv, (dict, list))}}, f"{path}.{k}")
                        elif isinstance(v, dict):
                            # Flatten nested dict into parent row
                            for nested_k, nested_v in v.items():
                                if not isinstance(nested_v, (dict, list)):
                                    col_name = f"{k}_{nested_k}"
                                    row[col_name] = nested_v
                                    columns.add(col_name)
                    else:
                        row[k] = v
                        columns.add(k)
                
                if row and any(v is not None for v in row.values()):
                    rows.append(row)
        
        elif isinstance(obj, dict):
            # Walk into nested structures
            flat_values = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
            for k, v in obj.items():
                if isinstance(v, (list, dict)):
                    extract_rows(v, {**inherited, **flat_values}, f"{path}.{k}" if path else k)
    
    extract_rows(data, parent_keys)
    
    # Ensure consistent column order
    column_list = sorted(list(columns))
    
    # Normalize rows to have all columns
    normalized_rows = []
    for row in rows:
        normalized_row = {col: row.get(col) for col in column_list}
        normalized_rows.append(normalized_row)
    
    return normalized_rows, column_list


def create_sqlite_table_from_json(
    json_data: any,
    db_path: str,
    table_name: str
) -> tuple:
    """
    Create SQLite table from JSON data by flattening nested arrays.
    
    Args:
        json_data: Parsed JSON data
        db_path: Path to SQLite database
        table_name: Name for the table
        
    Returns:
        Tuple of (row_count, column_names)
    """
    rows, columns = flatten_json_to_rows(json_data)
    
    if not rows or not columns:
        raise ValueError("No structured data found in JSON to create table")
    
    # Create DataFrame
    df = pd.DataFrame(rows, columns=columns)
    
    # Clean column names for SQLite
    df.columns = [col.replace(' ', '_').replace('-', '_').replace('.', '_') for col in df.columns]
    columns = list(df.columns)
    
    # Create table
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    
    return len(df), columns


def generate_json_schema_summary(
    columns: list,
    row_count: int,
    table_name: str,
    sample_rows: list = None
) -> str:
    """
    Generate schema summary for JSON-derived SQLite table.
    
    Args:
        columns: List of column names
        row_count: Number of rows
        table_name: Table name
        sample_rows: Optional sample data
        
    Returns:
        Schema summary text
    """
    lines = [
        f"Table: {table_name}",
        "",
        f"This dataset contains {row_count} rows and {len(columns)} columns.",
        "",
        "Columns:"
    ]
    
    for col in columns[:20]:
        lines.append(f"- {col}")
    
    if len(columns) > 20:
        lines.append(f"- ... and {len(columns) - 20} more columns")
    
    if sample_rows:
        lines.append("")
        lines.append("Sample Records:")
        for i, row in enumerate(sample_rows[:3]):
            lines.append(f"Record {i+1}:")
            for k, v in list(row.items())[:8]:
                if v is not None:
                    lines.append(f"  {k}: {v}")
    
    return "\n".join(lines)

def row_to_semantic_text(row: dict) -> str:
    """Convert a single row (dict) to semantic text."""
    lines = ["Record:"]
    for col_name, value in row.items():
        if pd.notna(value):
            lines.append(f"  {col_name} is {value}.")
    return "\n".join(lines)

def normalize_csv_to_records(file_content: bytes):
    """Convert CSV content to generator of semantic text blocks (one per row)."""
    decoded = file_content.decode('utf-8')
    reader = csv.DictReader(io.StringIO(decoded))
    for row in reader:
        yield row_to_semantic_text(row)

def normalize_excel_to_records(file_content: bytes) -> list:
    """Convert Excel content to list of semantic text blocks (one per row)."""
    df = pd.read_excel(io.BytesIO(file_content), engine='openpyxl')
    records = []
    for _, row in df.iterrows():
        records.append(row_to_semantic_text(row.to_dict()))
    return records

def profile_csv(file_content: bytes) -> dict:
    """Profile CSV file for metadata. Optimized for large files using streaming and incremental stats."""
    check_file_size(file_content)
    
    # Try different encodings
    encodings = ['utf-8', 'latin-1', 'cp1252']
    decoded = None
    for enc in encodings:
        try:
            decoded = file_content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("Unable to decode file with supported encodings")
    
    reader = csv.DictReader(io.StringIO(decoded))
    column_names = reader.fieldnames or []
    
    sample_rows = []
    null_counts = {col: 0 for col in column_names}
    unique_values = {col: set() for col in column_names}
    numeric_stats = {col: {'count': 0, 'sum': 0.0, 'min': float('inf'), 'max': float('-inf')} for col in column_names}
    row_count = 0
    truncated = False
    
    for i, row in enumerate(reader):
        if row_count >= MAX_PROFILE_ROWS:
            truncated = True
            break
        row_count += 1
        if i < 5:
            sample_rows.append(row)
        
        for col in column_names:
            val = row.get(col, '').strip()
            if not val:
                null_counts[col] += 1
            else:
                # Limit unique values
                if len(unique_values[col]) < MAX_UNIQUE_VALUES:
                    unique_values[col].add(val)
                # Incremental numeric stats
                try:
                    num = float(val)
                    stats = numeric_stats[col]
                    stats['count'] += 1
                    stats['sum'] += num
                    stats['min'] = min(stats['min'], num)
                    stats['max'] = max(stats['max'], num)
                except ValueError:
                    pass
    
    # Infer column types based on numeric stats
    inferred_types = {}
    for col in column_names:
        stats = numeric_stats[col]
        if stats['count'] > 0:
            # Check if all are integers
            if all(stats['sum'] % 1 == 0 for _ in range(min(10, stats['count']))):  # Rough check
                inferred_types[col] = 'int'
            else:
                inferred_types[col] = 'float'
        else:
            inferred_types[col] = 'string'
    
    # Finalize numeric stats
    final_numeric_stats = {}
    for col in column_names:
        stats = numeric_stats[col]
        if stats['count'] > 0:
            final_numeric_stats[col] = {
                'min': stats['min'] if stats['min'] != float('inf') else None,
                'max': stats['max'] if stats['max'] != float('-inf') else None,
                'mean': stats['sum'] / stats['count']
            }
    
    unique_value_counts = {col: len(unique_values[col]) for col in unique_values}
    
    return {
        'number_of_rows': row_count,
        'number_of_columns': len(column_names),
        'column_names': column_names,
        'inferred_column_types': inferred_types,
        'null_count': null_counts,
        'sample_rows': sample_rows,
        'numeric_stats': final_numeric_stats,
        'unique_value_count': unique_value_counts,
        'truncated': truncated
    }

def profile_excel(file_content: bytes) -> dict:
    """Profile Excel file for metadata. Memory-safe with row limits."""
    check_file_size(file_content)
    
    df = pd.read_excel(io.BytesIO(file_content), engine='openpyxl', nrows=MAX_PROFILE_ROWS)
    
    column_names = df.columns.tolist()
    number_of_rows = len(df)
    number_of_columns = len(column_names)
    truncated = number_of_rows == MAX_PROFILE_ROWS  # If we hit the limit, it was truncated
    
    # Infer types
    inferred_types = {}
    for col in column_names:
        dtype = df[col].dtype
        if 'int' in str(dtype):
            inferred_types[col] = 'int'
        elif 'float' in str(dtype):
            inferred_types[col] = 'float'
        elif 'datetime' in str(dtype):
            inferred_types[col] = 'datetime'
        else:
            inferred_types[col] = 'string'
    
    # Null counts
    null_counts = df.isnull().sum().to_dict()
    
    # Sample rows (first 5)
    sample_rows = df.head(5).to_dict('records')
    
    # Numeric stats (incremental, but since df is small, use pandas)
    numeric_stats = {}
    for col in column_names:
        if inferred_types[col] in ['int', 'float']:
            vals = df[col].dropna()
            if not vals.empty:
                numeric_stats[col] = {
                    'min': vals.min(),
                    'max': vals.max(),
                    'mean': vals.mean()
                }
    
    # Unique value counts (for low cardinality)
    unique_value_counts = {}
    for col in column_names:
        unique_count = df[col].nunique()
        if unique_count < MAX_UNIQUE_VALUES:
            unique_value_counts[col] = unique_count
    
    # Clean up memory
    del df
    
    return {
        'number_of_rows': number_of_rows,
        'number_of_columns': number_of_columns,
        'column_names': column_names,
        'inferred_column_types': inferred_types,
        'null_count': null_counts,
        'sample_rows': sample_rows,
        'numeric_stats': numeric_stats,
        'unique_value_count': unique_value_counts,
        'truncated': truncated
    }

def profile_json(text: str) -> dict:
    """Profile JSON data for metadata with depth and size limits."""
    if len(text) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise ValueError(f"JSON size exceeds maximum allowed {MAX_FILE_SIZE_MB} MB")
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {'error': 'Invalid JSON'}
    
    def get_all_paths(obj, path='', depth=0):
        """Recursively get all key paths with depth limit."""
        if depth > MAX_JSON_DEPTH:
            return set()
        paths = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                paths.add(new_path)
                paths.update(get_all_paths(v, new_path, depth + 1))
        elif isinstance(obj, list):
            # Sample only first MAX_JSON_ARRAY_SAMPLE elements
            for i, item in enumerate(obj[:MAX_JSON_ARRAY_SAMPLE]):
                new_path = f"{path}[{i}]"
                paths.update(get_all_paths(item, new_path, depth + 1))
        return paths
    
    def get_depth(obj, depth=0):
        """Get maximum depth of nested structure with limit."""
        if depth > MAX_JSON_DEPTH:
            return depth
        if isinstance(obj, dict):
            return 1 + max((get_depth(v, depth + 1) for v in obj.values()), default=0)
        elif isinstance(obj, list):
            return 1 + max((get_depth(item, depth + 1) for item in obj[:MAX_JSON_ARRAY_SAMPLE]), default=0)
        return 0
    
    def estimate_records(obj):
        """Estimate total records (dicts or list items) with sampling."""
        if isinstance(obj, dict):
            return 1
        elif isinstance(obj, list):
            # Estimate based on sample
            sample_size = min(len(obj), MAX_JSON_ARRAY_SAMPLE)
            if sample_size == 0:
                return 0
            avg_complexity = sum(1 for item in obj[:sample_size] if isinstance(item, (dict, list)))
            return int(len(obj) * (avg_complexity / sample_size)) if avg_complexity > 0 else len(obj)
        return 1
    
    top_level_keys = list(data.keys()) if isinstance(data, dict) else []
    nested_key_paths = list(get_all_paths(data))
    depth = get_depth(data)
    estimated_total_records = estimate_records(data)
    sample_records = data[:5] if isinstance(data, list) else [data]
    
    return {
        'top_level_keys': top_level_keys,
        'nested_key_paths': nested_key_paths,
        'depth': depth,
        'estimated_total_records': estimated_total_records,
        'sample_records': sample_records
    }

def create_sqlite_table(file_content: bytes, input_type: str, db_path: str, table_name: str):
    """Create SQLite table from file content."""
    if input_type == "csv":
        df = pd.read_csv(io.BytesIO(file_content))
    elif input_type == "excel":
        df = pd.read_excel(io.BytesIO(file_content))
    else:
        raise ValueError("Unsupported input type for SQLite")
    
    # Clean column names for SQLite
    df.columns = [col.replace(' ', '_').replace('-', '_') for col in df.columns]
    
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    
    return len(df)


def drop_sqlite_table(db_path: str, table_name: str) -> bool:
    """
    Drop a table from SQLite database.
    
    Args:
        db_path: Path to the SQLite database
        table_name: Name of the table to drop
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if not os.path.exists(db_path):
            print(f"DEBUG: SQLite DB not found at {db_path}")
            return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cursor.fetchone():
            print(f"DEBUG: Table {table_name} not found in database")
            conn.close()
            return False
        
        # Drop the table
        cursor.execute(f"DROP TABLE IF EXISTS \"{table_name}\"")
        conn.commit()
        conn.close()
        
        print(f"DEBUG: Successfully dropped table {table_name} from {db_path}")
        return True
    except Exception as e:
        print(f"Error dropping table {table_name}: {str(e)}")
        return False


def get_sqlite_tables(db_path: str) -> list:
    """
    Get list of all tables in SQLite database.
    
    Args:
        db_path: Path to the SQLite database
        
    Returns:
        List of table names
    """
    try:
        if not os.path.exists(db_path):
            return []
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return tables
    except Exception as e:
        print(f"Error getting tables from {db_path}: {str(e)}")
        return []


def delete_sqlite_database(db_path: str) -> bool:
    """
    Delete the SQLite database file.
    
    Args:
        db_path: Path to the SQLite database
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"DEBUG: Deleted SQLite database at {db_path}")
            return True
        return False
    except Exception as e:
        print(f"Error deleting SQLite database {db_path}: {str(e)}")
        return False


def determine_storage_mode(metadata: dict) -> dict:
    # Extract basic metrics
    num_rows = metadata.get('number_of_rows', 0)
    num_cols = metadata.get('number_of_columns', 0)
    column_names = metadata.get('column_names', [])
    inferred_types = metadata.get('inferred_column_types', {})
    unique_counts = metadata.get('unique_value_count', {})
    
    # Signal 1: row_score
    row_score = min(num_rows / 1000, 1.0) if num_rows >= 0 else 0.0
    
    # Signal 2: column_score
    column_score = min(num_cols / 10, 1.0) if num_cols >= 0 else 0.0
    
    # Signal 3: numeric_density
    numeric_columns = sum(1 for typ in inferred_types.values() if typ in ['int', 'float'])
    numeric_density = numeric_columns / num_cols if num_cols > 0 else 0.0
    
    # Signal 4: repetition_score
    if num_cols > 0 and num_rows > 0:
        repetition_values = []
        for col in column_names:
            unique_count = unique_counts.get(col, 0)
            repetition = 1 - (unique_count / num_rows)
            repetition_values.append(max(0, min(1, repetition)))  # Clamp to [0,1]
        repetition_score = sum(repetition_values) / len(repetition_values)
    else:
        repetition_score = 0.0
    
    # Signal 5: timestamp_bonus
    timestamp_keywords = ["date", "time", "created", "updated", "timestamp"]
    timestamp_bonus = 0.1 if any(
        any(keyword.lower() in col_name.lower() for keyword in timestamp_keywords)
        for col_name in column_names
    ) else 0.0
    
    # Compute final structured_score
    structured_score = (
        0.3 * row_score +
        0.2 * column_score +
        0.3 * numeric_density +
        0.1 * repetition_score +
        0.1 * timestamp_bonus
    )
    
    # Clamp to [0, 1]
    structured_score = max(0.0, min(1.0, structured_score))
    
    # Check if this is tabular data (CSV/Excel) - these should always be at least hybrid
    is_tabular = (
        len(column_names) > 0 and 
        len(inferred_types) > 0 and 
        num_rows > 0
    )
    
    # Decision logic - lowered thresholds for better hybrid coverage
    if structured_score >= 0.4:
        storage_mode = "sqlite"
    elif structured_score >= 0.15 or is_tabular:
        # Force hybrid for any tabular data (CSV/Excel) regardless of score
        storage_mode = "hybrid"
    else:
        storage_mode = "rag"
    
    return {
        "structured_score": round(structured_score, 4),
        "storage_mode": storage_mode,
        "is_tabular": is_tabular
    }

def profile_text(text: str) -> dict:
    """Profile raw text for metadata with LLM-based summary."""
    total_characters = len(text)
    words = text.split()
    total_words = len(words)
    estimated_tokens = int(total_words * 0.75)  # Rough estimate
    
    # Generate LLM summary
    truncated = False
    if total_characters < MIN_TEXT_FOR_SUMMARY:
        text_for_summary = text
    else:
        text_for_summary = text[:MAX_TEXT_SUMMARY_CHARS]
        truncated = True
    
    summary = generate_summary_with_llm(text_for_summary)
    if summary is not None and truncated:
        summary += "\n\nNote: Text was truncated for summary generation."
    
    return {
        'total_characters': total_characters,
        'total_words': total_words,
        'estimated_tokens': estimated_tokens,
        'summary': summary,
        'truncated': truncated
    }

def generate_schema_summary(profile: dict, table_name: str) -> str:
    """Generate a semantic summary document for SQLite table schema."""
    lines = []
    
    # Table header
    lines.append(f"Table: {table_name}")
    lines.append("")
    
    # Basic info
    num_rows = profile.get('number_of_rows', 0)
    num_cols = profile.get('number_of_columns', 0)
    lines.append(f"This dataset contains {num_rows} rows and {num_cols} columns.")
    lines.append("")
    
    # Columns section
    lines.append("Columns:")
    column_names = profile.get('column_names', [])
    inferred_types = profile.get('inferred_column_types', {})
    for col in column_names:
        col_type = inferred_types.get(col, 'unknown')
        lines.append(f"- {col} ({col_type})")
    lines.append("")
    
    # Numeric insights
    numeric_stats = profile.get('numeric_stats', {})
    if numeric_stats:
        lines.append("Numeric Insights:")
        for col, stats in list(numeric_stats.items())[:5]:
            if 'min' in stats and 'max' in stats and 'mean' in stats:
                min_val = stats['min']
                max_val = stats['max']
                mean_val = stats['mean']
                lines.append(f"- {col} ranges from {min_val} to {max_val} with mean {mean_val}")
        lines.append("")
    
    # Categorical columns (low cardinality)
    unique_counts = profile.get('unique_value_count', {})
    categorical_cols = []
    for col, count in unique_counts.items():
        if count is not None and count < 50:  # Low cardinality threshold
            categorical_cols.append(f"- {col} has {count} unique values")
    
    if categorical_cols:
        lines.append("Categorical columns:")
        lines.extend(categorical_cols)
        lines.append("")
    
    # Sample rows (first 3-5)
    sample_rows = profile.get('sample_rows', [])
    if sample_rows:
        lines.append("Sample Records:")
        for i, row in enumerate(sample_rows[:5]):  # Limit to 5 samples
            lines.append(f"Record {i+1}:")
            for key, value in row.items():
                if pd.notna(value):  # Only include non-null values
                    lines.append(f"  {key}: {value}")
            lines.append("")
    
    # Join all lines
    summary = "\n".join(lines)
    
    # Add a brief description at the end using LLM if available
    description_prompt = f"""
Based on this table schema, provide a 1-2 sentence description of what this dataset represents:

Table: {table_name}
Columns: {', '.join(column_names)}
Row count: {num_rows}

Description:
"""
    
    llm_description = generate_summary_with_llm(description_prompt)
    if llm_description:
        summary += f"\n\n{llm_description}"
    
    return summary


# =============================================================================
# LLM METADATA GENERATION (Single Call)
# =============================================================================

def generate_metadata_with_llm(
    content_preview: str,
    file_type: str,
    profile: dict,
    table_name: str = None
) -> dict:
    """
    Generate metadata (summary, description, keywords) with a SINGLE LLM call.
    
    Args:
        content_preview: First 2000 chars of content for text/json/pdf
        file_type: Type of file (csv, excel, json, text, pdf)
        profile: Profile data from profiling functions
        table_name: Table name for structured data
        
    Returns:
        Dict with summary, description, keywords (or fallback values)
    """
    from llm.factory import get_llm
    
    # Default fallback response
    fallback = {
        "summary": f"Document of type {file_type}",
        "description": f"A {file_type} file uploaded for analysis",
        "keywords": [file_type, "data", "document"]
    }
    
    try:
        # Build prompt based on file type
        if file_type in ["csv", "excel"]:
            # Structured data prompt - include profile metadata
            column_names = profile.get('column_names', [])
            num_rows = profile.get('number_of_rows', 0)
            num_cols = profile.get('number_of_columns', 0)
            inferred_types = profile.get('inferred_column_types', {})
            sample_rows = profile.get('sample_rows', [])
            numeric_stats = profile.get('numeric_stats', {})
            
            # Build column info string
            columns_info = []
            for col in column_names[:20]:  # Limit to 20 columns
                col_type = inferred_types.get(col, 'unknown')
                columns_info.append(f"- {col} ({col_type})")
            columns_str = "\n".join(columns_info)
            
            # Build sample rows string
            sample_str = ""
            if sample_rows:
                sample_str = "\n\nSample Data (first 3 rows):\n"
                for i, row in enumerate(sample_rows[:3]):
                    row_str = ", ".join([f"{k}: {v}" for k, v in list(row.items())[:8]])
                    sample_str += f"Row {i+1}: {row_str}\n"
            
            # Build numeric stats string
            stats_str = ""
            if numeric_stats:
                stats_str = "\n\nNumeric Column Statistics:\n"
                for col, stats in list(numeric_stats.items())[:5]:
                    stats_str += f"- {col}: min={stats.get('min')}, max={stats.get('max')}, mean={stats.get('mean', 0):.2f}\n"
            
            effective_table_name = table_name or f"data_{file_type}"
            
            prompt = f"""Analyze this {file_type.upper()} dataset. Table: {effective_table_name}, Rows: {num_rows}, Columns: {num_cols}

Columns: {columns_str}
{sample_str}
Return ONLY this JSON (keep summary under 100 words):
{{"summary": "brief 2-sentence summary", "description": "one sentence", "keywords": ["k1", "k2", "k3", "k4", "k5"]}}"""

        elif file_type == "json":
            # JSON-specific prompt - use content_preview (actual JSON) as primary context
            top_level_keys = profile.get('top_level_keys', [])
            nested_paths = profile.get('nested_key_paths', [])
            estimated_records = profile.get('estimated_total_records', 0)
            sample_records = profile.get('sample_records', [])
            depth = profile.get('depth', 1)
            
            # Use actual JSON content if available
            json_content = ""
            if content_preview:
                json_content = content_preview[:1500]
            elif sample_records:
                try:
                    json_content = json.dumps(sample_records[0], indent=2, default=str)[:1500]
                except:
                    pass
            
            keys_info = f"Top-level keys: {', '.join(top_level_keys[:10])}" if top_level_keys else ""
            nested_info = f"Nested paths: {', '.join(nested_paths[:10])}" if nested_paths else ""
            
            prompt = f"""Analyze this JSON data structure and determine the best storage mode:
{json_content}

{keys_info}
{nested_info}
Nesting depth: {depth}, Estimated records: {estimated_records}

STORAGE MODE RULES:
- "structured": Use when data is tabular (arrays of objects with consistent fields that could form a SQL table), has numeric/aggregatable fields, users will query by filtering/comparing values. Examples: product lists, user records, transactions.
- "rag": Use when data is narrative, descriptive text, configuration, or deeply nested without clear tabular pattern. Examples: articles, documentation, configs, API responses with mixed structure.
- "hybrid": Use when data has BOTH tabular elements (for SQL) AND rich text content (for semantic search). Examples: products with descriptions, employees with bios.

Based on the JSON structure, return ONLY valid JSON (no markdown):
{{"summary": "2 sentences describing what this data contains", "description": "1 sentence about data type", "keywords": ["relevant", "keywords", "here"], "data_mode": "rag|structured|hybrid", "data_mode_confidence": 0.0-1.0}}"""""

        else:
            # Text-based content prompt (text, pdf)
            content_sample = content_preview[:1500] if content_preview else ""
            
            prompt = f"""Analyze this {file_type} document:
{content_sample}

Return ONLY this JSON (keep summary under 100 words):
{{"summary": "brief 2-sentence summary", "description": "one sentence", "keywords": ["k1", "k2", "k3", "k4", "k5"]}}"""

        # Get LLM instance
        llm = get_llm(temperature=0.1, max_tokens=800)
        
        print(f"DEBUG: Calling LLM for metadata generation (single call) - file_type: {file_type}")
        
        # Call LLM
        response = llm.generate(prompt)
        
        if not response:
            print("WARNING: Empty LLM response, using fallback")
            return fallback
        
        # Clean response (remove markdown code blocks if present)
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        # Try to fix truncated JSON
        if not response.endswith("}"):
            # Try to find the last complete field and close the JSON
            if '"keywords"' in response:
                # Try to extract keywords array if present
                kw_match = response.find('"keywords"')
                if kw_match > 0:
                    # Check if we have a partial array
                    bracket_start = response.find('[', kw_match)
                    if bracket_start > 0:
                        # Find the last complete keyword
                        last_quote = response.rfind('"')
                        if last_quote > bracket_start:
                            response = response[:last_quote+1] + "]}"
            elif '"description"' in response:
                last_quote = response.rfind('"')
                if last_quote > 0:
                    response = response[:last_quote+1] + ', "keywords": []}'
            elif '"summary"' in response:
                last_quote = response.rfind('"')
                if last_quote > 0:
                    response = response[:last_quote+1] + ', "description": "", "keywords": []}'
        
        # Parse JSON
        try:
            result = json.loads(response)
            
            # Validate required fields
            if not isinstance(result.get("summary"), str):
                result["summary"] = fallback["summary"]
            if not isinstance(result.get("description"), str):
                result["description"] = fallback["description"]
            if not isinstance(result.get("keywords"), list):
                result["keywords"] = fallback["keywords"]
            else:
                # Ensure keywords are strings
                result["keywords"] = [str(k) for k in result["keywords"][:10]]
            
            # Handle JSON-specific data_mode field
            if file_type == "json":
                valid_modes = ["rag", "structured", "hybrid"]
                data_mode = result.get("data_mode", "rag")
                if data_mode not in valid_modes:
                    data_mode = "rag"
                result["data_mode"] = data_mode
                
                confidence = result.get("data_mode_confidence", 0.5)
                if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                    confidence = 0.5
                result["data_mode_confidence"] = float(confidence)
            
            print(f"DEBUG: LLM metadata generated successfully: {len(result.get('keywords', []))} keywords")
            return result
            
        except json.JSONDecodeError as e:
            print(f"WARNING: Failed to parse LLM JSON response: {e}")
            print(f"DEBUG: Raw response: {response[:300]}...")
            
            # Fallback: Try regex extraction
            extracted = {}
            
            # Try to extract summary
            summary_match = re.search(r'"summary"\s*:\s*"([^"]*(?:\\"[^"]*)*)"', response)
            if summary_match:
                extracted["summary"] = summary_match.group(1).replace('\\"', '"')
            
            # Try to extract description
            desc_match = re.search(r'"description"\s*:\s*"([^"]*(?:\\"[^"]*)*)"', response)
            if desc_match:
                extracted["description"] = desc_match.group(1).replace('\\"', '"')
            
            # Try to extract keywords
            kw_match = re.search(r'"keywords"\s*:\s*\[(.*?)\]', response, re.DOTALL)
            if kw_match:
                kw_str = kw_match.group(1)
                keywords = re.findall(r'"([^"]+)"', kw_str)
                if keywords:
                    extracted["keywords"] = keywords[:10]
            
            # For JSON, try to extract data_mode
            if file_type == "json":
                mode_match = re.search(r'"data_mode"\s*:\s*"(rag|structured|hybrid)"', response)
                if mode_match:
                    extracted["data_mode"] = mode_match.group(1)
                conf_match = re.search(r'"data_mode_confidence"\s*:\s*([0-9.]+)', response)
                if conf_match:
                    try:
                        extracted["data_mode_confidence"] = float(conf_match.group(1))
                    except:
                        pass
            
            if extracted:
                print(f"DEBUG: Extracted partial metadata via regex: {list(extracted.keys())}")
                result = {
                    "summary": extracted.get("summary", fallback["summary"]),
                    "description": extracted.get("description", fallback["description"]),
                    "keywords": extracted.get("keywords", fallback["keywords"])
                }
                # Add JSON-specific fields
                if file_type == "json":
                    result["data_mode"] = extracted.get("data_mode", "rag")
                    result["data_mode_confidence"] = extracted.get("data_mode_confidence", 0.5)
                return result
            
            # Return fallback with data_mode for JSON
            if file_type == "json":
                fallback["data_mode"] = "rag"
                fallback["data_mode_confidence"] = 0.5
            return fallback
            
    except Exception as e:
        print(f"WARNING: LLM metadata generation failed: {e}")
        import traceback
        traceback.print_exc()
        # Return fallback with data_mode for JSON
        if file_type == "json":
            fallback["data_mode"] = "rag"
            fallback["data_mode_confidence"] = 0.5
        return fallback


def build_metadata_for_query(
    llm_metadata: dict,
    storage_mode: str,
    profile: dict,
    table_name: str = None
) -> dict:
    """
    Build the lightweight metadataForQuery object from LLM response.
    
    Args:
        llm_metadata: Dict from generate_metadata_with_llm
        storage_mode: 'rag', 'sqlite', or 'hybrid'
        profile: Profile data from profiling functions
        table_name: Table name for structured data
        
    Returns:
        metadataForQuery dict
    """
    if storage_mode in ["sqlite", "hybrid"]:
        # Structured data type
        return {
            "type": "structured",
            "summary": llm_metadata.get("summary", ""),
            "description": llm_metadata.get("description", ""),
            "tableName": table_name,
            "columns": profile.get("column_names", []),
            "keywords": llm_metadata.get("keywords", []),
            "storageMode": storage_mode
        }
    else:
        # RAG type (text, json, pdf)
        return {
            "type": "rag",
            "summary": llm_metadata.get("summary", ""),
            "description": llm_metadata.get("description", ""),
            "keywords": llm_metadata.get("keywords", []),
            "storageMode": storage_mode
        }