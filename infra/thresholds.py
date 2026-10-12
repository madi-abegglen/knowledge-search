"""Alert thresholds for monitoring. The ONLY place thresholds are defined.

Refusal-rate threshold (0.40), justified from the eval data:
- The 25-question eval set refuses 6 of 25 (24%) by design (5 restricted / not-in-docs
  questions plus one injection attempt), i.e. an adversarial-heavy sample.
- Real traffic should refuse less often than this set, so 24% is an upper-ish baseline.
- The build plan's 30% is only 6 points above it and would fire on a normal day with a
  handful of unanswerable questions; 40% is about 1.7x the baseline.
- Rate alerts only evaluate once `min_queries_for_rate_alerts` queries are in the window.
"""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AlertThresholds:
    leak_scan_blocked_max: int = 0            # any value above this is CRITICAL
    error_rate_max: float = 0.05              # errors / all queries
    p95_latency_ms_max: float = 10_000.0      # our target
    refusal_rate_max: float = 0.40            # refuse / (answer + refuse + clarify)
    min_queries_for_rate_alerts: int = 20     # avoid alerting on tiny samples
    consecutive_errors_max: int = 3           # alert at this many errors in a row
    daily_cost_limit_usd: float | None = None  # from DAILY_COST_LIMIT_USD in .env


def load_thresholds() -> AlertThresholds:
    raw = os.environ.get("DAILY_COST_LIMIT_USD", "").strip()
    try:
        limit = float(raw) if raw else None
    except ValueError:
        limit = None
    return AlertThresholds(daily_cost_limit_usd=limit)
