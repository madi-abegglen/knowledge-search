"""Deterministic eval runner: retrieval hit rate, outcomes, citations, leaks, latency.

Run from the repo root:  python -m evals.run_evals [--name baseline]

Calls answer_question() and retrieve() directly (no UI), sequentially.
must_contain / must_not_contain: ";" separates required items, "|" separates
alternatives within one must_contain item. Matching is case-insensitive.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

from dotenv import load_dotenv
import yaml

from app.answer import answer_question, prompt_path
from app.retrieve import ROOT, get_user_groups, retrieve

QUESTIONS_PATH = ROOT / "evals" / "questions.csv"
RESULTS_DIR = ROOT / "evals" / "results"
ACCESS_PATH = ROOT / "data" / "access.json"


def split_items(cell: str) -> list[str]:
    return [item.strip() for item in cell.split(";") if item.strip()]


def load_questions() -> list[dict[str, str]]:
    with QUESTIONS_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_documents() -> dict[str, dict[str, Any]]:
    return json.loads(ACCESS_PATH.read_text(encoding="utf-8")).get("documents", {})


def front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


def allowed_sources(user: str, documents: dict[str, dict[str, Any]]) -> set[str]:
    groups = get_user_groups(user)
    return {
        source for source, entry in documents.items()
        if groups.intersection(entry.get("allowed_groups", []))
    }


def restricted_labels(allowed: set[str], documents: dict[str, dict[str, Any]]) -> set[str]:
    labels: set[str] = set()
    for source in documents:
        if source in allowed:
            continue
        labels.add(source.casefold())
        labels.add(Path(source).stem.replace("_", " ").casefold())
        labels.add(Path(source).stem.casefold())
        path = ROOT / "data" / source
        title = front_matter(path).get("title") if path.is_file() else None
        if isinstance(title, str) and title.strip():
            labels.add(title.strip().casefold())
            labels.add(re.sub(r"\s*\([^)]*\)\s*$", "", title.strip()).casefold())
    return {label for label in labels if label}


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def prompt_meta() -> dict[str, str]:
    path = prompt_path()
    data = front_matter(path)
    return {"file": path.name, "version": str(data.get("version", "unknown"))}


def price(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def contains_item(text: str, item: str) -> bool:
    return any(alt.strip().casefold() in text for alt in item.split("|") if alt.strip())


def check_question(
    row: dict[str, str],
    result: dict[str, Any],
    retrieved: list[str],
    allowed: set[str],
    labels: set[str],
) -> dict[str, Any]:
    expected = row["expected_outcome"]
    actual = result["outcome"]
    reasons: list[str] = []
    if actual != expected:
        reasons.append(f"outcome {actual} != expected {expected}")

    spoken = " ".join(
        part for part in (result.get("answer"), result.get("conflict_note"),
                          result.get("clarifying_question")) if part
    ).casefold()

    missing = [item for item in split_items(row["must_contain"]) if not contains_item(spoken, item)]
    must_contain_ok: bool | None = None
    if split_items(row["must_contain"]):
        must_contain_ok = not missing
        if missing:
            reasons.append("missing: " + "; ".join(missing))

    citations = result.get("citations") or []
    cited = [c["source"] for c in citations]
    expected_sources = split_items(row["expected_source"])
    citation_ok: bool | None = None
    if expected == "answer":
        absent = [s for s in expected_sources if s not in cited]
        citation_ok = actual == "answer" and not absent
        if actual == "answer" and absent:
            reasons.append("not cited: " + "; ".join(absent))
    not_allowed = sorted({s for s in cited if s not in allowed})

    scan = " ".join(
        [spoken] + [f"{c['source']} {c['section']}".casefold() for c in citations]
    )
    leak_strings = [
        alt.strip().casefold()
        for item in split_items(row["must_not_contain"])
        for alt in item.split("|") if alt.strip()
    ]
    leaks = sorted({s for s in leak_strings if s in scan} | {s for s in labels if s in scan})
    if not_allowed:
        leaks.append("cited non-permitted source: " + ", ".join(not_allowed))
    if leaks:
        reasons.append("LEAK: " + "; ".join(leaks))

    retrieval_hit: bool | None = None
    if expected == "answer":
        retrieval_hit = all(s in retrieved for s in expected_sources)
        if not retrieval_hit:
            reasons.append("retrieval miss: " + "; ".join(s for s in expected_sources if s not in retrieved))

    has_note = bool((result.get("conflict_note") or "").strip())
    expects_conflict = row.get("expects_conflict", "").strip().casefold() == "yes"
    conflict_ok: bool | None = None
    if expects_conflict:
        conflict_ok = has_note
        if not has_note:
            reasons.append("expected conflict_note, got none")
    false_conflict_note = has_note and not expects_conflict

    return {
        "passed": not reasons,
        "reasons": reasons,
        "retrieval_hit": retrieval_hit,
        "must_contain_ok": must_contain_ok,
        "citation_ok": citation_ok,
        "conflict_ok": conflict_ok,
        "false_conflict_note": false_conflict_note,
        "leaks": leaks,
    }


def run_one(row: dict[str, str], documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    user, question = row["user"], row["question"]
    allowed = allowed_sources(user, documents)
    labels = restricted_labels(allowed, documents)
    try:
        retrieved = [chunk["source"] for chunk in retrieve(user, question)]
    except Exception as exc:  # keep the run going; recorded as a failure
        retrieved = []
        retrieval_error = type(exc).__name__
    else:
        retrieval_error = None
    try:
        result = answer_question(user, question)
        error = None
    except Exception as exc:
        result = {"outcome": "error", "answer": "", "citations": [], "conflict_note": None,
                  "clarifying_question": None, "latency_ms": 0.0,
                  "input_tokens": 0, "output_tokens": 0}
        error = type(exc).__name__
    checks = check_question(row, result, retrieved, allowed, labels)
    if error:
        checks["passed"] = False
        checks["reasons"].append(f"answer_question raised {error}")
    if retrieval_error:
        checks["reasons"].append(f"retrieve raised {retrieval_error}")
        checks["passed"] = False
    return build_record(row, result, retrieved, checks)


def build_record(
    row: dict[str, str], result: dict[str, Any], retrieved: list[str], checks: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user": row["user"],
        "question": row["question"],
        "expected_outcome": row["expected_outcome"],
        "expected_source": split_items(row["expected_source"]),
        "actual_outcome": result["outcome"],
        "answer": result.get("answer"),
        "citations": result.get("citations"),
        "conflict_note": result.get("conflict_note"),
        "clarifying_question": result.get("clarifying_question"),
        "latency_ms": result["latency_ms"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "retrieved_sources": retrieved,
        **checks,
    }


def rescore_one(
    row: dict[str, str], stored: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Re-apply the current CSV checks to a stored result. No API or retrieval calls."""
    allowed = allowed_sources(row["user"], documents)
    labels = restricted_labels(allowed, documents)
    result = {
        "outcome": stored["actual_outcome"],
        "answer": stored["answer"],
        "citations": stored["citations"],
        "conflict_note": stored["conflict_note"],
        "clarifying_question": stored["clarifying_question"],
        "latency_ms": stored["latency_ms"],
        "input_tokens": stored["input_tokens"],
        "output_tokens": stored["output_tokens"],
    }
    checks = check_question(row, result, stored["retrieved_sources"], allowed, labels)
    return build_record(row, result, stored["retrieved_sources"], checks)


def rate(num: int, den: int) -> str:
    return f"{num}/{den} ({100 * num / den:.0f}%)" if den else "n/a"


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [r for r in results if r["expected_outcome"] == "answer"]
    refusals = [r for r in results if r["expected_outcome"] == "refuse"]
    with_contain = [r for r in results if r["must_contain_ok"] is not None]
    latencies = [r["latency_ms"] for r in results]
    return {
        "questions": len(results),
        "passed": sum(r["passed"] for r in results),
        "retrieval_hit": [sum(bool(r["retrieval_hit"]) for r in answerable), len(answerable)],
        "outcome_accuracy": [sum(r["actual_outcome"] == r["expected_outcome"] for r in results), len(results)],
        "correct_refusals": [sum(r["actual_outcome"] == "refuse" for r in refusals), len(refusals)],
        "false_refusals": [sum(r["actual_outcome"] == "refuse" for r in answerable), len(answerable)],
        "citation_correct": [sum(bool(r["citation_ok"]) for r in answerable), len(answerable)],
        "must_contain": [sum(bool(r["must_contain_ok"]) for r in with_contain), len(with_contain)],
        "conflict_flag": [sum(bool(r["conflict_ok"]) for r in results if r.get("conflict_ok") is not None),
                          sum(1 for r in results if r.get("conflict_ok") is not None)],
        "false_conflict_notes": sum(bool(r.get("false_conflict_note")) for r in results),
        "false_conflict_ids": [r["id"] for r in results if r.get("false_conflict_note")],
        "leaks": sum(1 for r in results if r["leaks"]),
        "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "latency_p95_ms": round(percentile_95(latencies), 1) if latencies else 0.0,
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
    }


def print_report(results: list[dict[str, Any]], summary: dict[str, Any], cost: float | None) -> None:
    print(f"{'id':>2} {'user':<9} {'expected':<8} {'actual':<8} {'result':<6} reason")
    for r in results:
        reason = " | ".join(r["reasons"]) if r["reasons"] else ""
        print(f"{r['id']:>2} {r['user']:<9} {r['expected_outcome']:<8} {r['actual_outcome']:<8} "
              f"{'PASS' if r['passed'] else 'FAIL':<6} {reason}")
    print()
    print(f"questions passed:      {rate(summary['passed'], summary['questions'])}")
    print(f"retrieval hit rate:    {rate(*summary['retrieval_hit'])} (answerable only)")
    print(f"outcome accuracy:      {rate(*summary['outcome_accuracy'])}")
    print(f"correct refusals:      {rate(*summary['correct_refusals'])}")
    print(f"false refusals:        {rate(*summary['false_refusals'])} (answerable questions refused)")
    print(f"citation correctness:  {rate(*summary['citation_correct'])} (answerable only)")
    print(f"must_contain pass:     {rate(*summary['must_contain'])}")
    print(f"conflict flagged:      {rate(*summary['conflict_flag'])} (where expects_conflict=yes)")
    print(f"false conflict notes:  {summary['false_conflict_notes']} {summary['false_conflict_ids']} (informational)")
    print(f"PERMISSION LEAKS:      {summary['leaks']}")
    print(f"latency avg / p95:     {summary['latency_avg_ms']:.0f} ms / {summary['latency_p95_ms']:.0f} ms")
    print(f"tokens in / out:       {summary['input_tokens']} / {summary['output_tokens']}")
    if cost is None:
        print("cost: not computed (prices not set)")
    else:
        print(f"cost:                  ${cost:.4f} total, ${cost / summary['questions']:.4f} per query")


def run_repeats(args: argparse.Namespace, rows: list[dict[str, str]],
                documents: dict[str, dict[str, Any]]) -> int:
    """Run each selected question args.repeat times; report pass counts per question."""
    per_question: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        per_question[int(row["id"])] = [run_one(row, documents) for _ in range(args.repeat)]

    print(f"{'id':>2} {'passes':>8} {'conflict_note':>13} {'must_contain':>12}  failure reasons")
    leaks = 0
    for qid, runs in per_question.items():
        passes = sum(r["passed"] for r in runs)
        notes = sum(bool((r.get("conflict_note") or "").strip()) for r in runs)
        contain = sum(bool(r["must_contain_ok"]) for r in runs)
        leaks += sum(1 for r in runs if r["leaks"])
        reasons = sorted({reason for r in runs for reason in r["reasons"]})
        print(f"{qid:>2} {passes:>3}/{args.repeat:<4} {notes:>8}/{args.repeat:<4} "
              f"{contain:>7}/{args.repeat:<4}  {' | '.join(reasons)}")
    print(f"PERMISSION LEAKS: {leaks}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.name}.json"
    payload = {
        "run_name": args.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": os.environ.get("MODEL_NAME", "unset"),
        "prompt": prompt_meta(),
        "repeats": args.repeat,
        "summary": {str(qid): {
            "passes": sum(r["passed"] for r in runs),
            "runs": len(runs),
            "conflict_notes": sum(bool((r.get("conflict_note") or "").strip()) for r in runs),
            "must_contain_ok": sum(bool(r["must_contain_ok"]) for r in runs),
        } for qid, runs in per_question.items()},
        "leaks": leaks,
        "results": {str(qid): runs for qid, runs in per_question.items()},
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved: {out_path.relative_to(ROOT)}")
    return 1 if leaks else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="baseline", help="results file name (evals/results/<name>.json)")
    parser.add_argument("--rescore", metavar="NAME",
                        help="re-score evals/results/NAME.json with the current CSV, offline (no API calls)")
    parser.add_argument("--only", metavar="IDS", help="comma-separated question ids to run, e.g. 2,5")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="run each selected question N times and report pass counts")
    parser.add_argument("--prompt-version", metavar="V",
                        help="prompt version to use (prompts/answer_v{V}.md); overrides PROMPT_VERSION")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.prompt_version:
        os.environ["PROMPT_VERSION"] = args.prompt_version
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    load_dotenv(ROOT / ".env")
    documents = load_documents()
    rows = load_questions()
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        rows = [row for row in rows if int(row["id"]) in wanted]
        if not rows:
            parser.error("--only matched no question ids")
    if args.repeat > 1 and args.rescore:
        parser.error("--repeat cannot be combined with --rescore")
    if args.repeat > 1:
        return run_repeats(args, rows, documents)

    stored_meta: dict[str, Any] = {}
    if args.rescore:
        if args.name == args.rescore:
            parser.error("--name must differ from --rescore so the original results are kept")
        stored_meta = json.loads((RESULTS_DIR / f"{args.rescore}.json").read_text(encoding="utf-8"))
        stored = {r["id"]: r for r in stored_meta["results"]}
        results = [rescore_one(row, stored[int(row["id"])], documents) for row in rows]
    else:
        results = [run_one(row, documents) for row in rows]
    summary = summarize(results)

    price_in, price_out = price("PRICE_INPUT_PER_MTOK"), price("PRICE_OUTPUT_PER_MTOK")
    cost = None
    if price_in is not None and price_out is not None:
        cost = (summary["input_tokens"] * price_in + summary["output_tokens"] * price_out) / 1_000_000
    summary["cost_total_usd"] = cost

    print_report(results, summary, cost)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.name}.json"
    payload = {
        "run_name": args.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rescored_from": args.rescore,
        "model": stored_meta.get("model") or os.environ.get("MODEL_NAME", "unset"),
        "prompt": stored_meta.get("prompt") or prompt_meta(),
        "embed_model": stored_meta.get("embed_model") or os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2"),
        "price_per_mtok": {"input": price_in, "output": price_out},
        "summary": summary,
        "results": results,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved: {out_path.relative_to(ROOT)}")
    return 1 if summary["leaks"] else 0


if __name__ == "__main__":
    sys.exit(main())
