"""Compare two Chroma indexes built from the same data (read-only).

Run from the repo root:
  .\\.venv\\Scripts\\python.exe scripts\\compare_indexes.py <old_chroma_path> <new_chroma_path>

Checks per chunk: ids, metadata (incl. every grp_ flag), embeddings (max abs diff) and text
(old vs new). Then compares retrieval for every eval question: top-8 ids, order, distances.
"""

import csv
import os
from pathlib import Path
import sys

import chromadb
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.retrieve import GROUPS, TOP_K, get_user_groups  # noqa: E402
from ingest.load import load_documents  # noqa: E402

COLLECTION = "knowledge_chunks"


def load(path: str) -> dict:
    collection = chromadb.PersistentClient(path=path).get_collection(COLLECTION)
    data = collection.get(include=["documents", "metadatas", "embeddings"])
    order = sorted(range(len(data["ids"])), key=lambda i: data["ids"][i])
    return {
        "collection": collection,
        "ids": [data["ids"][i] for i in order],
        "documents": [data["documents"][i] for i in order],
        "metadatas": [data["metadatas"][i] for i in order],
        "embeddings": np.array([data["embeddings"][i] for i in order], dtype=np.float64),
    }


def main(old_path: str, new_path: str) -> int:
    old, new = load(old_path), load(new_path)
    ok = True

    print(f"chunk count: old={len(old['ids'])} new={len(new['ids'])}")
    same_ids = old["ids"] == new["ids"]
    print(f"ids identical (sorted): {same_ids}")
    ok &= same_ids and len(old["ids"]) == len(new["ids"])

    meta_diff = [i for i, (a, b) in enumerate(zip(old["metadatas"], new["metadatas"])) if a != b]
    grp_diff = [i for i in meta_diff
                if any(old["metadatas"][i].get(k) != new["metadatas"][i].get(k) for k in old["metadatas"][i]
                       if k.startswith("grp_"))]
    print(f"metadata identical per chunk: {not meta_diff} (differing chunks: {len(meta_diff)}; "
          f"differing grp_ flags: {len(grp_diff)})")
    ok &= not meta_diff

    max_diff = float(np.max(np.abs(old["embeddings"] - new["embeddings"]))) if len(old["ids"]) else 0.0
    print(f"embeddings max abs diff: {max_diff}")
    ok &= max_diff == 0.0

    changed = sum(a != b for a, b in zip(old["documents"], new["documents"]))
    empty = sum(not d.strip() for d in new["documents"])
    print(f"documents changed: {changed}/{len(new['ids'])}; empty new documents: {empty}")
    ok &= empty == 0

    # new text must be a true substring of the source (after the heading line)
    documents = {d.source: d.text for d in load_documents(ROOT / "data")}
    not_substring = []
    for cid, text in zip(new["ids"], new["documents"]):
        source = cid.split("::")[0]
        _, _, body = text.partition("\n")
        if body and body not in documents[source]:
            not_substring.append(cid)
    print(f"new text is a substring of its source (after the heading): "
          f"{len(new['ids']) - len(not_substring)}/{len(new['ids'])}  failures={not_substring}")
    ok &= not not_substring

    print("\n=== 3 sample chunks (old decoded vs new original) ===")
    security = next(i for i, d in enumerate(new["documents"]) if "security@northwind.example" in d)
    samples = [security, new["ids"].index("hr_pto_policy_2025.md::2"), new["ids"].index("finance_expense_policy.md::1")]
    for i in samples:
        print(f"\n--- {new['ids'][i]} (section: {new['metadatas'][i]['section']}) ---")
        print("OLD:", repr(old["documents"][i]))
        print("NEW:", repr(new["documents"][i]))

    # retrieval comparison for every eval question
    print("\n=== retrieval: top-8 ids / order / distances for Q1-Q25 ===")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2"), trust_remote_code=False)
    with (ROOT / "evals" / "questions.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    mismatches = 0
    for row in rows:
        groups = get_user_groups(row["user"])
        conditions = [{f"grp_{g}": True} for g in GROUPS if g in groups]
        where = conditions[0] if len(conditions) == 1 else {"$or": conditions}
        embedding = model.encode(row["question"], convert_to_numpy=True, normalize_embeddings=True,
                                 show_progress_bar=False).tolist()
        results = []
        for side in (old, new):
            res = side["collection"].query(query_embeddings=[embedding], n_results=TOP_K, where=where,
                                           include=["distances"])
            results.append((res["ids"][0], res["distances"][0]))
        same = results[0][0] == results[1][0] and results[0][1] == results[1][1]
        mismatches += not same
        print(f"Q{row['id']:>2} {row['user']:<9} top-8 identical: {same}")
    print(f"\nretrieval mismatches: {mismatches}/{len(rows)}")
    ok &= mismatches == 0

    print(f"\nOVERALL: {'IDENTICAL except stored text' if ok else 'DIFFERENCES FOUND'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: compare_indexes.py <old_chroma_path> <new_chroma_path>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
