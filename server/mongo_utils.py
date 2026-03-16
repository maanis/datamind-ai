"""
MongoDB utilities for updating document status after ingestion.
Uses pymongo to connect to the same MongoDB as the Node.js server.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import math
from pymongo import MongoClient
from bson import ObjectId
import numpy as np

from config import MONGODB_URI


def convert_numpy_types(obj: Any) -> Any:
    """
    Recursively convert numpy types to Python native types for MongoDB serialization.
    Also handles NaN and Infinity values.
    """
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    elif isinstance(obj, np.ndarray):
        return convert_numpy_types(obj.tolist())
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj

# Initialize MongoDB client
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client.get_default_database()

# Collections
documents_collection = db["documents"]
ingestion_jobs_collection = db["ingestionjobs"]
workspaces_collection = db["workspaces"]


def update_document_status(
    document_id: str,
    status: str,
    vector_count: int = 0,
    storage_mode: str = "rag",
    table_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    metadata_for_query: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Update document status after ingestion.
    
    Args:
        document_id: MongoDB ObjectId as string
        status: 'completed' or 'failed'
        vector_count: Number of vectors created
        storage_mode: 'rag', 'sqlite', or 'hybrid'
        table_name: SQLite table name if applicable
        metadata: Additional metadata from profiling
        metadata_for_query: Lightweight LLM-generated metadata for query planning
    
    Returns:
        True if update was successful
    """
    try:
        update_data = {
            "ingestionStatus": status,
            "vectorCount": int(vector_count) if vector_count else 0,
            "storageMode": storage_mode
        }
        
        if table_name:
            update_data["tableName"] = table_name
        
        if metadata:
            update_data["metadata"] = convert_numpy_types(metadata)
        
        if metadata_for_query:
            update_data["metadataForQuery"] = convert_numpy_types(metadata_for_query)
        
        result = documents_collection.update_one(
            {"_id": ObjectId(document_id)},
            {"$set": update_data}
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"Error updating document {document_id}: {str(e)}")
        return False


def update_ingestion_job(
    document_id: str,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """
    Update ingestion job status.
    
    Args:
        document_id: MongoDB ObjectId as string for the document
        status: 'processing', 'completed', or 'failed'
        error_message: Error message if failed
    
    Returns:
        True if update was successful
    """
    try:
        update_data = {
            "status": status
        }
        
        if status == "processing":
            update_data["startedAt"] = datetime.utcnow()
        elif status in ["completed", "failed"]:
            update_data["completedAt"] = datetime.utcnow()
        
        if error_message:
            update_data["errorMessage"] = error_message
        
        result = ingestion_jobs_collection.update_one(
            {"documentId": ObjectId(document_id)},
            {"$set": update_data}
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"Error updating ingestion job for document {document_id}: {str(e)}")
        return False


def update_workspace_stats(workspace_id: str, vectors_added: int = 0, documents_added: int = 0) -> bool:
    """
    Update workspace statistics.
    
    Args:
        workspace_id: MongoDB ObjectId as string
        vectors_added: Number of vectors to add to total
        documents_added: Number of documents to add to total
    
    Returns:
        True if update was successful
    """
    try:
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {
                "$inc": {
                    "totalVectors": vectors_added,
                    "totalDocuments": documents_added
                }
            }
        )
        
        return result.modified_count > 0
    except Exception as e:
        print(f"Error updating workspace {workspace_id}: {str(e)}")
        return False


def get_document(document_id: str) -> Optional[Dict[str, Any]]:
    """
    Get document by ID.
    
    Args:
        document_id: MongoDB ObjectId as string
    
    Returns:
        Document dict or None
    """
    try:
        doc = documents_collection.find_one({"_id": ObjectId(document_id)})
        if doc:
            doc["_id"] = str(doc["_id"])
            doc["workspaceId"] = str(doc["workspaceId"])
        return doc
    except Exception as e:
        print(f"Error getting document {document_id}: {str(e)}")
        return None


def get_workspace(workspace_id: str) -> Optional[Dict[str, Any]]:
    """
    Get workspace by ID.
    
    Args:
        workspace_id: MongoDB ObjectId as string
    
    Returns:
        Workspace dict or None
    """
    try:
        ws = workspaces_collection.find_one({"_id": ObjectId(workspace_id)})
        if ws:
            ws["_id"] = str(ws["_id"])
            ws["userId"] = str(ws["userId"])
        return ws
    except Exception as e:
        print(f"Error getting workspace {workspace_id}: {str(e)}")
        return None


def get_workspace_metadata_for_query(workspace_id: str) -> List[Dict[str, Any]]:
    """
    Get metadataForQuery + full metadata stats from all completed documents in a workspace.

    Loads both:
      - metadataForQuery  → lightweight planner context (summary, keywords, columns, tableName)
      - metadata          → full numeric stats (number_of_rows, numeric_stats, unique_value_count, ...)
                            used by metadata-first answering to skip SQL/RAG for trivial stat queries

    Args:
        workspace_id: MongoDB ObjectId as string

    Returns:
        List of dicts with document_id, fileName, metadataForQuery fields, and nested metadata stats
    """
    try:
        cursor = documents_collection.find(
            {
                "workspaceId": ObjectId(workspace_id),
                "ingestionStatus": "completed",
                "metadataForQuery": {"$exists": True}
            },
            {
                "_id": 1,
                "metadataForQuery": 1,
                "metadata": 1,      # ← include full stats for metadata-first answering
                "fileName": 1,
                "tableName": 1,
                "storageMode": 1,
            }
        )

        metadata_list = []
        for doc in cursor:
            mfq = doc.get("metadataForQuery", {})
            if not mfq:
                continue

            # Ensure metadataForQuery is a dict
            if isinstance(mfq, str):
                try:
                    mfq = json.loads(mfq)
                except json.JSONDecodeError:
                    print(f"WARNING: Invalid metadataForQuery for doc {doc['_id']}: {mfq[:100]}...")
                    continue

            if not isinstance(mfq, dict):
                continue

            # Inject cross-references
            mfq["document_id"] = str(doc["_id"])
            mfq["fileName"] = doc.get("fileName", "unknown")
            mfq["tableName"] = doc.get("tableName") or mfq.get("tableName")
            mfq["storageMode"] = doc.get("storageMode") or mfq.get("storageMode", "rag")

            # Attach full metadata stats (numeric_stats, unique_value_count, etc.)
            full_meta = doc.get("metadata")
            if full_meta and isinstance(full_meta, dict):
                mfq["metadata"] = full_meta

            metadata_list.append(mfq)

        return metadata_list
    except Exception as e:
        print(f"Error getting metadata for workspace {workspace_id}: {str(e)}")
        return []


def get_document_metadata_for_query(document_id: str) -> Optional[Dict[str, Any]]:
    """
    Get metadataForQuery for a specific document.
    
    Args:
        document_id: MongoDB ObjectId as string
        
    Returns:
        metadataForQuery dict or None
    """
    try:
        doc = documents_collection.find_one(
            {"_id": ObjectId(document_id)},
            {"metadataForQuery": 1, "fileName": 1}
        )
        
        if doc and doc.get("metadataForQuery"):
            metadata = doc["metadataForQuery"]
            metadata["document_id"] = str(doc["_id"])
            metadata["fileName"] = doc.get("fileName", "unknown")
            return metadata
        
        return None
    except Exception as e:
        print(f"Error getting metadata for document {document_id}: {str(e)}")
        return None


def update_workspace_sqlite_path(workspace_id: str, sqlite_db_path: str) -> bool:
    """
    Update workspace's sqliteDbPath when first SQLite table is created.
    
    Args:
        workspace_id: MongoDB ObjectId as string
        sqlite_db_path: Path to the workspace's SQLite database
        
    Returns:
        True if update was successful
    """
    try:
        result = workspaces_collection.update_one(
            {"_id": ObjectId(workspace_id)},
            {"$set": {"sqliteDbPath": sqlite_db_path}}
        )
        return result.modified_count > 0 or result.matched_count > 0
    except Exception as e:
        print(f"Error updating workspace SQLite path {workspace_id}: {str(e)}")
        return False


def get_workspace_sqlite_path(workspace_id: str) -> Optional[str]:
    """
    Get workspace's SQLite database path.
    
    Args:
        workspace_id: MongoDB ObjectId as string
        
    Returns:
        SQLite database path or None
    """
    try:
        ws = workspaces_collection.find_one(
            {"_id": ObjectId(workspace_id)},
            {"sqliteDbPath": 1}
        )
        return ws.get("sqliteDbPath") if ws else None
    except Exception as e:
        print(f"Error getting workspace SQLite path {workspace_id}: {str(e)}")
        return None


def get_document_table_name(document_id: str) -> Optional[str]:
    """
    Get the SQLite table name for a document.
    
    Args:
        document_id: MongoDB ObjectId as string
        
    Returns:
        Table name or None
    """
    try:
        doc = documents_collection.find_one(
            {"_id": ObjectId(document_id)},
            {"tableName": 1}
        )
        return doc.get("tableName") if doc else None
    except Exception as e:
        print(f"Error getting document table name {document_id}: {str(e)}")
        return None


def get_workspace_documents_with_tables(workspace_id: str) -> List[Dict[str, Any]]:
    """
    Get all documents in a workspace that have SQLite tables.
    
    Args:
        workspace_id: MongoDB ObjectId as string
        
    Returns:
        List of documents with tableName
    """
    try:
        cursor = documents_collection.find(
            {
                "workspaceId": ObjectId(workspace_id),
                "tableName": {"$exists": True, "$ne": None}
            },
            {"_id": 1, "tableName": 1}
        )
        return [{"_id": str(doc["_id"]), "tableName": doc["tableName"]} for doc in cursor]
    except Exception as e:
        print(f"Error getting documents with tables for workspace {workspace_id}: {str(e)}")
        return []
