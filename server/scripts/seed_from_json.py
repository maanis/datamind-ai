"""
Seed FAISS indexes / chunks JSON for the TIMSCDR chatbot.

Modes:
- server: POST each entry to your running FastAPI `/ingest` endpoint.
- local: Build FAISS index files and write them to `faiss_indexes/` (no server needed).

Input JSON formats supported:
1) List of objects: [{"id": "meeting1", "text": "..."}, ...]
2) Mapping: {"meeting1": "text...", "meeting2": "..."}

Usage examples:
python scripts/seed_from_json.py --input data.json --mode server --server-url http://localhost:8000
python scripts/seed_from_json.py --input data.json --mode local

Requirements (for local mode):
- sentence-transformers
- faiss (faiss-cpu recommended)
- numpy

"""
import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

import requests

# Local indexing dependencies will be imported lazily (only in local mode)

OUTPUT_DIR = "faiss_indexes"


def parse_input_file(path: str) -> List[Tuple[str, str]]:
    """Return a list of (id, text) tuples from the input JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries: List[Tuple[str, str]] = []
    if isinstance(data, list):
        # Expect list of objects with id & text
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("When input is a list, each item must be an object with 'id' and 'text'.")
            if 'id' not in item or 'text' not in item:
                raise ValueError("Missing 'id' or 'text' in one of the list items.")
            entries.append((str(item['id']), str(item['text'])))
    elif isinstance(data, dict):
        # Mapping of id -> text
        for k, v in data.items():
            entries.append((str(k), str(v)))
    else:
        raise ValueError("Unsupported JSON format. Provide a list of {id,text} or a mapping id->text.")

    return entries


def post_to_server(server_url: str, entries: List[Tuple[str, str]]):
    for meeting_id, text in entries:
        payload = {"id": meeting_id, "text": text}
        print(f"POST {meeting_id} -> {server_url}/ingest ...", end=" ")
        try:
            res = requests.post(f"{server_url.rstrip('/')}/ingest", json=payload, timeout=120)
            if res.ok:
                print("OK", res.json())
            else:
                print("FAILED", res.status_code, res.text)
        except Exception as e:
            print("ERROR", e)


def build_local_indexes(entries: List[Tuple[str, str]], chunk_size: int, overlap: int):
    # lazy imports
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        print("Missing sentence-transformers. Install with: pip install sentence-transformers")
        raise
    try:
        import faiss
    except Exception as e:
        print("Missing faiss. For CPU use: pip install faiss-cpu")
        raise
    import numpy as np

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def chunk_text(text: str) -> List[str]:
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunks.append(text[start:end])
            start = end - overlap if end < text_len else end
        return chunks

    for meeting_id, text in entries:
        print(f"Indexing {meeting_id} ...")
        chunks = chunk_text(text)
        if not chunks:
            print("  empty text, skipping")
            continue
        embeddings = model.encode(chunks)
        vectors = np.array(embeddings).astype("float32")
        dim = vectors.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(vectors)

        index_path = os.path.join(OUTPUT_DIR, f"{meeting_id}.index")
        chunks_path = os.path.join(OUTPUT_DIR, f"{meeting_id}_chunks.json")
        faiss.write_index(index, index_path)
        with open(chunks_path, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"  wrote: {index_path}")
        print(f"  wrote: {chunks_path}")


def main():
    parser = argparse.ArgumentParser(description="Seed FAISS indexes or call /ingest on server from a JSON file")
    parser.add_argument("--input", "-i", required=True, help="Path to input JSON file")
    parser.add_argument("--mode", "-m", choices=["server", "local"], default="server", help="server -> POST to /ingest; local -> build .index and _chunks.json files")
    parser.add_argument("--server-url", default="http://localhost:8000", help="Base URL of running FastAPI server (used with mode=server)")
    parser.add_argument("--chunk-size", type=int, default=500, help="Chunk size for local indexing")
    parser.add_argument("--overlap", type=int, default=50, help="Chunk overlap for local indexing")

    args = parser.parse_args()

    entries = parse_input_file(args.input)
    print(f"Found {len(entries)} entries in {args.input}")

    if args.mode == "server":
        post_to_server(args.server_url, entries)
    else:
        build_local_indexes(entries, args.chunk_size, args.overlap)


if __name__ == "__main__":
    main()
