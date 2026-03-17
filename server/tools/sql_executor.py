"""
Secure SQL executor for structured data queries.
Validates and executes SQL on SQLite tables with strict security controls.
"""

import os
import re
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from config import SQLITE_DIR
from mongo_utils import get_workspace_sqlite_path


# Dangerous SQL keywords that must be blocked
BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", 
    "TRUNCATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA",
    "EXEC", "EXECUTE", "GRANT", "REVOKE", "SAVEPOINT", "ROLLBACK"
]

# Maximum rows to return
MAX_ROWS = 500

# Maximum execution time (seconds)
MAX_EXECUTION_TIME = 30


@dataclass
class SQLResult:
    """Result of SQL execution."""
    success: bool
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    truncated: bool
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "error": self.error
        }


class SQLValidationError(Exception):
    """Raised when SQL validation fails."""
    pass


def validate_sql(sql: str) -> Tuple[bool, str]:
    """
    Validate SQL query for security.
    
    Args:
        sql: SQL query string
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sql or not sql.strip():
        return False, "Empty SQL query"
    
    sql_clean = sql.strip()
    sql_upper = sql_clean.upper()
    
    # Must start with SELECT
    if not sql_upper.startswith("SELECT"):
        return False, "Only SELECT queries are allowed"
    
    # Check for blocked keywords
    for keyword in BLOCKED_KEYWORDS:
        # Use word boundary matching to avoid false positives
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, sql_upper):
            return False, f"Blocked SQL keyword detected: {keyword}"
    
    # Check for multiple statements (semicolon not at end)
    sql_no_comments = re.sub(r'--[^\n]*', '', sql_clean)  # Remove -- comments
    sql_no_comments = re.sub(r'/\*.*?\*/', '', sql_no_comments, flags=re.DOTALL)  # Remove /* */ comments
    
    # Remove trailing semicolon for check
    sql_check = sql_no_comments.rstrip(';').strip()
    if ';' in sql_check:
        return False, "Multiple SQL statements not allowed"
    
    # Check for system tables
    if "sqlite_" in sql_upper or "sys." in sql_upper:
        return False, "Access to system tables not allowed"
    
    # Check for dangerous functions
    dangerous_functions = ["load_extension", "writefile", "readfile", "fts3_tokenizer"]
    for func in dangerous_functions:
        if func in sql_upper.lower():
            return False, f"Dangerous function not allowed: {func}"
    
    return True, ""


def find_table_db_path(workspace_id: str, table_name: str) -> Optional[str]:
    """
    Find the database path for a table.
    
    New flow: sqlite_data/{userId}/data_{workspaceId}.db
    Uses workspace.sqliteDbPath from MongoDB as the primary source.
    
    Args:
        workspace_id: Workspace ID
        table_name: Table name to find
        
    Returns:
        Database path or None
    """
    # Primary: Get the stored path from MongoDB workspace
    db_path = get_workspace_sqlite_path(workspace_id)
    
    if db_path and os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return db_path
        except Exception as e:
            print(f"DEBUG: Error checking DB at {db_path}: {e}")
    
    # Fallback: Scan sqlite_data directory for any matching table (backward compatibility)
    if os.path.exists(SQLITE_DIR):
        for user_dir in os.listdir(SQLITE_DIR):
            user_path = os.path.join(SQLITE_DIR, user_dir)
            if not os.path.isdir(user_path):
                continue
            for filename in os.listdir(user_path):
                if filename.endswith(".db"):
                    fallback_db_path = os.path.join(user_path, filename)
                    try:
                        conn = sqlite3.connect(fallback_db_path)
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                        result = cursor.fetchone()
                        conn.close()
                        
                        if result:
                            print(f"DEBUG: Found table {table_name} in fallback path {fallback_db_path}")
                            return fallback_db_path
                    except Exception:
                        continue
    
    print(f"DEBUG: Table {table_name} not found in workspace {workspace_id}")
    return None


def extract_table_from_sql(sql: str) -> Optional[str]:
    """
    Extract table name from SQL query.
    
    Args:
        sql: SQL query
        
    Returns:
        Table name or None
    """
    # Simple regex to find FROM clause
    match = re.search(r'\bFROM\s+([`"\']?)(\w+)\1', sql, re.IGNORECASE)
    if match:
        return match.group(2)
    return None


def execute_sql(
    workspace_id: str,
    sql: str,
    table_name: Optional[str] = None,
    max_rows: int = MAX_ROWS
) -> SQLResult:
    """
    Execute SQL query securely.
    
    Args:
        workspace_id: Workspace ID
        sql: SQL query to execute
        table_name: Optional explicit table name
        max_rows: Maximum rows to return
        
    Returns:
        SQLResult with query results
    """
    # Validate SQL
    is_valid, error_msg = validate_sql(sql)
    if not is_valid:
        return SQLResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            error=error_msg
        )
    
    # Find database path
    if not table_name:
        table_name = extract_table_from_sql(sql)
    
    if not table_name:
        return SQLResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            error="Could not determine target table"
        )
    
    db_path = find_table_db_path(workspace_id, table_name)
    
    if not db_path:
        return SQLResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            error=f"Table '{table_name}' not found in workspace"
        )
    
    try:
        # Connect with timeout and read-only mode
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",  # Read-only mode
            uri=True,
            timeout=MAX_EXECUTION_TIME
        )
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Set execution limits
        conn.execute(f"PRAGMA busy_timeout = {MAX_EXECUTION_TIME * 1000}")
        
        # Clean SQL
        sql_clean = sql.strip().rstrip(';')
        
        # Add LIMIT if not present
        sql_upper = sql_clean.upper()
        if "LIMIT" not in sql_upper:
            sql_clean = f"{sql_clean} LIMIT {max_rows + 1}"  # +1 to detect truncation
        
        # Execute query
        cursor.execute(sql_clean)
        rows = cursor.fetchall()
        
        # Get column names
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        
        # Check for truncation
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        
        # Convert to list of dicts
        result_rows = [dict(row) for row in rows]
        
        conn.close()
        
        return SQLResult(
            success=True,
            columns=columns,
            rows=result_rows,
            row_count=len(result_rows),
            truncated=truncated
        )
        
    except sqlite3.OperationalError as e:
        return SQLResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            error=f"SQL execution error: {str(e)}"
        )
    except Exception as e:
        return SQLResult(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            error=f"Unexpected error: {str(e)}"
        )


def format_sql_results(result: SQLResult, max_display_rows: int = 20) -> str:
    """
    Format SQL results for LLM context.
    
    Args:
        result: SQLResult from execution
        max_display_rows: Maximum rows to include in formatted output
        
    Returns:
        Formatted string for LLM
    """
    if not result.success:
        return f"SQL Error: {result.error}"
    
    if result.row_count == 0:
        return "Query returned no results."
    
    lines = []
    lines.append(f"Query returned {result.row_count} row(s){'(truncated)' if result.truncated else ''}:")
    lines.append("")
    
    # Format as table-like structure
    display_rows = result.rows[:max_display_rows]
    
    # Header
    lines.append(" | ".join(result.columns))
    lines.append("-" * 50)
    
    # Rows
    for row in display_rows:
        values = []
        for col in result.columns:
            val = row.get(col, "")
            # Truncate long values
            val_str = str(val) if val is not None else "NULL"
            if len(val_str) > 50:
                val_str = val_str[:47] + "..."
            values.append(val_str)
        lines.append(" | ".join(values))
    
    if result.row_count > max_display_rows:
        lines.append(f"... and {result.row_count - max_display_rows} more rows")
    
    return "\n".join(lines)


def get_workspace_tables_metadata(workspace_id: str) -> List[Dict[str, Any]]:
    """
    Get all tables metadata for a workspace.
    
    Args:
        workspace_id: Workspace ID
        
    Returns:
        List of table metadata dicts
    """
    workspace_dir = os.path.join(SQLITE_DIR, workspace_id)
    
    if not os.path.exists(workspace_dir):
        return []
    
    tables = []
    
    for filename in os.listdir(workspace_dir):
        if filename.endswith(".db"):
            db_path = os.path.join(workspace_dir, filename)
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [row[0] for row in cursor.fetchall()]
                
                for table_name in table_names:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [row[1] for row in cursor.fetchall()]
                    
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    row_count = cursor.fetchone()[0]
                    
                    tables.append({
                        "name": table_name,
                        "db_path": db_path,
                        "columns": columns,
                        "row_count": row_count
                    })
                
                conn.close()
            except Exception as e:
                print(f"Error reading database {db_path}: {str(e)}")
                continue
    
    return tables
