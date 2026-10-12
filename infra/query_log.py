"""Query log: one JSON line per query in <LOG_DIR>/query_log.jsonl (git-ignored).

Hard Rule 1: a log line NEVER contains restricted document names, pre-filter retrieval
counts, answer text, exception text, or raw question text. The question is represented
only by its length and a short salted sha256 prefix. `retrieved_count` is the number of
chunks that survived the permission filter. A restricted-only query and a not-found
query therefore produce lines with the same shape and the same kind of values.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUERY_LOG_NAME = "query_log.jsonl"
FEEDBACK_NAME = "feedback.jsonl"
HASH_PREFIX_LEN = 12
_TYPE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,60}")


def log_dir() -> Path:
    configured = Path(os.environ.get("LOG_DIR", "./logs"))
    return configured if configured.is_absolute() else ROOT / configured


def question_fingerprint(question: str) -> tuple[int, str]:
    """(length, short salted sha256 prefix). Lets repeats be counted without storing text."""
    salt = os.environ.get("QUERY_LOG_SALT", "")
    normalized = question.strip().casefold()
    digest = hashlib.sha256((salt + normalized).encode("utf-8")).hexdigest()
    return len(question), digest[:HASH_PREFIX_LEN]


def _price(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def compute_cost(input_tokens: int, output_tokens: int) -> float | None:
    """USD cost from PRICE_INPUT_PER_MTOK / PRICE_OUTPUT_PER_MTOK; None if prices are unset."""
    price_in, price_out = _price("PRICE_INPUT_PER_MTOK"), _price("PRICE_OUTPUT_PER_MTOK")
    if price_in is None or price_out is None:
        return None
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def prompt_version() -> str:
    """Version from the front matter of the prompt file currently selected."""
    from app.answer import prompt_path

    try:
        parts = prompt_path().read_text(encoding="utf-8").split("---", 2)
        data = yaml.safe_load(parts[1]) if len(parts) == 3 else None
        return str(data.get("version", "unknown")) if isinstance(data, dict) else "unknown"
    except (OSError, ValueError, yaml.YAMLError):
        return "unknown"


def _safe_error_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _TYPE_NAME.fullmatch(text) else "UnknownError"


def _safe_guard_action(value: Any) -> str:
    return value if value in ("none", "repaired", "blocked") else "none"


def build_entry(
    user: str,
    question: str,
    result: dict[str, Any],
    *,
    query_id: str | None = None,
    now: datetime | None = None,
    model: str | None = None,
    prompt_ver: str | None = None,
) -> dict[str, Any]:
    """Build the log record. Only whitelisted, non-sensitive fields are read from `result`."""
    input_tokens = int(result.get("input_tokens", 0) or 0)
    output_tokens = int(result.get("output_tokens", 0) or 0)
    length, fingerprint = question_fingerprint(question)
    return {
        "id": query_id or uuid.uuid4().hex,
        "timestamp": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "user": user,
        # answer | refuse | clarify (model outcomes), error (service failure), or
        # invalid_input (rejected before retrieval; a fifth logged value, kept out of the
        # refusal rate by infra/report.py).
        "outcome": str(result.get("outcome", "unknown")),
        "latency_ms": float(result.get("latency_ms", 0.0) or 0.0),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": compute_cost(input_tokens, output_tokens),
        "error_type": _safe_error_type(result.get("error_type")),
        "retrieved_count": int(result.get("retrieved_count", 0) or 0),
        "leak_scan_blocked": bool(result.get("leak_scan_blocked", False)),
        # exact-string guard: none | repaired | blocked (never the string itself)
        "guard_action": _safe_guard_action(result.get("guard_action")),
        "prompt_version": prompt_ver if prompt_ver is not None else prompt_version(),
        "model": model if model is not None else os.environ.get("MODEL_NAME", "unset"),
        "question_len": length,
        "question_sha": fingerprint,
    }


def log_query(user: str, question: str, result: dict[str, Any]) -> str | None:
    """Append one line and return its id. Never raises: logging must not break answering."""
    try:
        entry = build_entry(user, question, result)
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / QUERY_LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]
    except Exception:
        return None
