"""
Routes for multi-tenant RAG platform.
Uses Qdrant for vector storage and MongoDB for document management.
"""

from fastapi import HTTPException, UploadFile, File, Form
from sentence_transformers import SentenceTransformer
import numpy as np
import json
import os
import requests
import uuid
import time
import sqlite3
import math
from fastapi.responses import StreamingResponse
from requests.exceptions import RequestException
from typing import Optional
from config import GEMINI_URL, DEVICE, SQLITE_DIR
from models import Query, IngestRequest, AnswerResponse, MultiTenantQuery, DeleteDocumentRequest
from utils import (
    chunk_text,
    flatten_dict,
    dict_to_semantic_text,
    normalize_json_to_records,
    row_to_semantic_text,
    normalize_csv_to_records,
    normalize_excel_to_records,
    profile_csv,
    profile_excel,
    profile_json,
    profile_text,
    check_file_size,
    determine_storage_mode,
    create_sqlite_table,
    drop_sqlite_table,
    get_sqlite_tables,
    delete_sqlite_database,
    generate_schema_summary,
    generate_metadata_with_llm,
    build_metadata_for_query,
    json_to_semantic_sentences,
    json_heuristic_mode,
    flatten_json_to_rows,
    create_sqlite_table_from_json,
    generate_json_schema_summary
)
from qdrant_utils import (
    get_collection_name,
    create_collection_if_not_exists,
    upsert_vectors,
    search_vectors,
    delete_document_vectors,
    count_document_vectors
)
from mongo_utils import (
    update_document_status,
    update_ingestion_job,
    update_workspace_stats,
    get_document,
    get_workspace,
    update_workspace_sqlite_path,
    get_workspace_sqlite_path,
    get_document_table_name,
    get_workspace_documents_with_tables
)

# Initialize embedding model
embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device=DEVICE)
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device=DEVICE)

# Embedding dimension for all-MiniLM-L6-v2
EMBEDDING_DIMENSION = 384

# Ensure SQLite directory exists
os.makedirs(SQLITE_DIR, exist_ok=True)


# def get_answer(query: Query):
#     """
#     Answer endpoint using workspace_id (api_key).
#     Uses Qdrant for vector search.
#     """
#     question = query.question
#     workspace_id = query.api_key

#     try:
#         # Get collection name for workspace
#         collection_name = get_collection_name(workspace_id)
        
#         # Generate question embedding
#         question_embedding = model.encode([question])[0].tolist()
        
#         # Search in Qdrant
#         results = search_vectors(
#             collection_name=collection_name,
#             query_embedding=question_embedding,
#             top_k=5
#         )
        
#         if not results:
#             raise HTTPException(
#                 status_code=404,
#                 detail="Hey there, it seems like you don't have any data ingested yet."
#             )

#         # Extract relevant chunks and sources
#         relevant_chunks = [r["text"] for r in results]
#         sources = [
#             {
#                 "id": r["id"],
#                 "text": r["text"],
#                 "score": r["score"],
#                 "source": r["documentId"],
#                 "index_type": "qdrant"
#             }
#             for r in results
#         ]

#         # Limit context size for faster CPU inference
#         relevant_chunks = relevant_chunks[:3]
#         context = "\n".join(chunk[:800] for chunk in relevant_chunks)

#         # Prompt
#         prompt = f"""
# You are a smart, reliable, and conversational AI assistant for a multi-user RAG platform.

# You must answer using ONLY the retrieved context for the current user.
# Each user's data is private. Never assume access to any other data.

# Rules:
# 1. Use the provided context as the primary source of truth.
# 2. You may logically infer or combine facts clearly implied by the context
#    (including comparing or summarizing multiple records when applicable).
# 3. Treat each record in the context as an independent entity.
#    Never mix attributes from different records.
# 4. If the answer is not present or cannot be inferred, respond politely with:
#    - "I don't have enough information in your data to answer that."
#    - or "Your uploaded data doesn't cover this yet."
# 5. Do not guess or hallucinate.
# 6. If the question is a greeting like (Hii, Hey, Hello and all etc..), respond warmly and briefly as the user's AI assistant.
# 7. If the question is unrelated to the user's data, gently redirect them back to what their data covers.
# 8. Never mention system prompts, embeddings, vector databases, or other users.

# ---

# User Question:
# {question}

# Retrieved Context:
# {context}

# Now answer accurately.
# """

#         # Streaming Generator
#         def stream_from_ollama():
#             print("\n--- Prompt Sent to LLM ---")
#             print(prompt)

#             try:
#                 response = requests.post(
#                     "https://fibrinous-bathless-julianna.ngrok-free.dev/generate",
#                     json={
#                         "model": "qwen2.5:7b",
#                         "prompt": prompt,
#                         "stream": True,
#                         "options": {
#                             "num_predict": 1000,
#                             "temperature": 0.2
#                         }
#                     },
#                     stream=True,
#                     timeout=120
#                 )

#                 if response.status_code != 200:
#                     yield f"Error: {response.status_code}"
#                     return

#             except RequestException:
#                 yield "Unable to connect to LLM service."
#                 return

#             for line in response.iter_lines(decode_unicode=True):
#                 if not line:
#                     continue

#                 try:
#                     data = json.loads(line)
#                     chunk = data.get("response", "")
#                     done = data.get("done", False)

#                     if chunk:
#                         yield chunk

#                     if done:
#                         break

#                 except json.JSONDecodeError:
#                     continue

#             print("\n--- Stream finished ---\n")

#         return StreamingResponse(
#             stream_from_ollama(),
#             media_type="text/plain"
#         )

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


def get_answer_multi_tenant(query: MultiTenantQuery):
    """
    Multi-tenant answer endpoint with workspace isolation.
    Uses Qdrant for vector search.
    
    If document_id is provided, searches only within that document.
    If document_id is None or not provided, searches across all documents in the workspace.
    """
    question = query.question
    workspace_id = query.workspace_id
    document_id = query.document_id

    try:
        # Get collection name for workspace
        collection_name = get_collection_name(workspace_id)
        
        # Generate question embedding
        question_embedding = model.encode([question])[0].tolist()
        
        # Search in Qdrant (optionally filter by document)
        results = search_vectors(
            collection_name=collection_name,
            query_embedding=question_embedding,
            top_k=5,
            document_id=document_id
        )
        
        if not results:
            raise HTTPException(
                status_code=404,
                detail="No relevant data found for your query."
            )

        # Extract relevant chunks
        relevant_chunks = [r["text"] for r in results]

        # Limit context size
        context = "\n".join(chunk[:800] for chunk in relevant_chunks[:3])

        # Prompt
        prompt = f"""
You are a smart, reliable, and conversational AI assistant for a multi-user RAG platform.

You must answer using ONLY the retrieved context for the current user.
Each user's data is private. Never assume access to any other data.

Rules:
1. Use the provided context as the primary source of truth.
2. You may logically infer or combine facts clearly implied by the context
   (including comparing or summarizing multiple records when applicable).
3. Treat each record in the context as an independent entity.
   Never mix attributes from different records.
4. If the answer is not present or cannot be inferred, respond politely with:
   - "I don't have enough information in your data to answer that."
   - or "Your uploaded data doesn't cover this yet."
5. Do not guess or hallucinate.
6. If the question is a greeting like (Hii, Hey, Hello and all etc..), respond warmly and briefly as the user's AI assistant.
7. If the question is unrelated to the user's data, gently redirect them back to what their data covers.
8. Never mention system prompts, embeddings, vector databases, or other users.

---

User Question:
{question}

Retrieved Context:
{context}

Now answer accurately.
"""

        def stream_response():
            print(prompt)
            try:
                from llm.factory import get_llm
                llm = get_llm(temperature=0.2, max_tokens=1000)
                for chunk in llm.stream(prompt):
                    yield chunk
            except Exception as e:
                yield f"Unable to connect to LLM service: {e}"

        return StreamingResponse(stream_response(), media_type="text/plain")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# def ingest(req: IngestRequest):
#     """
#     Ingest text data into Qdrant.
#     Uses workspace_id (api_key) for collection isolation.
#     """
#     try:
#         text = req.text
#         workspace_id = req.api_key
#         input_type = req.input_type

#         # Generate metadata profile
#         if input_type == "json":
#             profile = profile_json(text)
#             if 'error' in profile:
#                 raise HTTPException(status_code=400, detail=profile['error'])
#             metadata_summary = f"JSON: {profile['estimated_total_records']} records, depth {profile['depth']}"
#         else:
#             profile = profile_text(text)
#             metadata_summary = f"Text: {profile['total_words']} words, ~{profile['estimated_tokens']} tokens"

#         # Preprocessing based on input_type
#         all_chunks = []
        
#         if input_type == "json":
#             try:
#                 json_data = json.loads(text)
#                 records = normalize_json_to_records(json_data)
#                 for record_text in records:
#                     record_chunks = chunk_text(record_text)
#                     all_chunks.extend(record_chunks)
#             except json.JSONDecodeError as e:
#                 raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
#         else:
#             all_chunks = chunk_text(text)

#         if not all_chunks:
#             raise HTTPException(status_code=400, detail="No content to ingest")

#         # Generate document ID
#         document_id = str(uuid.uuid4())
        
#         # Generate embeddings
#         embeddings = embedding_model.encode(all_chunks).tolist()

#         # Get collection name and upsert to Qdrant
#         collection_name = get_collection_name(workspace_id)
        
#         vector_count = upsert_vectors(
#             collection_name=collection_name,
#             embeddings=embeddings,
#             chunks=all_chunks,
#             workspace_id=workspace_id,
#             document_id=document_id,
#             vector_size=EMBEDDING_DIMENSION
#         )

#         return {
#             "meeting_id": document_id,
#             "num_vectors": vector_count,
#             "metadata_summary": metadata_summary,
#             "storage_mode": "rag"
#         }

#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


# async def ingest_file(file: UploadFile, api_key: str, input_type: str):
#     """
#     Ingest file into Qdrant.
#     Supports CSV and Excel files with optional SQLite storage for structured data.
#     """
#     start_time = time.time()
#     print(f"Starting ingestion for {file.filename} ({input_type})")
    
#     try:
#         # Read file content
#         file_content = await file.read()
        
#         # Check file size
#         check_file_size(file_content)
        
#         # Generate metadata profile
#         if input_type == "csv":
#             profile = profile_csv(file_content)
#             storage_decision = determine_storage_mode(profile)
#             metadata_summary = f"CSV: {profile['number_of_rows']} rows, {profile['number_of_columns']} columns"
#         elif input_type == "excel":
#             profile = profile_excel(file_content)
#             storage_decision = determine_storage_mode(profile)
#             metadata_summary = f"Excel: {profile['number_of_rows']} rows, {profile['number_of_columns']} columns"
#         else:
#             raise HTTPException(status_code=400, detail=f"Unsupported input_type for file upload: {input_type}")
        
#         print(f"Storage mode: {storage_decision['storage_mode']}")
        
#         # Generate document ID
#         document_id = str(uuid.uuid4())
#         workspace_id = api_key
#         collection_name = get_collection_name(workspace_id)
        
#         # Create SQLite table if storage mode is sqlite
#         sqlite_created = False
#         db_path = None
#         table_name = None
        
#         if storage_decision['storage_mode'] == 'sqlite' and input_type in ['csv', 'excel']:
#             print("Creating SQLite table...")
#             workspace_dir = os.path.join(SQLITE_DIR, workspace_id)
#             os.makedirs(workspace_dir, exist_ok=True)
#             db_path = os.path.join(workspace_dir, f"data_{document_id}.db")
#             table_name = f"table_{document_id.replace('-', '_')}"
            
#             try:
#                 row_count = create_sqlite_table(file_content, input_type, db_path, table_name)
#                 sqlite_created = True
#                 print(f"SQLite table created with {row_count} rows")
#             except Exception as e:
#                 print(f"SQLite creation failed: {str(e)}, falling back to RAG")
#                 storage_decision['storage_mode'] = 'rag'
        
#         # Generate chunks to embed
#         all_chunks = []
        
#         if sqlite_created:
#             # For SQLite mode, embed schema summary
#             schema_summary = generate_schema_summary(profile, table_name)
#             all_chunks = chunk_text(schema_summary)
#             print(f"Schema summary chunks: {len(all_chunks)}")
#         else:
#             # For RAG mode, embed all records
#             print("Normalizing records...")
#             if input_type == "csv":
#                 records = normalize_csv_to_records(file_content)
#             else:
#                 records = normalize_excel_to_records(file_content)
            
#             for record_text in records:
#                 record_chunks = chunk_text(record_text)
#                 all_chunks.extend(record_chunks)
            
#             print(f"Total chunks: {len(all_chunks)}")
        
#         if not all_chunks:
#             raise HTTPException(status_code=400, detail="No content to ingest")
        
#         # Generate embeddings
#         print("Generating embeddings...")
#         embeddings = embedding_model.encode(all_chunks).tolist()
        
#         # Upsert to Qdrant
#         print("Upserting to Qdrant...")
#         vector_count = upsert_vectors(
#             collection_name=collection_name,
#             embeddings=embeddings,
#             chunks=all_chunks,
#             workspace_id=workspace_id,
#             document_id=document_id,
#             vector_size=EMBEDDING_DIMENSION
#         )
        
#         total_time = time.time() - start_time
#         print(f"Ingestion completed in {total_time:.2f}s")
        
#         return {
#             "meeting_id": document_id,
#             "num_vectors": vector_count,
#             "metadata_summary": metadata_summary,
#             "storage_mode": storage_decision['storage_mode'],
#             "sqlite_created": sqlite_created,
#             "database_path": db_path,
#             "table_name": table_name,
#             "embedding_mode": "schema_only" if sqlite_created else "full_content"
#         }

#     except HTTPException:
#         raise
#     except Exception as e:
#         print(f"Error during ingestion: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


async def ingest_document(
    workspace_id: str,
    document_id: str,
    file: Optional[UploadFile] = None,
    raw_text: Optional[str] = None,
    raw_json: Optional[str] = None,
):
    """
    Multi-tenant document ingestion endpoint.

    Supports:
    - File uploads (csv, excel, pdf, json, txt)
    - Raw plain text
    - Raw JSON string

    Always updates MongoDB after ingestion.
    """

    start_time = time.time()
    print(f"Starting ingestion for document {document_id}")
    print(f"DEBUG: workspace_id={workspace_id}, document_id={document_id}")
    print(f"DEBUG: file={file.filename if file else None}, raw_text={bool(raw_text)}, raw_json={bool(raw_json)}")

    try:
        update_ingestion_job(document_id, "processing")
        
        # Get userId from workspace for SQLite directory structure
        workspace_obj = get_workspace(workspace_id)
        user_id = workspace_obj.get("userId") if workspace_obj else None
        if not user_id:
            raise HTTPException(status_code=400, detail="Workspace not found or missing userId")
        print(f"DEBUG: user_id={user_id}")

        profile = {}
        all_chunks = []
        storage_mode = "rag"
        table_name = None
        db_path = None
        sqlite_created = False
        vector_count = 0  # Track if vectors were created for cleanup
        content_preview = ""  # For LLM metadata generation
        file_type = "text"  # For LLM metadata generation

        # =========================================================
        # 1️⃣ HANDLE RAW TEXT INPUT
        # =========================================================
        if raw_text:
            print(f"DEBUG: Processing raw plain text input, length: {len(raw_text)}")
            profile = profile_text(raw_text)
            from ingestion.helpers import ingest_plain_text
            child_chunks, parent_chunks, _emb = ingest_plain_text(raw_text, embedding_model)
            all_chunks = child_chunks
            _parent_chunks_text = parent_chunks
            content_preview = raw_text[:2000]
            file_type = "text"
            print(f"DEBUG: Generated {len(all_chunks)} hierarchical chunks from raw text")

        # =========================================================
        # 2️⃣ HANDLE RAW JSON STRING
        # =========================================================
        elif raw_json:
            print(f"DEBUG: Processing raw JSON string input, length: {len(raw_json)}")
            try:
                profile = profile_json(raw_json)
                json_data = json.loads(raw_json)
                content_preview = raw_json[:2000]  # Store preview for LLM
                file_type = "json"
                
                # Get LLM metadata early to determine storage mode
                print("DEBUG: Getting LLM metadata for raw JSON mode detection...")
                llm_metadata = generate_metadata_with_llm(
                    content_preview=content_preview,
                    file_type=file_type,
                    profile=profile,
                    table_name=None
                )
                
                # Extract data_mode from LLM response
                data_mode = llm_metadata.get("data_mode", "rag")
                mode_confidence = llm_metadata.get("data_mode_confidence", 0.5)
                print(f"DEBUG: LLM recommended data_mode={data_mode} with confidence={mode_confidence}")
                
                # If confidence is low, use heuristic fallback
                if mode_confidence < 0.6:
                    heuristic_mode = json_heuristic_mode(json_data, profile)
                    print(f"DEBUG: Low confidence, heuristic suggests: {heuristic_mode}")
                    # Use heuristic if it differs and makes more sense
                    if heuristic_mode != "rag" and data_mode == "rag":
                        data_mode = heuristic_mode
                        print(f"DEBUG: Using heuristic mode instead: {data_mode}")
                
                # Map data_mode to storage_mode
                storage_mode = "rag" if data_mode == "rag" else ("sqlite" if data_mode == "structured" else "hybrid")
                print(f"DEBUG: Final storage_mode for raw JSON: {storage_mode}")
                
                # Handle based on storage mode
                if storage_mode in ["sqlite", "hybrid"]:
                    print(f"DEBUG: Creating SQLite table for raw JSON ({storage_mode})")
                    
                    # Generate table name
                    table_name = f"json_{document_id.replace('-', '_')}"
                    
                    # Build SQLite DB path: sqlite_data/{userId}/data_{workspaceId}.db
                    user_dir = os.path.join(SQLITE_DIR, str(user_id))
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    print(f"DEBUG: Raw JSON SQLite path: {db_path}, table: {table_name}")
                    
                    # Update workspace SQLite path in MongoDB
                    update_workspace_sqlite_path(workspace_id, db_path)
                    
                    try:
                        row_count, columns = create_sqlite_table_from_json(json_data, db_path, table_name)
                        sqlite_created = True
                        print(f"DEBUG: Created SQLite table with {row_count} rows, {len(columns)} columns")
                        
                        # Update profile with table info
                        profile["table_name"] = table_name
                        profile["row_count"] = row_count
                        profile["columns"] = columns
                        
                        # Generate schema summary
                        schema_summary = generate_json_schema_summary(columns, row_count, table_name)
                        profile["schema_summary"] = schema_summary
                        
                    except ValueError as e:
                        print(f"WARNING: Failed to create SQLite from raw JSON: {e}")
                        # Fall back to RAG mode if table creation fails
                        storage_mode = "rag"
                        sqlite_created = False
                
                # Generate chunks based on mode
                if storage_mode == "rag" or storage_mode == "hybrid":
                    print(f"DEBUG: Generating semantic chunks for raw JSON ({storage_mode})")
                    # Use semantic sentences for better embedding quality
                    semantic_sentences = json_to_semantic_sentences(json_data)
                    if semantic_sentences:
                        for sentence in semantic_sentences:
                            if sentence.strip():
                                chunks = chunk_text(sentence)
                                all_chunks.extend(chunks)
                    
                    # Fallback to simple normalization if no semantic sentences
                    if not all_chunks:
                        records = normalize_json_to_records(json_data)
                        for record_text in records:
                            record_chunks = chunk_text(record_text)
                            all_chunks.extend(record_chunks)
                    
                    print(f"DEBUG: Generated {len(all_chunks)} chunks from JSON")
                else:
                    # Structured-only mode: no embeddings needed
                    print("DEBUG: Structured-only mode, skipping chunk generation")
                    all_chunks = []
                
                # Store LLM metadata for later (skip second call)
                profile["_llm_metadata_cached"] = llm_metadata

            except json.JSONDecodeError as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

        # =========================================================
        # 3️⃣ HANDLE FILE UPLOAD
        # =========================================================
        elif file:
            file_content = await file.read()
            filename = file.filename or "unknown"
            print(f"DEBUG: Processing file upload: {filename}, size: {len(file_content)} bytes")

            check_file_size(file_content)

            ext = filename.split('.')[-1].lower() if '.' in filename else ''
            file_type = ext if ext in ['csv', 'excel', 'json', 'pdf'] else 'text'
            print(f"DEBUG: File extension: {ext}")

            # ---------- CSV ----------
            if ext == "csv":
                print("DEBUG: Processing CSV file")
                file_type = "csv"
                profile = profile_csv(file_content)
                storage_decision = determine_storage_mode(profile)
                storage_mode = storage_decision['storage_mode']
                print(f"DEBUG: Storage mode for CSV: {storage_mode}")
                print(f"DEBUG: Storage decision details: {storage_decision}")

                num_rows = profile.get('number_of_rows', 0)
                print(f"DEBUG: CSV has {num_rows} rows")

                if storage_mode == 'sqlite':
                    user_dir = os.path.join(SQLITE_DIR, user_id)
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    table_name = f"table_{document_id.replace('-', '_')}"

                    try:
                        create_sqlite_table(file_content, 'csv', db_path, table_name)
                        sqlite_created = True
                        # Update workspace with SQLite path
                        update_workspace_sqlite_path(workspace_id, db_path)
                        schema_summary = generate_schema_summary(profile, table_name)
                        all_chunks = chunk_text(schema_summary)
                        print(f"DEBUG: SQLite mode - generated {len(all_chunks)} chunks from schema")
                    except Exception as e:
                        print(f"SQLite creation failed: {e}, fallback to RAG")
                        storage_mode = 'rag'

                elif storage_mode == 'hybrid':
                    print("DEBUG: Hybrid mode - creating SQLite table AND chunks")
                    # Create user directory and SQLite table
                    user_dir = os.path.join(SQLITE_DIR, user_id)
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    table_name = f"table_{document_id.replace('-', '_')}"
                    
                    try:
                        create_sqlite_table(file_content, 'csv', db_path, table_name)
                        sqlite_created = True
                        # Update workspace with SQLite path
                        update_workspace_sqlite_path(workspace_id, db_path)
                        print(f"DEBUG: Hybrid mode - SQLite table {table_name} created")
                    except Exception as e:
                        print(f"WARNING: SQLite creation failed in hybrid mode: {e}")
                        # Continue anyway - vectors will still work
                    
                    # Now create chunks for vector storage
                    if num_rows < 1000:
                        print("DEBUG: Row count < 1000, creating chunks from data")
                        records = normalize_csv_to_records(file_content)
                        record_count = 0
                        for record_text in records:
                            record_chunks = chunk_text(record_text)
                            all_chunks.extend(record_chunks)
                            record_count += 1
                        print(f"DEBUG: Generated {len(all_chunks)} total chunks from {record_count} CSV records")
                    else:
                        print("DEBUG: Row count >= 1000, using schema summary for chunks")
                        # For large datasets, generate chunks from schema summary
                        schema_summary = generate_schema_summary(profile, table_name)
                        all_chunks = chunk_text(schema_summary)
                        print(f"DEBUG: Generated {len(all_chunks)} chunks from schema summary")

                if storage_mode == 'rag':
                    # Set table_name for metadata even in rag mode
                    if not table_name:
                        table_name = f"table_{document_id.replace('-', '_')}"
                    records = normalize_csv_to_records(file_content)
                    print(f"DEBUG: RAG mode - processing CSV records")
                    record_count = 0
                    for record_text in records:
                        record_chunks = chunk_text(record_text)
                        all_chunks.extend(record_chunks)
                        record_count += 1
                    print(f"DEBUG: Generated {len(all_chunks)} total chunks from {record_count} CSV records")

            # ---------- EXCEL ----------
            elif ext in ["xlsx", "xls"]:
                print("DEBUG: Processing Excel file")
                file_type = "excel"
                profile = profile_excel(file_content)
                storage_decision = determine_storage_mode(profile)
                storage_mode = storage_decision['storage_mode']
                print(f"DEBUG: Storage mode for Excel: {storage_mode}")
                print(f"DEBUG: Storage decision details: {storage_decision}")

                num_rows = profile.get('number_of_rows', 0)
                print(f"DEBUG: Excel has {num_rows} rows")

                if storage_mode == 'sqlite':
                    user_dir = os.path.join(SQLITE_DIR, user_id)
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    table_name = f"table_{document_id.replace('-', '_')}"

                    try:
                        create_sqlite_table(file_content, 'excel', db_path, table_name)
                        sqlite_created = True
                        # Update workspace with SQLite path
                        update_workspace_sqlite_path(workspace_id, db_path)
                        schema_summary = generate_schema_summary(profile, table_name)
                        all_chunks = chunk_text(schema_summary)
                        print(f"DEBUG: SQLite mode - generated {len(all_chunks)} chunks from schema")
                    except Exception as e:
                        print(f"SQLite creation failed: {e}, fallback to RAG")
                        storage_mode = 'rag'

                elif storage_mode == 'hybrid':
                    print("DEBUG: Hybrid mode - creating SQLite table AND chunks")
                    # Create user directory and SQLite table
                    user_dir = os.path.join(SQLITE_DIR, user_id)
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    table_name = f"table_{document_id.replace('-', '_')}"
                    
                    try:
                        create_sqlite_table(file_content, 'excel', db_path, table_name)
                        sqlite_created = True
                        # Update workspace with SQLite path
                        update_workspace_sqlite_path(workspace_id, db_path)
                        print(f"DEBUG: Hybrid mode - SQLite table {table_name} created")
                    except Exception as e:
                        print(f"WARNING: SQLite creation failed in hybrid mode: {e}")
                        # Continue anyway - vectors will still work
                    
                    # Now create chunks for vector storage
                    if num_rows < 1000:
                        print("DEBUG: Row count < 1000, creating chunks from data")
                        records = normalize_excel_to_records(file_content)
                        for record_text in records:
                            record_chunks = chunk_text(record_text)
                            all_chunks.extend(record_chunks)
                        print(f"DEBUG: Generated {len(all_chunks)} total chunks from Excel records")
                    else:
                        print("DEBUG: Row count >= 1000, using schema summary for chunks")
                        # For large datasets, generate chunks from schema summary
                        schema_summary = generate_schema_summary(profile, table_name)
                        all_chunks = chunk_text(schema_summary)
                        print(f"DEBUG: Generated {len(all_chunks)} chunks from schema summary")

                if storage_mode == 'rag':
                    # Set table_name for metadata even in rag mode
                    if not table_name:
                        table_name = f"table_{document_id.replace('-', '_')}"
                    records = normalize_excel_to_records(file_content)
                    print(f"DEBUG: RAG mode - normalized Excel to {len(records)} records")
                    for record_text in records:
                        record_chunks = chunk_text(record_text)
                        all_chunks.extend(record_chunks)
                    print(f"DEBUG: Generated {len(all_chunks)} total chunks from Excel records")

            # ---------- JSON FILE ----------
            elif ext == "json":
                print("DEBUG: Processing JSON file")
                file_type = "json"
                text = file_content.decode("utf-8")
                content_preview = text[:2000]  # Store preview for LLM
                profile = profile_json(text)
                json_data = json.loads(text)
                
                # Get LLM metadata early to determine storage mode
                print("DEBUG: Getting LLM metadata for JSON mode detection...")
                llm_metadata = generate_metadata_with_llm(
                    content_preview=content_preview,
                    file_type=file_type,
                    profile=profile,
                    table_name=None
                )
                
                # Extract data_mode from LLM response
                data_mode = llm_metadata.get("data_mode", "rag")
                mode_confidence = llm_metadata.get("data_mode_confidence", 0.5)
                print(f"DEBUG: LLM recommended data_mode={data_mode} with confidence={mode_confidence}")
                
                # If confidence is low, use heuristic fallback
                if mode_confidence < 0.6:
                    heuristic_mode = json_heuristic_mode(json_data, profile)
                    print(f"DEBUG: Low confidence, heuristic suggests: {heuristic_mode}")
                    # Use heuristic if it differs and makes more sense
                    if heuristic_mode != "rag" and data_mode == "rag":
                        data_mode = heuristic_mode
                        print(f"DEBUG: Using heuristic mode instead: {data_mode}")
                
                # Map data_mode to storage_mode
                storage_mode = "rag" if data_mode == "rag" else ("sqlite" if data_mode == "structured" else "hybrid")
                print(f"DEBUG: Final storage_mode for JSON: {storage_mode}")
                
                # Handle based on storage mode
                if storage_mode in ["sqlite", "hybrid"]:
                    print(f"DEBUG: Creating SQLite table for JSON ({storage_mode})")
                    
                    # Generate table name
                    table_name = f"json_{document_id.replace('-', '_')}"
                    
                    # Build SQLite DB path: sqlite_data/{userId}/data_{workspaceId}.db
                    user_dir = os.path.join(SQLITE_DIR, str(user_id))
                    os.makedirs(user_dir, exist_ok=True)
                    db_path = os.path.join(user_dir, f"data_{workspace_id}.db")
                    print(f"DEBUG: JSON SQLite path: {db_path}, table: {table_name}")
                    
                    # Update workspace SQLite path in MongoDB
                    update_workspace_sqlite_path(workspace_id, db_path)
                    
                    try:
                        row_count, columns = create_sqlite_table_from_json(json_data, db_path, table_name)
                        sqlite_created = True
                        print(f"DEBUG: Created SQLite table with {row_count} rows, {len(columns)} columns")
                        
                        # Update profile with table info
                        profile["table_name"] = table_name
                        profile["row_count"] = row_count
                        profile["columns"] = columns
                        
                        # Generate schema summary
                        schema_summary = generate_json_schema_summary(columns, row_count, table_name)
                        profile["schema_summary"] = schema_summary
                        
                    except ValueError as e:
                        print(f"WARNING: Failed to create SQLite from JSON: {e}")
                        # Fall back to RAG mode if table creation fails
                        storage_mode = "rag"
                        sqlite_created = False
                
                # Generate chunks based on mode
                if storage_mode == "rag" or storage_mode == "hybrid":
                    print(f"DEBUG: Generating semantic chunks for JSON ({storage_mode})")
                    # Use semantic sentences for better embedding quality
                    semantic_sentences = json_to_semantic_sentences(json_data)
                    if semantic_sentences:
                        for sentence in semantic_sentences:
                            if sentence.strip():
                                chunks = chunk_text(sentence)
                                all_chunks.extend(chunks)
                    
                    # Fallback to simple normalization if no semantic sentences
                    if not all_chunks:
                        records = normalize_json_to_records(json_data)
                        for record_text in records:
                            record_chunks = chunk_text(record_text)
                            all_chunks.extend(record_chunks)
                    
                    print(f"DEBUG: Generated {len(all_chunks)} chunks from JSON")
                else:
                    # Structured-only mode: no embeddings needed
                    print("DEBUG: Structured-only mode, skipping chunk generation")
                    all_chunks = []
                
                # Store LLM metadata for later (skip second call)
                profile["_llm_metadata_cached"] = llm_metadata

            # ---------- TEXT FILE ----------
            elif ext == "txt":
                print("DEBUG: Processing text file")
                file_type = "text"
                text = file_content.decode("utf-8")
                content_preview = text[:2000]  # Store preview for LLM
                profile = profile_text(text)
                all_chunks = chunk_text(text)
                print(f"DEBUG: Generated {len(all_chunks)} chunks from text file")

            # ---------- PDF ----------
            elif ext == "pdf":
                print("DEBUG: Processing PDF file with pymupdf (layout-aware)")
                file_type = "pdf"
                from ingestion.helpers import ingest_pdf
                child_chunks, parent_chunks, _embeddings_pdf = ingest_pdf(file_content, embedding_model)
                # Store chunks for later embedding
                all_chunks = child_chunks
                _parent_chunks_pdf = parent_chunks
                content_preview = (child_chunks[0] if child_chunks else "")[:2000]
                profile = profile_text(content_preview)
                print(f"DEBUG: Extracted {len(all_chunks)} chunks from PDF (hierarchical)")

            else:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

        else:
            raise HTTPException(
                status_code=400,
                detail="No input provided. Provide file OR raw_text OR raw_json."
            )

        # =========================================================
        # VALIDATION
        # =========================================================
        print(f"DEBUG: Final chunk count: {len(all_chunks)}")
        if not all_chunks:
            print("WARNING: No chunks generated - proceeding with metadata-only ingestion")
            # Allow metadata-only ingestion for large datasets or when chunking is skipped
            vector_count = 0  # No vectors to create
        else:
            print("DEBUG: Proceeding with chunk-based ingestion")

        # =========================================================
        # LLM METADATA GENERATION (single call)
        # =========================================================
        # Check if we already have cached LLM metadata (from JSON early detection)
        cached_llm_metadata = profile.get("_llm_metadata_cached")
        if cached_llm_metadata:
            print("DEBUG: Using cached LLM metadata from JSON processing")
            llm_metadata = cached_llm_metadata
            # Clean up the cache marker
            del profile["_llm_metadata_cached"]
        else:
            print("DEBUG: Generating LLM metadata...")
            llm_metadata = generate_metadata_with_llm(
                content_preview=content_preview,
                file_type=file_type,
                profile=profile,
                table_name=table_name
            )
        print(f"DEBUG: LLM metadata: {llm_metadata}")
        
        # Build lightweight metadata for query planning
        metadata_for_query = build_metadata_for_query(
            llm_metadata=llm_metadata,
            storage_mode=storage_mode,
            profile=profile,
            table_name=table_name
        )
        print(f"DEBUG: Metadata for query built: {metadata_for_query}")

        # =========================================================
        # EMBEDDINGS (only if we have chunks)
        # =========================================================
        vectors_created = False  # Track for cleanup
        if all_chunks:
            print(f"Generating embeddings for {len(all_chunks)} chunks...")
            embeddings = embedding_model.encode(all_chunks).tolist()

            collection_name = get_collection_name(workspace_id)

            # Pass parent_chunks if available (hierarchical chunking for text/PDF)
            _parent_chunks = locals().get('_parent_chunks_pdf') or locals().get('_parent_chunks_text') or None
            vector_count = upsert_vectors(
                collection_name=collection_name,
                embeddings=embeddings,
                chunks=all_chunks,
                workspace_id=workspace_id,
                document_id=document_id,
                vector_size=EMBEDDING_DIMENSION,
                parent_chunks=_parent_chunks  # Hierarchical chunking
            )
            vectors_created = True
            
            # Only update MongoDB and mark as completed if vectors were successfully created
            update_document_status(
                document_id=document_id,
                status="completed",
                vector_count=vector_count,
                storage_mode=storage_mode,
                table_name=table_name,
                metadata=profile,
                metadata_for_query=metadata_for_query
            )

            update_ingestion_job(document_id, "completed")
            update_workspace_stats(workspace_id, vectors_added=vector_count, documents_added=1)
            
        else:
            print("DEBUG: Skipping embeddings - no chunks to embed")
            collection_name = get_collection_name(workspace_id)
            vector_count = 0
            
            # For metadata-only ingestion, still update MongoDB
            update_document_status(
                document_id=document_id,
                status="completed",
                vector_count=vector_count,
                storage_mode=storage_mode,
                table_name=table_name,
                metadata=profile,
                metadata_for_query=metadata_for_query
            )

            update_ingestion_job(document_id, "completed")
            update_workspace_stats(workspace_id, vectors_added=vector_count, documents_added=1)

        total_time = time.time() - start_time
        print(f"Ingestion completed in {total_time:.2f}s")

        ingestion_type = "chunk-based" if all_chunks else "metadata-only"
        print(f"DEBUG: Completed {ingestion_type} ingestion with {vector_count} vectors")

        def convert_numpy_and_sanitize(obj):
            if isinstance(obj, dict):
                return {k: convert_numpy_and_sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy_and_sanitize(v) for v in obj]
            elif isinstance(obj, np.generic):
                return obj.item()
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            else:
                return obj

        # Ensure LLM metadata is always valid
        if not isinstance(llm_metadata, dict):
            llm_metadata = {
                "data_mode": "rag",
                "data_mode_confidence": 0.5
            }

        # Extract data_mode from LLM response
        data_mode = llm_metadata.get("data_mode", "rag")
        mode_confidence = llm_metadata.get("data_mode_confidence", 0.5)
        print(f"DEBUG: LLM recommended data_mode={data_mode} with confidence={mode_confidence}")
        
        # Build lightweight metadata for query planning
        metadata_for_query = build_metadata_for_query(
            llm_metadata=llm_metadata,
            storage_mode=storage_mode,
            profile=profile,
            table_name=table_name
        )
        print(f"DEBUG: Metadata for query built: {metadata_for_query}")

        response = {
            "success": True,
            "document_id": document_id,
            "workspace_id": workspace_id,
            "vector_count": vector_count,
            "storage_mode": storage_mode,
            "table_name": table_name,
            "ingestion_type": ingestion_type,
            "metadata": profile,
            "metadataForQuery": metadata_for_query,
            "message": f"Ingestion completed successfully ({ingestion_type})"
        }
        return convert_numpy_and_sanitize(response)

    except Exception as e:
        print(f"Ingestion failed: {str(e)}")
        
        # Cleanup: If vectors were created but MongoDB update failed, remove them
        try:
            if vectors_created and vector_count > 0:
                print(f"DEBUG: Cleaning up {vector_count} vectors from failed ingestion")
                collection_name = get_collection_name(workspace_id)
                delete_document_vectors(collection_name, document_id)
        except Exception as cleanup_error:
            print(f"WARNING: Failed to cleanup vectors: {cleanup_error}")
        
        # Always mark as failed in MongoDB
        update_document_status(document_id, "failed")
        update_ingestion_job(document_id, "failed", str(e))
        raise HTTPException(status_code=500, detail=str(e))

def delete_document(req: DeleteDocumentRequest):
    """
    Delete all vectors for a document from Qdrant and drop SQLite table if exists.
    """
    try:
        workspace_id = req.workspace_id
        document_id = req.document_id
        
        collection_name = get_collection_name(workspace_id)
        
        # Get vector count before deletion for stats update
        vector_count = count_document_vectors(collection_name, document_id)
        
        # Delete vectors from Qdrant
        success = delete_document_vectors(collection_name, document_id)
        
        # Get document's table name from MongoDB before any cleanup
        table_name = get_document_table_name(document_id)
        
        # If document has a SQLite table, drop it
        if table_name:
            db_path = get_workspace_sqlite_path(workspace_id)
            if db_path and os.path.exists(db_path):
                drop_result = drop_sqlite_table(db_path, table_name)
                print(f"DEBUG: Drop table {table_name} result: {drop_result}")
                
                # Check if DB is now empty (no tables left)
                remaining_tables = get_sqlite_tables(db_path)
                if not remaining_tables:
                    # Delete the empty DB file
                    delete_sqlite_database(db_path)
                    print(f"DEBUG: Deleted empty SQLite database {db_path}")
        
        if success:
            # Update workspace stats
            update_workspace_stats(workspace_id, vectors_added=-vector_count, documents_added=-1)
            
            return {
                "success": True,
                "message": f"Document {document_id} deleted successfully"
            }
        else:
            return {
                "success": False,
                "message": "Failed to delete document vectors"
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def delete_workspace(workspace_id: str):
    """
    Delete entire workspace collection from Qdrant and SQLite database.
    """
    try:
        from qdrant_client import QdrantClient
        from config import QDRANT_URL
        
        client = QdrantClient(url=QDRANT_URL)
        collection_name = get_collection_name(workspace_id)
        
        # Delete the entire Qdrant collection
        client.delete_collection(collection_name)
        
        # Delete SQLite database for this workspace
        db_path = get_workspace_sqlite_path(workspace_id)
        if db_path and os.path.exists(db_path):
            delete_sqlite_database(db_path)
            print(f"DEBUG: Deleted workspace SQLite database {db_path}")
        
        return {
            "success": True,
            "message": f"Workspace {workspace_id} collection deleted successfully"
        }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete workspace: {str(e)}")

def delete_all_documents_from_workspace(workspace_id: str):
    """
    Delete all vectors from a workspace collection in Qdrant and all SQLite tables.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models
        from config import QDRANT_URL
        
        client = QdrantClient(url=QDRANT_URL)
        collection_name = get_collection_name(workspace_id)
        
        # Delete all points in the collection using an empty filter (matches all points)
        client.delete(
            collection_name=collection_name,
            points_selector=models.Filter()  # Empty filter matches all points
        )
        
        # Delete all SQLite tables for this workspace
        db_path = get_workspace_sqlite_path(workspace_id)
        if db_path and os.path.exists(db_path):
            # Get all documents with tables in this workspace
            docs_with_tables = get_workspace_documents_with_tables(workspace_id)
            for doc in docs_with_tables:
                table_name = doc.get("tableName")
                if table_name:
                    drop_sqlite_table(db_path, table_name)
                    print(f"DEBUG: Dropped table {table_name}")
            
            # Check if DB is now empty
            remaining_tables = get_sqlite_tables(db_path)
            if not remaining_tables:
                delete_sqlite_database(db_path)
                print(f"DEBUG: Deleted empty SQLite database {db_path}")
        
        return {
            "success": True,
            "message": f"All documents deleted from workspace {workspace_id}"
        }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete all documents: {str(e)}")


# =============================================================================
# REFACTORED QUERY API
# =============================================================================

def handle_query(req):
    """
    Handle query using the refactored QueryService.
    
    This endpoint uses:
    - LLM abstraction (Ollama/Gemini via factory)
    - Intent classification (semantic/structured/hybrid/clarification)
    - Tool execution (semantic search, SQL, clarification)
    - Conversation memory (last 6 messages in workspace)
    
    Args:
        req: QueryRequest with workspace_id, question, document_id, stream
        
    Returns:
        Query response or streaming generator
    """
    from services.query_service import QueryService
    
    try:
        service = QueryService()
        result = service.handle_query(
            workspace_id=req.workspace_id,
            question=req.question,
            document_id=req.document_id,
            stream=req.stream
        )
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


def clear_memory(workspace_id: str):
    """Clear all memory and workflow state for a workspace."""
    from services.memory_service import clear_all
    try:
        clear_all(workspace_id)
        return {"success": True, "message": "Memory and workflow state cleared."}
    except Exception as e:
        return {"success": False, "message": str(e)}
