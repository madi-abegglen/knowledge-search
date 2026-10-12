"""Compare two eval result files per question (read-only).

Run:  .\\.venv\\Scripts\\python.exe scripts\\compare_evals.py baseline_tightened after_2b [id ...]
Prints metric deltas, per-question changes, and (for the listed ids) raw
answer / conflict_note / citations from the second file.
"""

import json
from pathlib import Path
import sys

RESULTS = Path(__file__).resolve().parent.parent / "evals" / "results"
METRICS = [
    ("passed", "questions passed"),
    ("retrieval_hit", "retrieval hit"),
    ("outcome_accuracy", "outcome accuracy"),
    ("correct_refusals", "correct refusals"),
    ("false_refusals", "false refusals"),
    ("citation_correct", "citation correctness"),
    ("must_contain", "must_contain pass"),
]


def load(name: str) -> dict:
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))


def superseded_sources() -> set[str]:
    """Sources whose front matter says doc_status: superseded."""
    found = set()
    for path in (RESULTS.parent.parent / "data").glob("*.md"):
        head = path.read_text(encoding="utf-8").split("---", 2)
        if len(head) == 3 and "doc_status: superseded" in head[1]:
            found.add(path.name)
    return found


def fmt(value) -> str:
    return f"{value[0]}/{value[1]}" if isinstance(value, list) else str(value)


def main(argv: list[str]) -> int:
    before, after = load(argv[0]), load(argv[1])
    show_ids = {int(a) for a in argv[2:]}
    sb, sa = before["summary"], after["summary"]

    print(f"{'metric':<24} {argv[0]:>20} {argv[1]:>12}")
    for key, label in METRICS:
        b = f"{sb[key]}/{sb['questions']}" if key == "passed" else fmt(sb[key])
        a = f"{sa[key]}/{sa['questions']}" if key == "passed" else fmt(sa[key])
        print(f"{label:<24} {b:>20} {a:>12}")
    for key, label in [("leaks", "permission leaks"), ("latency_avg_ms", "latency avg ms"),
                       ("latency_p95_ms", "latency p95 ms"), ("input_tokens", "input tokens"),
                       ("output_tokens", "output tokens"), ("cost_total_usd", "cost total usd")]:
        print(f"{label:<24} {fmt(sb.get(key)):>20} {fmt(sa.get(key)):>12}")

    rb = {r["id"]: r for r in before["results"]}
    ra = {r["id"]: r for r in after["results"]}
    print("\nPer-question changes:")
    worse = []
    for qid in sorted(rb):
        b, a = rb[qid], ra[qid]
        notes = []
        if b["passed"] != a["passed"]:
            notes.append("PASS->FAIL (WORSE)" if b["passed"] else "FAIL->PASS")
            if b["passed"] and not a["passed"]:
                worse.append(qid)
        if b["retrieved_sources"] != a["retrieved_sources"]:
            notes.append("retrieved set changed")
        if bool(b["conflict_note"]) != bool(a["conflict_note"]):
            notes.append(f"conflict_note {'added' if a['conflict_note'] else 'removed'}")
        if b["actual_outcome"] != a["actual_outcome"]:
            notes.append(f"outcome {b['actual_outcome']}->{a['actual_outcome']}")
        if notes:
            print(f"  Q{qid}: " + "; ".join(notes))
    print("questions that got WORSE:", worse or "none")

    superseded = superseded_sources()
    for name, data in ((argv[0], rb), (argv[1], ra)):
        hit = [q for q, r in sorted(data.items()) if superseded.intersection(r["retrieved_sources"])]
        print(f"retrieved sets with a superseded chunk [{name}]: {len(hit)}/{len(data)} {hit}")

    for qid in sorted(show_ids):
        for label, r in ((argv[0], rb[qid]), (argv[1], ra[qid])):
            print(f"\n--- Q{qid} [{label}] ---")
            for key in ("answer", "conflict_note", "citations", "retrieved_sources"):
                print(f"{key}: {json.dumps(r[key], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
