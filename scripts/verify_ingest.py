"""Verify indexed source counts and per-chunk permission metadata."""

import json
import os
from pathlib import Path

import chromadb


GROUPS = ("all", "hr", "finance", "sales", "exec")
COLLECTION_NAME = "knowledge_chunks"


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    access_path = repo_root / "data" / "access.json"
    access = json.loads(access_path.read_text(encoding="utf-8"))
    expected_documents = access["documents"]

    configured_path = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    chroma_path = configured_path if configured_path.is_absolute() else repo_root / configured_path
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(COLLECTION_NAME)
    stored = collection.get(include=["metadatas"])

    chunks_by_source: dict[str, list[dict]] = {}
    for metadata in stored["metadatas"]:
        source = metadata.get("source")
        assert isinstance(source, str) and source, "chunk is missing its source metadata"
        chunks_by_source.setdefault(source, []).append(metadata)

    actual_sources = set(chunks_by_source)
    expected_sources = set(expected_documents)
    assert actual_sources == expected_sources, (
        f"source mismatch: missing={sorted(expected_sources - actual_sources)}, "
        f"unexpected={sorted(actual_sources - expected_sources)}"
    )

    print("source\tchunks\tgrp_all\tgrp_hr\tgrp_finance\tgrp_sales\tgrp_exec")
    for source in sorted(expected_sources):
        chunks = chunks_by_source[source]
        allowed_groups = expected_documents[source]["allowed_groups"]
        expected_values = {f"grp_{group}": group in allowed_groups for group in GROUPS}

        for metadata in chunks:
            for key, expected_value in expected_values.items():
                value = metadata.get(key)
                assert type(value) is bool, f"{source}: {key} must be a real boolean"
                assert value is expected_value, (
                    f"{source}: {key}={value} does not match access.json ({expected_value})"
                )

        values = chunks[0]
        print(
            f"{source}\t{len(chunks)}\t"
            + "\t".join(str(values[f"grp_{group}"]) for group in GROUPS)
        )

    print(f"Verified {len(expected_sources)} sources and {len(stored['ids'])} chunks.")


if __name__ == "__main__":
    main()