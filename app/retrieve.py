"""Permission-filtered retrieval from the local Chroma collection."""

import json
import os
from functools import lru_cache
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
SUPERSEDED_MARGIN = 0.10  # fixed a priori; not tuned against evals


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


@lru_cache(maxsize=1)
def _get_model(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name, trust_remote_code=False)


@lru_cache(maxsize=1)
def _get_collection(chroma_path: str) -> Any:
    client = chromadb.PersistentClient(path=chroma_path)
    return client.get_collection(COLLECTION_NAME)


def _select_final(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best current chunks first; reserve ONE slot for a superseded chunk of a
    same-titled document so conflicts can surface. Superseded chunks go last."""
    by_distance = lambda chunk: chunk["distance"]  # noqa: E731
    current = sorted(
        (c for c in chunks if c["metadata"].get("doc_status") == "current"), key=by_distance
    )
    superseded = sorted(
        (c for c in chunks if c["metadata"].get("doc_status") != "current"), key=by_distance
    )

    head = current[: KEEP_BEST - 1]
    titles = {chunk["metadata"].get("title") for chunk in head}
    reserved = None
    if current:
        limit = current[0]["distance"] + SUPERSEDED_MARGIN
        reserved = next(
            (c for c in superseded
             if c["metadata"].get("title") in titles and c["distance"] <= limit),
            None,
        )
    if reserved is None:
        return (current + superseded)[:KEEP_BEST]

    rest = [c for c in superseded if c is not reserved]
    return (head + [reserved] + rest)[:KEEP_BEST]


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
    model = _get_model(model_name)

    configured_path = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    chroma_path = configured_path if configured_path.is_absolute() else ROOT / configured_path
    collection = _get_collection(str(chroma_path))
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

    return _select_final(chunks)