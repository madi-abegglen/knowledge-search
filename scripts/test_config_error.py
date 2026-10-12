"""Tests for the misconfiguration error state (missing/blank MODEL_NAME or ANTHROPIC_API_KEY).

No API calls, no embedding model load. Run from the repo root:
  .\\.venv\\Scripts\\python.exe scripts\\test_config_error.py
"""

import contextlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

import app.answer as A  # noqa: E402
from infra.query_log import build_entry  # noqa: E402

SECRET = "SECRET-VALUE-4f1d9c"
VALID_MODEL = "claude-test-model"
VALID_KEY = "sk-test-not-a-real-key"


@contextlib.contextmanager
def settings(**values: str | None) -> Iterator[dict[str, int]]:
    """Set (or delete, with None) env vars; neutralize .env loading; count retrieve/model calls."""
    saved = {k: os.environ.get(k) for k in ("MODEL_NAME", "ANTHROPIC_API_KEY")}
    calls = {"retrieve": 0, "client": 0}
    original_dotenv, original_retrieve, original_client = A.load_dotenv, A.retrieve, anthropic.Anthropic

    def fake_retrieve(user: str, question: str) -> list[dict]:
        calls["retrieve"] += 1
        return []

    def fake_client(**kwargs: Any) -> Any:
        calls["client"] += 1
        raise AssertionError("the model client must not be created")

    A.load_dotenv = lambda *a, **k: None  # type: ignore[assignment]
    A.retrieve = fake_retrieve  # type: ignore[assignment]
    anthropic.Anthropic = fake_client  # type: ignore[assignment]
    for name, value in values.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        yield calls
    finally:
        A.load_dotenv, A.retrieve, anthropic.Anthropic = original_dotenv, original_retrieve, original_client  # type: ignore
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def assert_config_error(result: dict[str, Any], calls: dict[str, int]) -> None:
    assert result["outcome"] == A.OUTCOME_SERVICE_ERROR, result
    assert result["outcome"] not in {"answer", "refuse", "clarify"}
    assert result["answer"] == A.CONFIG_ERROR_MESSAGE == "Service is not configured. Contact the administrator."
    assert result["error_type"] == A.CONFIG_ERROR_TYPE == "ConfigError"
    assert result["citations"] == [] and result["conflict_note"] is None
    assert result["input_tokens"] == 0 and result["output_tokens"] == 0
    assert calls == {"retrieve": 0, "client": 0}, calls          # no retrieval, no model call


def test_each_missing_variable() -> None:
    for missing, other in (("MODEL_NAME", "ANTHROPIC_API_KEY"), ("ANTHROPIC_API_KEY", "MODEL_NAME")):
        values = {other: VALID_MODEL if other == "MODEL_NAME" else VALID_KEY, missing: None}
        with settings(**values) as calls:
            assert_config_error(A.answer_question("employee", "How many PTO days?"), calls)


def test_empty_and_whitespace_only_values() -> None:
    for bad in ("", " ", "   ", "\t", "\n", " \t\n "):
        for name in ("MODEL_NAME", "ANTHROPIC_API_KEY"):
            other = "ANTHROPIC_API_KEY" if name == "MODEL_NAME" else "MODEL_NAME"
            values = {name: bad, other: VALID_KEY if other == "ANTHROPIC_API_KEY" else VALID_MODEL}
            with settings(**values) as calls:
                assert_config_error(A.answer_question("employee", "q"), calls)


def test_both_missing() -> None:
    with settings(MODEL_NAME=None, ANTHROPIC_API_KEY=None) as calls:
        assert_config_error(A.answer_question("employee", "q"), calls)
        assert A.check_config() == "ConfigError"


def test_valid_configuration_is_not_a_config_error() -> None:
    with settings(MODEL_NAME=VALID_MODEL, ANTHROPIC_API_KEY=VALID_KEY) as calls:
        assert A.check_config() is None
        result = A.answer_question("employee", "How many PTO days?")   # fake retrieve returns no chunks
        assert result["outcome"] == "refuse" and calls["retrieve"] == 1


def test_identical_for_every_user_and_query() -> None:
    seen = set()
    with settings(MODEL_NAME=None, ANTHROPIC_API_KEY=VALID_KEY) as calls:
        for user, question in (("employee", "What's the exec bonus structure?"), ("hr_user", "salary band L5?"),
                               ("exec_user", "How many PTO days?"), ("nobody", "anything"),
                               ("employee", ""), ("employee", "   "), ("employee", "x" * 5000)):
            result = A.answer_question(user, question)
            seen.add((result["outcome"], result["answer"], result["error_type"],
                      json.dumps(result["citations"]), result["input_tokens"]))
        assert calls == {"retrieve": 0, "client": 0}
    assert len(seen) == 1, seen


def test_names_and_values_are_never_shown_or_logged() -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture()
    A.logger.addHandler(handler)
    try:
        with settings(MODEL_NAME=None, ANTHROPIC_API_KEY=SECRET) as calls:
            result = A.answer_question("employee", "q")
            assert_config_error(result, calls)
        with settings(MODEL_NAME=SECRET, ANTHROPIC_API_KEY="   ") as calls:
            result2 = A.answer_question("employee", "q")
            assert_config_error(result2, calls)
    finally:
        A.logger.removeHandler(handler)
    blob = json.dumps([result, result2]) + "\n".join(records)
    for forbidden in (SECRET, "MODEL_NAME", "ANTHROPIC_API_KEY", "API_KEY", "env"):
        assert forbidden not in blob, forbidden
    assert records and all(r == "answer service error: ConfigError" for r in records), records


def test_query_log_line_carries_only_the_error_type() -> None:
    with settings(MODEL_NAME=None, ANTHROPIC_API_KEY=SECRET) as calls:
        result = A.answer_question("employee", "How many PTO days do new hires get?")
    entry = build_entry("employee", "How many PTO days do new hires get?", result,
                        now=None, model="m", prompt_ver="1")
    line = json.dumps(entry)
    assert entry["outcome"] == "error" and entry["error_type"] == "ConfigError"
    assert entry["retrieved_count"] == 0 and entry["input_tokens"] == 0
    for forbidden in (SECRET, "MODEL_NAME", "ANTHROPIC_API_KEY", "PTO days", A.CONFIG_ERROR_MESSAGE):
        assert forbidden not in line, forbidden


def _ui_text(at: Any) -> dict[str, list[str]]:
    return {
        "error": [e.value for e in at.error],
        "markdown": [m.value for m in at.markdown],
        "text_inputs": [t.label for t in at.text_input],
        "buttons": [b.label for b in at.button],
    }


def test_ui_startup_shows_the_config_error_and_no_search_box() -> None:
    from streamlit.testing.v1 import AppTest

    saved_log_dir = os.environ.get("LOG_DIR")
    os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="ks_cfg_logs_")
    try:
        with settings(MODEL_NAME=None, ANTHROPIC_API_KEY=VALID_KEY):
            at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
            ui = _ui_text(at)
        assert not at.exception, [e.value for e in at.exception]
        assert ui["error"] == [A.CONFIG_ERROR_MESSAGE], ui
        assert ui["text_inputs"] == [] and "Ask" not in ui["buttons"], ui
        shown = json.dumps(ui)
        for forbidden in ("MODEL_NAME", "ANTHROPIC_API_KEY", "REFUSED", "Service unavailable"):
            assert forbidden not in shown, forbidden
        with settings(MODEL_NAME=VALID_MODEL, ANTHROPIC_API_KEY=VALID_KEY):
            at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
            assert at.text_input and not at.error, _ui_text(at)      # normal page when configured
    finally:
        if saved_log_dir is None:
            os.environ.pop("LOG_DIR", None)
        else:
            os.environ["LOG_DIR"] = saved_log_dir


def test_ui_query_time_config_error_is_not_shown_as_refused_or_unavailable() -> None:
    from streamlit.testing.v1 import AppTest

    saved_log_dir = os.environ.get("LOG_DIR")
    os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="ks_cfg_logs_")
    original = A.answer_question
    A.answer_question = lambda user, question: {  # type: ignore[assignment]
        "outcome": A.OUTCOME_SERVICE_ERROR, "answer": A.CONFIG_ERROR_MESSAGE, "citations": [],
        "conflict_note": None, "clarifying_question": None, "latency_ms": 1.0,
        "input_tokens": 0, "output_tokens": 0, "error_type": A.CONFIG_ERROR_TYPE,
        "retrieved_count": 0, "leak_scan_blocked": False, "guard_action": "none",
    }
    try:
        with settings(MODEL_NAME=VALID_MODEL, ANTHROPIC_API_KEY=VALID_KEY):     # startup check passes
            at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
            at.text_input[0].set_value("anything")
            next(b for b in at.button if b.label == "Ask").click()
            at.run(timeout=60)
            ui = _ui_text(at)
        assert ui["error"] == [A.CONFIG_ERROR_MESSAGE], ui
        shown = json.dumps(ui)
        assert "REFUSED" not in shown and "Service unavailable" not in shown
        assert not [b for b in at.button if b.key in ("rate_up", "rate_down")]
    finally:
        A.answer_question = original  # type: ignore[assignment]
        if saved_log_dir is None:
            os.environ.pop("LOG_DIR", None)
        else:
            os.environ["LOG_DIR"] = saved_log_dir


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
