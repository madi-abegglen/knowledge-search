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
SUPERSEDED_SLOTS = 2


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


def _version_key(chunk: dict[str, Any]) -> tuple[Any, Any] | None:
    """Identity of a chunk across document versions: (title, section)."""
    metadata = chunk["metadata"]
    title, section = metadata.get("title"), metadata.get("section")
    return (title, section) if title and section else None


def _select_final(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best current chunks first. A retained current chunk whose exact (title, section)
    counterpart is a superseded chunk in the top 8 gets that counterpart reserved
    (max SUPERSEDED_SLOTS), so conflicts can surface. Superseded chunks go last.
    With no counterpart this is the plain sort-and-truncate."""
    by_distance = lambda chunk: chunk["distance"]  # noqa: E731
    current = sorted(
        (c for c in chunks if c["metadata"].get("doc_status") == "current"), key=by_distance
    )
    superseded = sorted(
        (c for c in chunks if c["metadata"].get("doc_status") != "current"), key=by_distance
    )

    reserved: list[dict[str, Any]] = []
    always_kept = KEEP_BEST - SUPERSEDED_SLOTS  # the head never shrinks below this
    for chunk in current[:always_kept]:
        key = _version_key(chunk)
        counterpart = next(
            (c for c in superseded
             if key is not None and _version_key(c) == key
             and all(c is not r for r in reserved)),
            None,
        )
        if counterpart is not None:
            reserved.append(counterpart)
    reserved = reserved[:SUPERSEDED_SLOTS]

    if not reserved:
        return (current + superseded)[:KEEP_BEST]

    head = current[: KEEP_BEST - len(reserved)]
    rest = [c for c in superseded if all(c is not r for r in reserved)]
    return (head + reserved + rest)[:KEEP_BEST]


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