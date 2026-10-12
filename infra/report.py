"""Monitoring report over the query log.

Run from the repo root:  python -m infra.report [--days N] [--log-dir DIR]
Exit code 0 = healthy, 1 = at least one alert (see infra/thresholds.py).
Never prints question text, answer text or document names: the log does not contain them.
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv

from infra.query_log import FEEDBACK_NAME, QUERY_LOG_NAME, ROOT, log_dir
from infra.thresholds import AlertThresholds, load_thresholds

REQUIRED_KEYS = ("id", "timestamp", "outcome", "latency_ms")
# Logged outcomes are answer / refuse / clarify (model outcomes), error (service failure) and
# invalid_input (query rejected before retrieval, e.g. over the length cap). invalid_input is a
# fifth logged value: it is reported as its own count and is NOT part of the refusal rate,
# whose denominator is model outcomes only.
MODEL_OUTCOMES = ("answer", "refuse", "clarify")
INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class Alert:
    severity: str  # CRITICAL | WARNING
    code: str
    message: str


def _parse_ts(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_entries(path: Path, since: datetime) -> tuple[list[dict[str, Any]], int]:
    """Entries at or after `since`, plus the number of malformed lines (skipped)."""
    entries: list[dict[str, Any]] = []
    malformed = 0
    if not path.is_file():
        return entries, malformed
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or any(k not in record for k in REQUIRED_KEYS):
                    raise ValueError("missing keys")
                timestamp = _parse_ts(record["timestamp"])
                float(record["latency_ms"])
            except (ValueError, TypeError):
                malformed += 1
                continue
            if timestamp >= since:
                record["_ts"] = timestamp
                entries.append(record)
    return entries, malformed


def load_ratings(path: Path, since: datetime) -> list[dict[str, Any]]:
    ratings: list[dict[str, Any]] = []
    if not path.is_file():
        return ratings
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
                if record.get("rating") in ("up", "down") and _parse_ts(record["timestamp"]) >= since:
                    ratings.append(record)
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
    return ratings


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def longest_error_run(entries: list[dict[str, Any]]) -> int:
    best = run = 0
    for entry in sorted(entries, key=lambda e: e["_ts"]):
        run = run + 1 if entry["outcome"] == "error" else 0
        best = max(best, run)
    return best


def summarize(entries: list[dict[str, Any]], ratings: list[dict[str, Any]], malformed: int) -> dict[str, Any]:
    total = len(entries)
    outcomes: dict[str, int] = defaultdict(int)
    for entry in entries:
        outcomes[entry["outcome"]] += 1
    handled = sum(outcomes[o] for o in MODEL_OUTCOMES)
    latencies = [float(e["latency_ms"]) for e in entries]
    daily_cost: dict[str, float] = defaultdict(float)
    cost_unknown = 0
    for entry in entries:
        if entry.get("cost_usd") is None:
            cost_unknown += 1
        else:
            daily_cost[entry["_ts"].date().isoformat()] += float(entry["cost_usd"])
    downs = sum(1 for r in ratings if r["rating"] == "down")
    return {
        "queries": total,
        "outcomes": dict(outcomes),
        "refusal_rate": (outcomes["refuse"] / handled) if handled else None,
        "invalid_input": outcomes[INVALID_INPUT],
        "error_rate": (outcomes["error"] / total) if total else None,
        "latency_p50_ms": percentile(latencies, 0.50) if latencies else None,
        "latency_p95_ms": percentile(latencies, 0.95) if latencies else None,
        "input_tokens": sum(int(e.get("input_tokens", 0) or 0) for e in entries),
        "output_tokens": sum(int(e.get("output_tokens", 0) or 0) for e in entries),
        "cost_total_usd": round(sum(daily_cost.values()), 6),
        "cost_unknown_queries": cost_unknown,
        "daily_cost_usd": {day: round(v, 6) for day, v in sorted(daily_cost.items())},
        "ratings": len(ratings),
        "thumbs_down": downs,
        "thumbs_down_rate": (downs / len(ratings)) if ratings else None,
        "leak_scan_blocked": sum(1 for e in entries if e.get("leak_scan_blocked")),
        "longest_error_run": longest_error_run(entries),
        "malformed_lines": malformed,
    }


def evaluate_alerts(summary: dict[str, Any], t: AlertThresholds) -> list[Alert]:
    alerts: list[Alert] = []
    if summary["leak_scan_blocked"] > t.leak_scan_blocked_max:
        alerts.append(Alert("CRITICAL", "LEAK_SCAN_BLOCKED",
                            f"{summary['leak_scan_blocked']} response(s) blocked by the leak scan "
                            f"(allowed: {t.leak_scan_blocked_max}). Treat as a potential permission leak."))
    if summary["longest_error_run"] >= t.consecutive_errors_max:
        alerts.append(Alert("WARNING", "CONSECUTIVE_ERRORS",
                            f"{summary['longest_error_run']} errors in a row (limit {t.consecutive_errors_max})."))
    enough = summary["queries"] >= t.min_queries_for_rate_alerts
    if enough and summary["error_rate"] is not None and summary["error_rate"] > t.error_rate_max:
        alerts.append(Alert("WARNING", "ERROR_RATE",
                            f"error rate {summary['error_rate']:.1%} > {t.error_rate_max:.0%}."))
    if enough and summary["latency_p95_ms"] is not None and summary["latency_p95_ms"] > t.p95_latency_ms_max:
        alerts.append(Alert("WARNING", "P95_LATENCY",
                            f"p95 latency {summary['latency_p95_ms']:.0f} ms > {t.p95_latency_ms_max:.0f} ms."))
    if enough and summary["refusal_rate"] is not None and summary["refusal_rate"] > t.refusal_rate_max:
        alerts.append(Alert("WARNING", "REFUSAL_RATE",
                            f"refusal rate {summary['refusal_rate']:.1%} > {t.refusal_rate_max:.0%}."))
    if t.daily_cost_limit_usd is not None:
        over = {d: c for d, c in summary["daily_cost_usd"].items() if c > t.daily_cost_limit_usd}
        if over:
            days = ", ".join(f"{d} ${c:.4f}" for d, c in over.items())
            alerts.append(Alert("WARNING", "DAILY_COST",
                                f"daily cost over ${t.daily_cost_limit_usd:.2f}: {days}."))
    return alerts


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render(summary: dict[str, Any], alerts: list[Alert], t: AlertThresholds, days: int) -> str:
    lines = [f"Query log report, last {days} day(s)", "-" * 44]
    if summary["queries"] == 0:
        lines.append("no queries in window")
    else:
        mix = ", ".join(f"{k}={v}" for k, v in sorted(summary["outcomes"].items()))
        lines += [
            f"queries:            {summary['queries']}",
            f"outcome mix:        {mix}",
            f"refusal rate:       {_pct(summary['refusal_rate'])} (of answer/refuse/clarify)",
            f"invalid input:      {summary['invalid_input']} (rejected before retrieval; "
            "excluded from refusal rate)",
            f"error rate:         {_pct(summary['error_rate'])}",
            f"latency p50 / p95:  {summary['latency_p50_ms']:.0f} ms / {summary['latency_p95_ms']:.0f} ms",
            f"tokens in / out:    {summary['input_tokens']} / {summary['output_tokens']}",
            f"cost:               ${summary['cost_total_usd']:.4f}"
            + (f" ({summary['cost_unknown_queries']} queries without a price)"
               if summary["cost_unknown_queries"] else ""),
            f"thumbs-down rate:   {_pct(summary['thumbs_down_rate'])} "
            f"({summary['thumbs_down']}/{summary['ratings']} ratings)",
            f"leak_scan_blocked:  {summary['leak_scan_blocked']}",
        ]
    lines.append(f"malformed lines:    {summary['malformed_lines']} (skipped)")
    if t.daily_cost_limit_usd is None:
        lines.append("daily cost limit:   not set (DAILY_COST_LIMIT_USD); cost alert disabled")
    lines.append("")
    if alerts:
        lines.append("ALERTS:")
        lines += [f"  [{a.severity}] {a.code}: {a.message}" for a in alerts]
        lines.append("STATUS: ALERT")
    else:
        lines.append("STATUS: HEALTHY")
    return "\n".join(lines)


def run_report(directory: Path, days: int, thresholds: AlertThresholds,
               now: datetime | None = None) -> tuple[str, int]:
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    entries, malformed = load_entries(directory / QUERY_LOG_NAME, since)
    ratings = load_ratings(directory / FEEDBACK_NAME, since)
    summary = summarize(entries, ratings, malformed)
    alerts = evaluate_alerts(summary, thresholds)
    return render(summary, alerts, thresholds, days), (1 if alerts else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--log-dir", type=Path, default=None)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    text, code = run_report(args.log_dir or log_dir(), args.days, load_thresholds())
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
