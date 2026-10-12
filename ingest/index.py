"""Embed and upsert source-document chunks into local ChromaDB."""

import os
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from ingest.chunk import MAX_TOKENS, chunk_document
from ingest.load import GROUPS, SourceDocument, load_documents


COLLECTION_NAME = "knowledge_chunks"


def _chunk_metadata(document: SourceDocument, chunk_id: str, section: str) -> dict[str, str | bool]:
    metadata: dict[str, str | bool] = {
        "source": document.source,
        "title": document.metadata["title"],
        "chunk_id": chunk_id,
        "section": section,
        "version": document.metadata["version"],
        "effective_date": document.metadata["effective_date"],
        "doc_status": document.metadata["doc_status"],
        "last_modified": document.metadata["last_modified"],
    }
    metadata.update({f"grp_{group}": group in document.allowed_groups for group in GROUPS})
    return metadata


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    documents = load_documents(data_dir)

    model_name = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name, trust_remote_code=False)
    tokenizer = model.tokenizer

    chunks_by_document = [
        (document, chunk_document(document, tokenizer))
        for document in documents
    ]
    flat_chunks = [
        (document, index, chunk)
        for document, chunks in chunks_by_document
        for index, chunk in enumerate(chunks)
    ]
    texts = [chunk.text for _, _, chunk in flat_chunks]              # stored document: original text
    embed_texts = [chunk.embed_text for _, _, chunk in flat_chunks]  # embedding input: unchanged
    token_counts = [chunk.token_count for _, _, chunk in flat_chunks]
    assert all(count <= MAX_TOKENS for count in token_counts)

    vectors = model.encode(
        embed_texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    ids = [f"{document.source}::{index}" for document, index, _ in flat_chunks]
    metadatas = [
        _chunk_metadata(document, f"{document.source}::{index}", chunk.section)
        for document, index, chunk in flat_chunks
    ]

    configured_path = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    chroma_path = configured_path if configured_path.is_absolute() else repo_root / configured_path
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    collection.upsert(
        ids=ids,
        embeddings=vectors.tolist(),
        documents=texts,
        metadatas=metadatas,
    )

    current_ids = set(ids)
    existing = collection.get(include=["metadatas"])
    stale_ids = [existing_id for existing_id in existing["ids"] if existing_id not in current_ids]
    if stale_ids:
        collection.delete(ids=stale_ids)

    for document, chunks in chunks_by_document:
        print(f"{document.source}: {len(chunks)} chunks")
    print(f"Total chunks: {collection.count()}")


if __name__ == "__main__":
    main()