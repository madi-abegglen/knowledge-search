"""Check README.md and docs/runbook.md: relative links, referenced repo paths, and numbers.

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\check_docs.py
1. every relative markdown link resolves to a file;
2. every backticked path-like token (contains / or \\ and a known extension) exists in the repo,
   ignoring placeholders (<...>, *, %TEMP%) and generated/ignored locations;
3. every README results number appears in docs/eval-results.md (or docs/edge-cases.md where cited).
Exit code 1 on any failure.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "docs" / "runbook.md"]
EXTENSIONS = (".md", ".py", ".txt", ".csv", ".json", ".jsonl", ".in", ".ps1")
IGNORED_PREFIXES = ("logs", "chroma_db", ".venv", "evals/results")   # generated or git-ignored
failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print("FAIL", message)


# ---- 1 + 2: links and paths
for doc in DOCS:
    text = doc.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
            continue
        if not (doc.parent / target).resolve().exists():
            fail(f"{doc.name}: link target missing: {target}")
    for token in re.findall(r"`([^`\n]+)`", text):
        token = token.strip()
        if not any(token.endswith(ext) for ext in EXTENSIONS) or not re.search(r"[\\/]", token):
            continue
        if re.search(r"[<>*%]|\{|\}|\s", token) or token.startswith(("http", "~")):
            continue
        normalized = token.replace("\\", "/").lstrip("./")
        if normalized.startswith(IGNORED_PREFIXES):
            continue
        if not (ROOT / normalized).exists():
            fail(f"{doc.name}: referenced path does not exist: {token}")

# ---- 3: README numbers against the source docs
results = (ROOT / "docs" / "eval-results.md").read_text(encoding="utf-8")
edge = (ROOT / "docs" / "edge-cases.md").read_text(encoding="utf-8")
problem = (ROOT / "docs" / "problem-statement.md").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")

# (what README states, where it must appear, a substring the source doc must contain)
CLAIMS = [
    ("25/25 questions passed", results, "| Questions passed | 25/25 | 25/25 |"),
    ("retrieval 18/18, outcome 25/25, citations 18/18", results, "| Retrieval / outcome / citations | 18/18, 25/25, 18/18 | 18/18, 25/25, 18/18 |"),
    ("conflicts 8/8 and 0 false", results, "| Conflicts flagged / false notes | 8/8 / 0 | 8/8 / 0 |"),
    ("latency 1978 / 3366 ms", results, "1978 / 3366"),
    ("cost $0.1000", results, "| Cost | $0.0991 | $0.1000 |"),
    ("correct refusals 6/6", results, "| Correct refusals | 6/6 (100%) |"),
    ("baseline 19/20", results, "| Questions passed (all checks) | 19/20 (95%) |"),
    ("tightened 18/20", results, "**18/20 (90%)**"),
    ("2b 20/20", results, "| Questions passed | 18/20 (90%) | 20/20 (100%) | +2 |"),
    ("6e 24/25", results, "**Q1-Q25 under prompt v1 = 24/25**"),
    ("Q5 18/20 vs 20/20", results, "**18/20** (`q5_oldindex_guard_off.json`)"),
    ("Q5 first implementation 0/20", results, "**0/20: all blocked and refused**"),
    ("65 chunks", results, "65 = 65"),
    ("leak scan: was 1 of 25 (Q10), now 0 of 25", results, "scan would block 0 of 25"),
    ("leak scan before: 1 of 25 questions, Q10", results, "**1 of 25** questions"),
    ("conflict note 20/20 in the PTO measurement", results, "| **Answers with a conflict note** | **20/20** |"),
    ("hardening P1 met", results, "| P1 | `leak_scan_blocked` over Q1-Q25 falls from 1 (Q10) to 0 | **0**"),
    ("edge cases 30 PASS 0 FAIL 2 FINDING", edge, "30 PASS, 0 FAIL, 2 FINDING"),
    ("4 restricted vs 4 not-found -> 1 distinct output", edge, "1 distinct output"),
    ("answer accuracy target 80%", problem, ">= 80%"),
    ("time to answer under 10 seconds", problem, "< 10 seconds"),
]
for label, source, needle in CLAIMS:
    if source is None:
        print("NOTE ", label)
        continue
    if needle not in source:
        fail(f"README claim not found in source doc: {label} -> {needle!r}")
    else:
        print("PASS ", label)

for must in ("25/25", "18/18", "8/8", "1,978 ms / 3,366 ms", "$0.1000", "30 PASS, 0 FAIL, 2 documented findings",
             "20 of 20", "docs/screenshots/01-initial-load.webp", "docs/screenshots/02-employee-refused.webp",
             "docs/screenshots/03-exec-answer.webp", "docs/screenshots/04-hr-pto-sources-expanded.webp"):
    if must not in readme:
        fail(f"README is missing expected text: {must}")

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
