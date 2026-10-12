"""Measure how often the model writes a conflict note for one question (measurement only).

Run from the repo root:
  .\\.venv\\Scripts\\python.exe scripts\\measure_conflict_note.py --user hr_user --repeat 20 \
      "How many PTO days do new hires get?"

Makes real API calls (one per repeat). Changes nothing; prints counts and sample notes. The question
and the answers are printed to the operator console only; nothing is logged or saved.
"""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.answer as A  # noqa: E402
from app.retrieve import retrieve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="hr_user")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--prompt-version", default="1")
    parser.add_argument("question", nargs="+")
    args = parser.parse_args()
    os.environ["PROMPT_VERSION"] = args.prompt_version
    question = " ".join(args.question)

    kept = retrieve(args.user, question)
    stale = [c for c in kept if c["metadata"].get("doc_status") != "current"]
    print(f"user={args.user} prompt=v{args.prompt_version} runs={args.repeat}")
    print(f"retrieved: {len(kept)} chunks, of which superseded: {len(stale)} "
          f"({', '.join(sorted({c['source'] + ' ' + c['metadata']['section'] for c in stale}))})")

    outcomes: dict[str, int] = {}
    notes: list[str] = []
    answers_ok = 0
    for _ in range(args.repeat):
        result = A.answer_question(args.user, question)
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
        if result["outcome"] == "answer":
            answers_ok += "20" in (result["answer"] or "")
            if (result.get("conflict_note") or "").strip():
                notes.append(result["conflict_note"].strip())

    print(f"outcomes: {outcomes}")
    print(f"answers containing '20': {answers_ok}/{args.repeat}")
    print(f"answers WITH a conflict note: {len(notes)}/{outcomes.get('answer', 0)} answers "
          f"({len(notes)}/{args.repeat} runs)")
    seen: list[str] = []
    for note in notes:
        if note not in seen:
            seen.append(note)
    print(f"distinct note texts: {len(seen)}")
    for i, note in enumerate(seen[:3], 1):
        print(f"  sample {i}: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
