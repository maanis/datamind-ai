"""
SQL tool for structured data queries.
Generates and executes SQL on SQLite tables.
"""

import os
import re
import sqlite3
from typing import List, Dict, Any, Optional, Tuple

from config import SQLITE_DIR
from llm.factory import get_llm


def get_workspace_tables(workspace_id: str) -> List[Dict[str, Any]]:
    """
    Get all tables and their schemas for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        List of table metadata with columns and row counts
    """
    workspace_dir = os.path.join(SQLITE_DIR, workspace_id)
    
    if not os.path.exists(workspace_dir):
        return []
    
    tables = []
    
    # Find all .db files in workspace directory
    for filename in os.listdir(workspace_dir):
        if filename.endswith(".db"):
            db_path = os.path.join(workspace_dir, filename)
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Get all tables in this database
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [row[0] for row in cursor.fetchall()]
                
                for table_name in table_names:
                    # Get columns
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [row[1] for row in cursor.fetchall()]
                    
                    # Get row count
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    row_count = cursor.fetchone()[0]
                    
                    # Get sample values for context
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
                    sample_rows = cursor.fetchall()
                    
                    tables.append({
                        "name": table_name,
                        "db_path": db_path,
                        "columns": columns,
                        "row_count": row_count,
                        "sample_rows": sample_rows
                    })
                
                conn.close()
            except Exception as e:
                print(f"Error reading database {db_path}: {str(e)}")
                continue
    
    return tables


def generate_sql(
    question: str,
    tables_metadata: List[Dict[str, Any]],
    llm_provider: Optional[str] = None
) -> Tuple[str, str]:
    """
    Generate SQL query from natural language question.
    
    Args:
        question: User's question
        tables_metadata: Available tables with schema info
        llm_provider: Override LLM provider
        
    Returns:
        Tuple of (sql_query, target_table_db_path)
    """
    if not tables_metadata:
        raise ValueError("No tables available for SQL generation")
    
    # Build schema description
    schema_desc = ""
    table_db_map = {}
    
    for table in tables_metadata:
        table_name = table["name"]
        columns = table["columns"]
        row_count = table.get("row_count", "unknown")
        sample_rows = table.get("sample_rows", [])
        
        table_db_map[table_name] = table["db_path"]
        
        schema_desc += f"\nTable: {table_name}\n"
        schema_desc += f"Columns: {', '.join(columns)}\n"
        schema_desc += f"Row count: {row_count}\n"
        
        if sample_rows:
            schema_desc += f"Sample data (first row): {dict(zip(columns, sample_rows[0]))}\n"
    
    prompt = f"""Generate a SQLite query for this question.

Database Schema:
{schema_desc}

Question: {question}

Rules:
1. Generate ONLY a SELECT query (no INSERT, UPDATE, DELETE, DROP)
2. Use exact column names from the schema
3. Keep query simple and efficient
4. Use appropriate aggregations (COUNT, SUM, AVG, etc.) when needed

Respond with JSON:
{{"sql": "<query>", "table": "<main_table_name>", "explanation": "<brief>"}}"""

    llm = get_llm(provider=llm_provider, max_tokens=300)
    result = llm.generate_json(prompt)
    
    sql = result.get("sql", "")
    table = result.get("table", "")
    
    # Validate SELECT only
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    
    dangerous_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            raise ValueError(f"Dangerous SQL keyword detected: {keyword}")
    
    db_path = table_db_map.get(table)
    if not db_path:
        # Try to find the table in any db
        for t_name, t_path in table_db_map.items():
            if t_name.lower() in sql.lower():
                db_path = t_path
                break
    
    if not db_path:
        db_path = list(table_db_map.values())[0] if table_db_map else None
    
    return sql, db_path


def execute_sql_query(
    sql: str,
    db_path: str,
    max_rows: int = 100
) -> Dict[str, Any]:
    """
    Execute SQL query on SQLite database.
    
    Args:
        sql: SQL query to execute
        db_path: Path to SQLite database
        max_rows: Maximum rows to return
        
    Returns:
        Dictionary with columns, rows, and row_count
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    
    # Additional validation
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Add LIMIT if not present
        if "LIMIT" not in sql_upper:
            sql = f"{sql.rstrip(';')} LIMIT {max_rows}"
        
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        # Get column names
        columns = [description[0] for description in cursor.description] if cursor.description else []
        
        # Convert rows to list of dicts
        result_rows = [dict(row) for row in rows]
        
        conn.close()
        
        return {
            "columns": columns,
            "rows": result_rows,
            "row_count": len(result_rows),
            "sql": sql
        }
        
    except sqlite3.Error as e:
        raise Exception(f"SQL execution error: {str(e)}")


def format_sql_results(results: Dict[str, Any], max_rows: int = 20) -> str:
    """
    Format SQL results into readable string for LLM.
    
    Args:
        results: Results from execute_sql_query
        max_rows: Maximum rows to include in output
        
    Returns:
        Formatted results string
    """
    if not results.get("rows"):
        return "Query returned no results."
    
    columns = results.get("columns", [])
    rows = results.get("rows", [])[:max_rows]
    total = results.get("row_count", 0)
    
    # Build table string
    output = f"Query: {results.get('sql', 'N/A')}\n"
    output += f"Results ({total} rows):\n\n"
    
    # Simple formatting
    for i, row in enumerate(rows):
        row_str = " | ".join(f"{k}: {v}" for k, v in row.items())
        output += f"{i+1}. {row_str}\n"
    
    if total > max_rows:
        output += f"\n... and {total - max_rows} more rows"
    
    return output
