"""Permission-filtered retrieval from the local Chroma collection."""

import json
import os
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer


ROOT = Path(__file__).resolve().parent.parent
COLLECTION_NAME = "knowledge_chunks"
GROUPS = ("all", "hr", "finance", "sales", "exec")
TOP_K = 8
KEEP_BEST = 5


def get_user_groups(user: str) -> frozenset[str]:
    access = json.loads((ROOT / "data" / "access.json").read_text(encoding="utf-8"))
    user_record = access.get("users", {}).get(user)
    if not isinstance(user_record, dict):
        return frozenset()
    groups = user_record.get("groups")
    if not isinstance(groups, list) or not groups:
        return frozenset()
    if any(not isinstance(group, str) or group not in GROUPS for group in groups):
        return frozenset()
    return frozenset(groups)


def retrieve(user: str, question: str) -> list[dict[str, Any]]:
    groups = get_user_groups(user)
    if not groups or not question.strip():
        return []

    conditions = [{f"grp_{group}": True} for group in GROUPS if group in groups]
    where: dict[str, Any]
    if len(conditions) == 1:
        where = conditions[0]
    else:
        where = {"$or": conditions}

    load_dotenv(ROOT / ".env")
    model_name = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name, trust_remote_code=False)

    configured_path = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    chroma_path = configured_path if configured_path.is_absolute() else ROOT / configured_path
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(COLLECTION_NAME)
    count = collection.count()
    if count == 0:
        return []

    embedding = model.encode(
        question,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    results = collection.query(
        query_embeddings=[embedding.tolist()],
        n_results=min(TOP_K, count),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for index, text in enumerate(results["documents"][0]):
        metadata = results["metadatas"][0][index]
        distance = results["distances"][0][index]
        chunks.append({
            "source": metadata["source"],
            "text": text,
            "metadata": metadata,
            "distance": float(distance),
        })

    chunks.sort(key=lambda chunk: (
        chunk["metadata"].get("doc_status") != "current",
        chunk["distance"],
    ))
    return chunks[:KEEP_BEST]