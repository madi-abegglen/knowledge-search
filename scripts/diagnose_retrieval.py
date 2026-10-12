"""Diagnose retrieval ordering for one eval question (read-only, no API calls).

Run from the repo root:
  .\\.venv\\Scripts\\python.exe scripts\\diagnose_retrieval.py [question_id]   (default 17)

Replicates the query in app.retrieve.retrieve() but prints the raw top 8 BEFORE
the select step, then what _select_final keeps. Also prints a wider top-20 view
(informational) to see where superseded chunks rank. Does not modify app code.
"""

import csv
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import retrieve as R  # noqa: E402

QUESTIONS = ROOT / "evals" / "questions.csv"
STORED = ROOT / "evals" / "results" / "after_2b_gate.json"
WIDE = 20


def load_question(qid: int) -> tuple[str, str]:
    with QUESTIONS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["id"]) == qid:
                return row["user"], row["question"]
    raise SystemExit(f"question id {qid} not found in {QUESTIONS.name}")


def raw_top_k(user: str, question: str, n_results: int) -> list[dict]:
    groups = R.get_user_groups(user)
    conditions = [{f"grp_{g}": True} for g in R.GROUPS if g in groups]
    where = conditions[0] if len(conditions) == 1 else {"$or": conditions}
    R.load_dotenv(R.ROOT / ".env")
    model = R._get_model(os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2"))
    configured = Path(os.environ.get("CHROMA_PATH", "./chroma_db"))
    path = configured if configured.is_absolute() else R.ROOT / configured
    collection = R._get_collection(str(path))
    embedding = model.encode(
        question, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    )
    res = collection.query(
        query_embeddings=[embedding.tolist()],
        n_results=min(n_results, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"metadata": res["metadatas"][0][i], "distance": float(res["distances"][0][i]),
         "source": res["metadatas"][0][i]["source"], "text": res["documents"][0][i]}
        for i in range(len(res["documents"][0]))
    ]


def show(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    print(f"{'rank':>4} {'distance':>8}  {'doc_status':<11} {'source':<30} section")
    for rank, row in enumerate(rows, 1):
        m = row["metadata"]
        print(f"{rank:>4} {row['distance']:>8.4f}  {m.get('doc_status', '?'):<11} "
              f"{row['source']:<30} {m.get('section', '?')}")


def main(argv: list[str]) -> int:
    qid = int(argv[0]) if argv else 17
    user, question = load_question(qid)
    print(f"Q{qid} user={user}: {question}")

    chunks = raw_top_k(user, question, R.TOP_K)
    show(chunks, f"RAW top {R.TOP_K} from Chroma (before select), user={user}")

    selected = R._select_final([dict(c) for c in chunks])
    show(selected, f"KEPT by _select_final (superseded slots {R.SUPERSEDED_SLOTS})")
    live = [c["source"] for c in R.retrieve(user, question)]
    print("\nreplica matches retrieve():", live == [c["source"] for c in selected])

    superseded_top8 = [c for c in chunks if c["metadata"].get("doc_status") != "current"]
    print("superseded chunks in raw top 8:",
          [(c["source"], c["metadata"].get("section"), round(c["distance"], 4)) for c in superseded_top8])

    wide = raw_top_k(user, question, WIDE)
    print(f"\nWIDER view (informational, not what the app retrieves): top {WIDE}, superseded chunks only")
    for rank, row in enumerate(wide, 1):
        if row["metadata"].get("doc_status") != "current":
            m = row["metadata"]
            print(f"  rank {rank:>2}  {row['distance']:.4f}  {row['source']}  {m.get('section')}")

    if STORED.exists():
        stored = {r["id"]: r for r in json.loads(STORED.read_text(encoding="utf-8"))["results"]}
        r = stored.get(qid)
        if r:
            print(f"\n=== stored result Q{qid} ({STORED.name}) ===")
            for key in ("answer", "conflict_note", "citations", "retrieved_sources"):
                print(f"{key}: {json.dumps(r[key], ensure_ascii=False)}")
            if qid == 5:
                match = re.search(r"security.{0,12}northwind.{0,12}", r["answer"], flags=re.IGNORECASE)
                print("email region:", repr(match.group(0)) if match else "not found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
