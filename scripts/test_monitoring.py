"""Tests for the query log and report (fabricated log files, no API, no embedding model).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_monitoring.py
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import infra.query_log as QL  # noqa: E402
from infra.report import evaluate_alerts, load_entries, run_report, summarize  # noqa: E402
from infra.thresholds import AlertThresholds  # noqa: E402

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
DEFAULT = AlertThresholds()


def entry(minutes_ago: int = 5, outcome: str = "answer", latency: float = 1500.0, cost: float | None = 0.004,
          leak: bool = False, error_type: str | None = None, user: str = "employee") -> dict[str, Any]:
    return {
        "id": f"id{minutes_ago}{outcome}{latency}",
        "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds"),
        "user": user, "outcome": outcome, "latency_ms": latency,
        "input_tokens": 1000, "output_tokens": 150, "cost_usd": cost, "error_type": error_type,
        "retrieved_count": 5, "leak_scan_blocked": leak, "prompt_version": "1",
        "model": "claude-sonnet-5-5", "question_len": 30, "question_sha": "abc123def456",
    }


def write_log(directory: Path, entries: list[Any], raw_extra: list[str] | None = None) -> None:
    lines = [e if isinstance(e, str) else json.dumps(e) for e in entries] + (raw_extra or [])
    (directory / QL.QUERY_LOG_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def report(entries: list[Any], thresholds: AlertThresholds = DEFAULT, raw_extra: list[str] | None = None,
           feedback: list[dict] | None = None) -> tuple[str, int]:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        write_log(directory, entries, raw_extra)
        if feedback is not None:
            (directory / QL.FEEDBACK_NAME).write_text(
                "\n".join(json.dumps(f) for f in feedback) + "\n", encoding="utf-8")
        return run_report(directory, 7, thresholds, now=NOW)


def healthy_set(n: int = 25) -> list[dict[str, Any]]:
    # 20% refusals, no errors, fast
    return [entry(minutes_ago=i + 1, outcome="refuse" if i % 5 == 0 else "answer", latency=1000 + i * 20)
            for i in range(n)]


# ---------------------------------------------------------------- report
def test_healthy() -> None:
    text, code = report(healthy_set())
    assert code == 0 and "STATUS: HEALTHY" in text, text
    assert "queries:            25" in text


def test_leak_scan_blocked_is_critical_even_with_tiny_sample() -> None:
    text, code = report([entry(leak=True)])
    assert code == 1 and "[CRITICAL] LEAK_SCAN_BLOCKED" in text, text


def test_error_rate_alert() -> None:
    entries = healthy_set(20)
    for i in (0, 7, 14):   # 3 of 20 = 15%, not consecutive
        entries[i] = entry(minutes_ago=i + 1, outcome="error", error_type="APITimeoutError")
    text, code = report(entries)
    assert code == 1 and "ERROR_RATE" in text and "CONSECUTIVE_ERRORS" not in text, text


def test_consecutive_errors_alert_fires_on_small_sample() -> None:
    entries = [entry(minutes_ago=m, outcome="error", error_type="APIConnectionError") for m in (1, 2, 3)]
    text, code = report(entries)
    assert code == 1 and "CONSECUTIVE_ERRORS" in text and "ERROR_RATE" not in text, text   # n < 20


def test_p95_latency_alert() -> None:
    entries = healthy_set(20)
    for i in range(3):     # 3 of 20 slow -> p95 above 10 s
        entries[i] = entry(minutes_ago=i + 1, latency=15000.0)
    text, code = report(entries)
    assert code == 1 and "P95_LATENCY" in text, text


def test_refusal_rate_alert() -> None:
    entries = [entry(minutes_ago=i + 1, outcome="refuse" if i < 10 else "answer") for i in range(20)]  # 50%
    text, code = report(entries)
    assert code == 1 and "REFUSAL_RATE" in text, text
    just_under = [entry(minutes_ago=i + 1, outcome="refuse" if i < 8 else "answer") for i in range(20)]  # 40%
    _, code = report(just_under)
    assert code == 0                                                       # threshold is strictly above 40%


def test_rate_alerts_need_minimum_sample() -> None:
    entries = [entry(minutes_ago=i + 1, outcome="refuse") for i in range(5)]   # 100% refusals, n=5
    text, code = report(entries)
    assert code == 0 and "REFUSAL_RATE" not in text, text


def test_daily_cost_alert_and_disabled_when_unset() -> None:
    entries = [entry(minutes_ago=i + 1, cost=0.50) for i in range(4)]            # $2.00 today
    text, code = report(entries, AlertThresholds(daily_cost_limit_usd=1.0))
    assert code == 1 and "DAILY_COST" in text, text
    text, code = report(entries, AlertThresholds(daily_cost_limit_usd=None))
    assert code == 0 and "cost alert disabled" in text, text
    text, code = report(entries, AlertThresholds(daily_cost_limit_usd=5.0))
    assert code == 0


def test_empty_log_and_missing_log() -> None:
    text, code = report([])
    assert code == 0 and "no queries in window" in text, text
    with tempfile.TemporaryDirectory() as tmp:
        text, code = run_report(Path(tmp), 7, DEFAULT, now=NOW)       # no file at all
    assert code == 0 and "no queries in window" in text


def test_malformed_lines_are_skipped_and_counted() -> None:
    bad = ["{not json", '{"id": "x"}', "[1, 2, 3]", '{"id":"y","timestamp":"nope","outcome":"answer","latency_ms":1}']
    text, code = report(healthy_set(25), raw_extra=bad)
    assert code == 0 and "malformed lines:    4 (skipped)" in text, text
    assert "queries:            25" in text


def test_window_excludes_old_entries() -> None:
    entries = healthy_set(25) + [entry(minutes_ago=60 * 24 * 30, leak=True)]   # 30 days old
    text, code = report(entries)
    assert code == 0 and "queries:            25" in text, text


def test_thumbs_down_rate_and_linking() -> None:
    entries = healthy_set(25)
    feedback = [
        {"timestamp": NOW.isoformat(), "query_id": entries[0]["id"], "rating": "down"},
        {"timestamp": NOW.isoformat(), "query_id": entries[1]["id"], "rating": "up"},
        {"timestamp": NOW.isoformat(), "query_id": entries[2]["id"], "rating": "up"},
        {"timestamp": NOW.isoformat(), "query_id": entries[3]["id"], "rating": "up"},
    ]
    text, _ = report(entries, feedback=feedback)
    assert "thumbs-down rate:   25.0% (1/4 ratings)" in text, text


def test_report_output_has_no_question_or_answer_text() -> None:
    text, _ = report(healthy_set())
    for forbidden in ("exec_bonus_structure", "How many", "I couldn't find"):
        assert forbidden not in text


# ---------------------------------------------------------------- log entries
def result(outcome: str, answer: str, **extra: Any) -> dict[str, Any]:
    base = {"outcome": outcome, "answer": answer, "citations": [], "conflict_note": None,
            "clarifying_question": None, "latency_ms": 1234.5, "input_tokens": 950, "output_tokens": 80,
            "retrieved_count": 5, "leak_scan_blocked": False}
    base.update(extra)
    return base


def test_restricted_only_and_not_found_produce_identical_log_shape() -> None:
    fixed = dict(now=NOW, model="m", prompt_ver="1")
    restricted = QL.build_entry("employee", "What's the exec bonus structure?",
                                result("refuse", "I couldn't find that."), query_id="A", **fixed)
    not_found = QL.build_entry("employee", "How do I book a company jet?",
                               result("refuse", "I couldn't find that."), query_id="B", **fixed)
    assert list(restricted) == list(not_found)
    variable = {"id", "question_len", "question_sha"}
    for key in restricted:
        if key not in variable:
            assert restricted[key] == not_found[key], key
    assert {k: type(v) for k, v in restricted.items()} == {k: type(v) for k, v in not_found.items()}


def test_log_line_never_contains_forbidden_content() -> None:
    secret_text = "SECRET-EXCEPTION-TEXT-77"
    leaky = result(
        "error", "exec_bonus_structure.md says the CEO gets 100% of base",
        error_type=f"APITimeoutError: {secret_text}",           # not a clean type name
        citations=[{"source": "hr_salary_bands.md", "section": "L5"}],
        conflict_note="superseded hr_pto_policy_2023.md says 15",
        prefilter_count=17, restricted_hits=["exec_bonus_structure.md"],   # extra keys must be ignored
    )
    question = "What's the exec bonus structure for the CEO?"
    line = json.dumps(QL.build_entry("employee", question, leaky, now=NOW, model="m", prompt_ver="1"))
    for forbidden in ("exec_bonus_structure", "hr_salary_bands", "hr_pto_policy_2023", "100% of base",
                      secret_text, "prefilter", "restricted_hits", "CEO", "bonus structure", "L5"):
        assert forbidden not in line, forbidden
    entry_ = json.loads(line)
    assert entry_["error_type"] == "UnknownError"
    assert entry_["question_len"] == len(question) and len(entry_["question_sha"]) == QL.HASH_PREFIX_LEN
    assert set(entry_) == {"id", "timestamp", "user", "outcome", "latency_ms", "input_tokens", "output_tokens",
                           "cost_usd", "error_type", "retrieved_count", "leak_scan_blocked", "guard_action",
                           "prompt_version", "model", "question_len", "question_sha"}
    assert entry_["guard_action"] == "none"


def test_cost_from_env_prices_and_none_when_unset() -> None:
    saved = {k: os.environ.get(k) for k in ("PRICE_INPUT_PER_MTOK", "PRICE_OUTPUT_PER_MTOK")}
    try:
        os.environ["PRICE_INPUT_PER_MTOK"], os.environ["PRICE_OUTPUT_PER_MTOK"] = "2.00", "10.00"
        assert QL.compute_cost(1_000_000, 100_000) == 3.0
        os.environ["PRICE_INPUT_PER_MTOK"] = ""
        assert QL.compute_cost(1000, 100) is None
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_log_query_writes_one_line_per_call_in_log_dir() -> None:
    saved = os.environ.get("LOG_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOG_DIR"] = tmp
        try:
            ids = [QL.log_query("employee", f"question {i}", result("answer", "text")) for i in range(3)]
        finally:
            if saved is None:
                os.environ.pop("LOG_DIR", None)
            else:
                os.environ["LOG_DIR"] = saved
        lines = (Path(tmp) / QL.QUERY_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and len(set(ids)) == 3 and all(ids)
    assert [json.loads(l)["id"] for l in lines] == ids
    assert "question 1" not in "\n".join(lines)


def test_log_query_never_raises() -> None:
    saved = os.environ.get("LOG_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "file"
        blocker.write_text("x", encoding="utf-8")
        os.environ["LOG_DIR"] = str(blocker / "sub")     # cannot be created: parent is a file
        try:
            assert QL.log_query("employee", "q", result("answer", "t")) is None
        finally:
            if saved is None:
                os.environ.pop("LOG_DIR", None)
            else:
                os.environ["LOG_DIR"] = saved


def test_rating_is_linked_to_the_query_id_through_the_ui() -> None:
    import app.answer as A
    from streamlit.testing.v1 import AppTest

    original = A.answer_question
    A.answer_question = lambda user, question: result("answer", "20 days", citations=[  # type: ignore
        {"source": "hr_pto_policy_2025.md", "section": "3. Annual PTO allotment"}])
    saved = os.environ.get("LOG_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOG_DIR"] = tmp
        try:
            at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
            at.text_input[0].set_value("How many PTO days do new hires get?")
            next(b for b in at.button if b.label == "Ask").click()
            at.run(timeout=60)
            next(b for b in at.button if b.key == "rate_up").click()
            at.run(timeout=60)
            logged = json.loads((Path(tmp) / QL.QUERY_LOG_NAME).read_text(encoding="utf-8").splitlines()[-1])
            rated = json.loads((Path(tmp) / QL.FEEDBACK_NAME).read_text(encoding="utf-8").splitlines()[-1])
        finally:
            A.answer_question = original  # type: ignore[assignment]
            if saved is None:
                os.environ.pop("LOG_DIR", None)
            else:
                os.environ["LOG_DIR"] = saved
    assert rated["query_id"] == logged["id"] and rated["rating"] == "up"
    assert "PTO days" not in json.dumps(logged)


def _rate_once(rating: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import app.answer as A
    from streamlit.testing.v1 import AppTest

    original = A.answer_question
    A.answer_question = lambda user, question: result("answer", "20 days", citations=[  # type: ignore
        {"source": "hr_pto_policy_2025.md", "section": "3. Annual PTO allotment"}])
    saved = os.environ.get("LOG_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOG_DIR"] = tmp
        try:
            at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
            at.text_input[0].set_value("How many PTO days do new hires get?")
            next(b for b in at.button if b.label == "Ask").click()
            at.run(timeout=60)
            next(b for b in at.button if b.key == f"rate_{rating}").click()
            at.run(timeout=60)
            logged = json.loads((Path(tmp) / QL.QUERY_LOG_NAME).read_text(encoding="utf-8").splitlines()[-1])
            rated = json.loads((Path(tmp) / QL.FEEDBACK_NAME).read_text(encoding="utf-8").splitlines()[-1])
        finally:
            A.answer_question = original  # type: ignore[assignment]
            if saved is None:
                os.environ.pop("LOG_DIR", None)
            else:
                os.environ["LOG_DIR"] = saved
    return logged, rated


def test_feedback_stores_question_text_only_for_thumbs_down() -> None:
    question = "How many PTO days do new hires get?"
    logged, up = _rate_once("up")
    assert up["rating"] == "up" and "question" not in up, up
    assert question not in json.dumps(up)
    assert up["query_id"] == logged["id"]
    logged, down = _rate_once("down")
    assert down["rating"] == "down" and down["question"] == question, down
    assert down["query_id"] == logged["id"]
    for record in (up, down):                      # never answer text, even on thumbs-down
        assert "20 days" not in json.dumps(record)
    assert question not in json.dumps(logged)      # the query log never has the text


def test_invalid_input_is_its_own_count_and_not_in_refusal_rate() -> None:
    entries = [entry(minutes_ago=i + 1, outcome="answer") for i in range(15)]
    entries += [entry(minutes_ago=30 + i, outcome="refuse") for i in range(5)]       # 5/20 = 25%
    entries += [entry(minutes_ago=60 + i, outcome="invalid_input", latency=1.0) for i in range(10)]
    text, code = report(entries)
    assert "invalid input:      10" in text and "excluded from refusal rate" in text, text
    assert "refusal rate:       25.0%" in text, text    # 10 invalid_input rows not in the denominator
    assert code == 0
    with tempfile.TemporaryDirectory() as tmp:
        write_log(Path(tmp), entries)
        loaded, _ = load_entries(Path(tmp) / QL.QUERY_LOG_NAME, NOW - timedelta(days=7))
    summary = summarize(loaded, [], 0)
    assert summary["invalid_input"] == 10 and summary["refusal_rate"] == 0.25


def _with_guard(e: dict[str, Any], action: str) -> dict[str, Any]:
    return dict(e, guard_action=action)


def test_guard_counts_are_shown_and_old_lines_count_as_none() -> None:
    entries = healthy_set(25)                                      # no guard_action key at all (old lines)
    entries[0] = _with_guard(entries[0], "repaired")
    entries[1] = _with_guard(entries[1], "repaired")
    text, code = report(entries)
    assert "exact-string guard: repaired=2, blocked=0" in text, text
    assert code == 0 and "GUARD_BLOCKED" not in text


def test_rising_guard_blocked_is_a_warning_not_critical() -> None:
    day = 60 * 24
    prior = [_with_guard(entry(minutes_ago=8 * day + i), "blocked") for i in range(1)]     # 1 in previous window
    now_window = healthy_set(25)
    for i in range(3):                                                                   # 3 in this window
        now_window[i] = _with_guard(now_window[i], "blocked")
    text, code = report(now_window + prior)
    assert code == 1 and "[WARNING] GUARD_BLOCKED_RISING" in text, text
    assert "CRITICAL" not in text and "blocked=3 (previous window blocked=1)" in text, text


def test_stable_or_zero_guard_blocked_does_not_alert() -> None:
    day = 60 * 24
    prior = [_with_guard(entry(minutes_ago=8 * day + i), "blocked") for i in range(2)]
    window = healthy_set(25)
    for i in range(2):                                                                   # 2 now, 2 before
        window[i] = _with_guard(window[i], "blocked")
    text, code = report(window + prior)
    assert code == 0 and "GUARD_BLOCKED" not in text, text
    text, code = report(healthy_set(25) + prior)                                         # 0 now
    assert code == 0 and "GUARD_BLOCKED" not in text


def test_guard_action_in_log_is_whitelisted_to_three_values() -> None:
    for given, expected in (("repaired", "repaired"), ("blocked", "blocked"), ("none", "none"),
                            ("helpdesk@northwind.example", "none"), (None, "none")):
        line = QL.build_entry("employee", "q", result("answer", "t", guard_action=given),
                              now=NOW, model="m", prompt_ver="1")
        assert line["guard_action"] == expected, (given, line["guard_action"])
        assert "helpdesk" not in json.dumps(line)


def test_answer_question_reports_retrieved_count_and_leak_flag() -> None:
    import app.answer as A

    chunk = {"source": "hr_pto_policy_2025.md", "text": "20 days per year.",
             "metadata": {"section": "3", "version": "2025.1", "doc_status": "current"}, "distance": 0.2}
    original = A.retrieve
    A.retrieve = lambda user, question: []  # type: ignore[assignment]
    try:
        empty = A.answer_question("employee", "anything")
        assert empty["retrieved_count"] == 0 and empty["leak_scan_blocked"] is False
        too_long = A.answer_question("employee", "x" * 2001)
        assert too_long["retrieved_count"] == 0
        A.retrieve = lambda user, question: [chunk] * 3  # type: ignore[assignment]
        saved = A.anthropic.Anthropic

        class _Resp:
            class usage:
                input_tokens, output_tokens = 10, 5

            content = [type("B", (), {"type": "text", "text": json.dumps({
                "outcome": "answer", "answer": "See exec_bonus_structure.md for details.",
                "citations": [{"source": "hr_pto_policy_2025.md", "section": "3"}],
                "conflict_note": None, "clarifying_question": None})})()]

        fake = type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: _Resp())})()})
        A.anthropic.Anthropic = lambda **k: fake  # type: ignore[assignment]
        try:
            blocked = A.answer_question("employee", "What is the PTO policy?")
        finally:
            A.anthropic.Anthropic = saved  # type: ignore[assignment]
        assert blocked["outcome"] == "refuse" and blocked["leak_scan_blocked"] is True
        assert blocked["retrieved_count"] == 3
        assert "exec_bonus_structure" not in json.dumps(blocked)
    finally:
        A.retrieve = original  # type: ignore[assignment]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
