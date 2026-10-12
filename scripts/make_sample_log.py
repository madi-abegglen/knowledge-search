"""Write a SYNTHETIC query log so infra.report output can be shown without real traffic.

Run from the repo root:
  .\\.venv\\Scripts\\python.exe scripts\\make_sample_log.py <out_dir> healthy|alert

Uses the real log-entry builder (infra.query_log.build_entry), so the lines have the production shape.
Nothing here is real usage; it exists to demonstrate the report and the alert format (see docs/runbook.md).
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infra.query_log import QUERY_LOG_NAME, build_entry  # noqa: E402


def result(outcome: str, latency: float, **extra) -> dict:
    base = {"outcome": outcome, "latency_ms": latency, "input_tokens": 1040, "output_tokens": 190,
            "retrieved_count": 5, "leak_scan_blocked": False, "guard_action": "none", "error_type": None}
    base.update(extra)
    return base


def main(out_dir: str, kind: str) -> int:
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30):
        outcome = "refuse" if i % 6 == 0 else "answer"
        rows.append(result(outcome, 1400.0 + 40 * i))
    if kind == "alert":
        rows[3] = result("refuse", 1500.0, leak_scan_blocked=True)                 # CRITICAL
        rows[10] = result("error", 30.0, error_type="APITimeoutError")
        rows[11] = result("error", 31.0, error_type="APITimeoutError")
        rows[12] = result("error", 29.0, error_type="APITimeoutError")             # 3 in a row
        rows[20] = result("refuse", 1700.0, guard_action="blocked")                # guard warning
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / QUERY_LOG_NAME).open("w", encoding="utf-8") as handle:
        for i, row in enumerate(rows):
            stamp = now - timedelta(minutes=2 * (len(rows) - i))
            entry = build_entry("employee", f"synthetic question {i}", row, now=stamp, model="claude-sonnet-5-5",
                                prompt_ver="1")
            handle.write(json.dumps(entry) + "\n")
    print(f"wrote {len(rows)} synthetic lines to {directory / QUERY_LOG_NAME}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("healthy", "alert"):
        raise SystemExit("usage: make_sample_log.py <out_dir> healthy|alert")
    sys.exit(main(sys.argv[1], sys.argv[2]))
