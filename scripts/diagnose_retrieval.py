"""Diagnose Q17 retrieval ordering and inspect raw baseline answers (read-only).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\diagnose_retrieval.py
Replicates the query in app.retrieve.retrieve() but prints the raw top 8 BEFORE
the sort/truncate. Does not modify any app code.
"""

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import retrieve as R  # noqa: E402

USER = "employee"
QUESTION = "Is new-hire PTO 15 or 20 days?"
BASELINE = ROOT / "evals" / "results" / "baseline.json"


def raw_top_k() -> list[dict]:
    import os

    groups = R.get_user_groups(USER)
    conditions = [{f"grp_{g}": True} for g in R.GROUPS if g in groups]
    where = conditions[0] if len(conditions) == 1 else {"$or": conditions}
    R.load_dotenv(R.ROOT / ".env")
    model = R._get_model(os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2"))
    configured = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    path = configured if configured.is_absolute() else R.ROOT / configured
    collection = R._get_collection(str(path))
    embedding = model.encode(
        QUESTION, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    res = collection.query(
        query_embeddings=[embedding.tolist()],
        n_results=min(R.TOP_K, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"metadata": res["metadatas"][0][i], "distance": float(res["distances"][0][i]),
         "source": res["metadatas"][0][i]["source"]}
        for i in range(len(res["documents"][0]))
    ]


def show(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"{'rank':>4} {'distance':>8}  {'doc_status':<11} {'source':<30} section")
    for rank, row in enumerate(rows, 1):
        m = row["metadata"]
        print(f"{rank:>4} {row['distance']:>8.4f}  {m.get('doc_status', '?'):<11} "
              f"{row['source']:<30} {m.get('section', '?')}")


def main() -> int:
    chunks = raw_top_k()
    show(chunks, f"RAW top {R.TOP_K} from Chroma (before sort/truncate), user={USER}")

    ordered = sorted(chunks, key=lambda c: (c["metadata"].get("doc_status") != "current", c["distance"]))
    show(ordered, "AFTER sort (current first, then distance) - all 8")
    show(ordered[: R.KEEP_BEST], f"KEPT after truncate to {R.KEEP_BEST}")

    dropped = ordered[R.KEEP_BEST:]
    print("\nDROPPED by the sort/truncate:")
    for row in dropped:
        print(f"  {row['source']} / {row['metadata'].get('section')} "
              f"({row['metadata'].get('doc_status')}, distance {row['distance']:.4f})")
    in_raw = sorted({r["source"] for r in chunks})
    print("\nsources in raw top 8:", in_raw)
    print("superseded chunks in raw top 8:",
          sum(r["metadata"].get("doc_status") != "current" for r in chunks))

    live = [c["source"] for c in R.retrieve(USER, QUESTION)]
    kept = [c["source"] for c in ordered[: R.KEEP_BEST]]
    print("\nretrieve() sources:", live)
    print("replica matches retrieve():", live == kept)

    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["results"]}

    print("\n=== Q5 raw answer (repr) ===")
    answer5 = by_id[5]["answer"]
    print(repr(answer5))
    match = re.search(r"security.{0,12}northwind.{0,12}", answer5, flags=re.IGNORECASE)
    if match:
        text = match.group(0)
        print("email region:", repr(text))
        print("code points:", [f"{c}:U+{ord(c):04X}" for c in text])
    else:
        print("no 'security...northwind' region found")

    print("\n=== Q17 full result ===")
    r17 = by_id[17]
    for key in ("answer", "conflict_note", "clarifying_question", "citations", "retrieved_sources"):
        print(f"{key}: {json.dumps(r17[key], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
