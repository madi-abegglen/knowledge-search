"""Unit tests for the Phase 6e fixes (plain asserts, no real API, no embedding model load).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_phase6e_fixes.py

Covers: code-fence stripping, API error mapping, client timeout/retries, the 2,000 char
query cap, multi-error ingest reporting, and the UI error state.
"""

import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
import httpx  # noqa: E402

import app.answer as A  # noqa: E402
from ingest.load import load_documents  # noqa: E402

CHUNK = {
    "source": "hr_pto_policy_2025.md",
    "text": "New hires (0-2 years of service): 20 days per year.",
    "metadata": {"section": "3. Annual PTO allotment", "version": "2025.1", "doc_status": "current"},
    "distance": 0.2,
}
GOOD = {"outcome": "answer", "answer": "New hires get 20 PTO days.",
        "citations": [{"source": "hr_pto_policy_2025.md", "section": "3. Annual PTO allotment"}],
        "conflict_note": None, "clarifying_question": None}


class _Block:
    def __init__(self, text: str) -> None:
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]

        class Usage:
            input_tokens = 100
            output_tokens = 20

        self.usage = Usage()


class FakeAnthropic:
    """Replaces anthropic.Anthropic; records constructor kwargs and call count."""

    def __init__(self, script: list[Any]) -> None:
        self.script, self.calls, self.kwargs = list(script), 0, {}
        outer = self

        class Messages:
            def create(self, **_: Any) -> _Response:
                outer.calls += 1
                item = outer.script.pop(0) if len(outer.script) > 1 else outer.script[0]
                if isinstance(item, Exception):
                    raise item
                return _Response(item)

        self.messages = Messages()

    def factory(self, **kwargs: Any) -> "FakeAnthropic":
        self.kwargs = kwargs
        return self


class Patched:
    def __init__(self, script: list[Any], chunks: list[dict] | None = None) -> None:
        self.fake = FakeAnthropic(script)
        self.chunks = [CHUNK] if chunks is None else chunks
        self.retrieve_calls = 0

    def __enter__(self) -> "Patched":
        self._anthropic, self._retrieve = anthropic.Anthropic, A.retrieve

        def fake_retrieve(user: str, question: str) -> list[dict]:
            self.retrieve_calls += 1
            return self.chunks

        anthropic.Anthropic = self.fake.factory  # type: ignore[assignment]
        A.retrieve = fake_retrieve  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        anthropic.Anthropic, A.retrieve = self._anthropic, self._retrieve  # type: ignore[assignment]


def ask(script: list[Any], question: str = "How many PTO days do new hires get?",
        user: str = "employee") -> tuple[dict[str, Any], Patched]:
    with Patched(script) as patched:
        result = A.answer_question(user, question)
    return result, patched


# ---------------------------------------------------------------- 1. code fences
def test_strip_code_fence_variants() -> None:
    body = json.dumps(GOOD)
    assert A._strip_code_fence(f"```json\n{body}\n```") == body
    assert A._strip_code_fence(f"```\n{body}\n```") == body
    assert A._strip_code_fence(f"```JSON\n{body}\n```") == body
    assert A._strip_code_fence(f"  \n```json\n{body}\n```\n  ") == body
    assert A._strip_code_fence(body) == body                      # unfenced is untouched
    # only ONE leading and ONE trailing fence line are removed
    twice = A._strip_code_fence(f"```json\n```json\n{body}\n```\n```")
    assert twice.startswith("```json") and twice.endswith("```"), twice
    # prose around the fence is not stripped or extracted
    prose = f"Here is the JSON:\n```json\n{body}\n```"
    assert A._strip_code_fence(prose) == prose
    # an unpaired fence (truncated output) is left alone
    assert A._strip_code_fence(f"```json\n{body}") == f"```json\n{body}"
    assert A._strip_code_fence(f"{body}\n```") == f"{body}\n```"


def test_fenced_valid_json_is_accepted() -> None:
    for fence in ("```json\n%s\n```", "```\n%s\n```"):
        result, patched = ask([fence % json.dumps(GOOD)])
        assert result["outcome"] == "answer", result
        assert patched.fake.calls == 1


def test_fenced_but_invalid_json_still_refuses_after_one_retry() -> None:
    result, patched = ask(["```json\n{not valid json}\n```"])
    assert result["outcome"] == "refuse" and result["answer"] == A.REFUSAL_MESSAGE
    assert patched.fake.calls == 2                                # retry-once logic unchanged
    bad_schema = dict(GOOD, outcome="maybe")
    result, patched = ask(["```json\n%s\n```" % json.dumps(bad_schema)])
    assert result["outcome"] == "refuse" and patched.fake.calls == 2


def test_prose_around_json_still_refuses() -> None:
    result, patched = ask(["Sure! Here you go:\n```json\n%s\n```" % json.dumps(GOOD)])
    assert result["outcome"] == "refuse" and patched.fake.calls == 2


def test_fenced_citation_of_unretrieved_source_refuses() -> None:
    bad = dict(GOOD, citations=[{"source": "exec_bonus_structure.md", "section": "1"}])
    result, _ = ask(["```json\n%s\n```" % json.dumps(bad)])
    assert result["outcome"] == "refuse"
    assert "exec_bonus_structure" not in json.dumps(result)


# ---------------------------------------------------------------- 2/3. API errors, timeout
def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_api_errors_map_to_service_unavailable() -> None:
    secret = "SECRET-UPSTREAM-DETAIL-9d3f"
    errors: list[anthropic.APIError] = [
        anthropic.APIConnectionError(message=secret, request=_request()),
        anthropic.APITimeoutError(request=_request()),
        anthropic.InternalServerError(
            secret, response=httpx.Response(500, request=_request()), body=None),
    ]
    results = []
    for error in errors:
        result, patched = ask([error])
        assert result["outcome"] == A.OUTCOME_SERVICE_ERROR, result
        assert result["answer"] == A.SERVICE_UNAVAILABLE_MESSAGE
        assert result["outcome"] not in {"answer", "refuse", "clarify"}   # not a model outcome
        assert result["error_type"] == type(error).__name__
        assert secret not in json.dumps(result)                          # no exception text
        assert result["citations"] == [] and result["conflict_note"] is None
        assert patched.fake.calls == 1
        results.append(result)
    assert len({r["answer"] for r in results}) == 1


def test_service_message_identical_for_every_user_and_query() -> None:
    error = anthropic.APITimeoutError(request=_request())
    seen = set()
    for user, question in (("employee", "What's the exec bonus structure?"),
                           ("hr_user", "What's the salary band for L5?"),
                           ("exec_user", "How many PTO days do new hires get?")):
        result, _ = ask([error], question=question, user=user)
        seen.add((result["outcome"], result["answer"], json.dumps(result["citations"])))
    assert len(seen) == 1, seen


def test_client_has_timeout_and_one_retry() -> None:
    _, patched = ask([json.dumps(GOOD)])
    assert patched.fake.kwargs == {"timeout": 30.0, "max_retries": 1}, patched.fake.kwargs
    assert A.API_TIMEOUT_SECONDS == 30.0 and A.API_MAX_RETRIES == 1


def test_error_on_second_attempt_keeps_token_count() -> None:
    result, patched = ask(["not json", anthropic.APITimeoutError(request=_request())])
    assert result["outcome"] == A.OUTCOME_SERVICE_ERROR
    assert result["input_tokens"] == 100 and patched.fake.calls == 2


# ---------------------------------------------------------------- 4. query cap
def test_query_cap_2000_vs_2001() -> None:
    assert A.MAX_QUERY_CHARS == 2000
    with Patched([json.dumps(GOOD)]) as patched:
        ok = A.answer_question("employee", "a" * 2000)
        assert patched.retrieve_calls == 1                       # 2,000 is allowed
        assert ok["outcome"] == "answer"
    with Patched([json.dumps(GOOD)]) as patched:
        rejected = A.answer_question("employee", "a" * 2001)
        assert patched.retrieve_calls == 0                       # rejected before retrieval
        assert patched.fake.calls == 0
    assert rejected["outcome"] == A.OUTCOME_INVALID_INPUT
    assert rejected["answer"] == A.QUERY_TOO_LONG_MESSAGE == "Please shorten your question (2,000 characters max)."
    assert rejected["input_tokens"] == 0 and rejected["output_tokens"] == 0
    assert "a" * 50 not in json.dumps(rejected)                  # not truncated/echoed


# ---------------------------------------------------------------- 5. ingest error collection
def test_ingest_reports_all_errors_together() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        good = ('---\ntitle: Good\nversion: "1"\neffective_date: 2025-01-01\n'
                "doc_status: current\n---\n\nBody text.\n")
        (folder / "good.md").write_text(good, encoding="utf-8")
        (folder / "empty.md").write_text("", encoding="utf-8")
        (folder / "nofront.md").write_text("just text, no front matter", encoding="utf-8")
        (folder / "orphan.md").write_text(good, encoding="utf-8")        # not in access.json
        (folder / "badgroup.md").write_text(good, encoding="utf-8")
        (folder / "access.json").write_text(json.dumps({"documents": {
            "good.md": {"allowed_groups": ["all"]},
            "empty.md": {"allowed_groups": ["all"]},
            "nofront.md": {"allowed_groups": ["all"]},
            "badgroup.md": {"allowed_groups": ["not_a_group"]},
        }}), encoding="utf-8")
        try:
            load_documents(folder)
        except ValueError as error:
            message = str(error)
        else:
            raise AssertionError("expected ValueError")
        assert message.startswith("4 document(s) failed validation"), message
        for name in ("empty.md", "nofront.md", "orphan.md", "badgroup.md"):
            assert name in message, (name, message)
        assert "good.md:" not in message                          # the good file is not blamed


def test_ingest_single_error_and_success_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        good = ('---\ntitle: Good\nversion: "1"\neffective_date: 2025-01-01\n'
                "doc_status: current\n---\n\nBody text.\n")
        (folder / "good.md").write_text(good, encoding="utf-8")
        (folder / "access.json").write_text(
            json.dumps({"documents": {"good.md": {"allowed_groups": ["all", "hr"]}}}), encoding="utf-8")
        documents = load_documents(folder)
        assert [d.source for d in documents] == ["good.md"]
        assert documents[0].allowed_groups == frozenset({"all", "hr"})
        (folder / "bad.md").write_text("", encoding="utf-8")
        (folder / "access.json").write_text(json.dumps({"documents": {
            "good.md": {"allowed_groups": ["all"]}, "bad.md": {"allowed_groups": ["all"]}}}), encoding="utf-8")
        try:
            load_documents(folder)
        except ValueError as error:
            assert "1 document(s) failed validation" in str(error) and "bad.md" in str(error)
        else:
            raise AssertionError("expected ValueError")


# ---------------------------------------------------------------- UI error state
def test_ui_shows_error_state_not_refused() -> None:
    from streamlit.testing.v1 import AppTest

    import os

    secret = "SECRET-UPSTREAM-DETAIL-9d3f"
    original = A.answer_question
    saved_log_dir = os.environ.get("LOG_DIR")
    log_tmp = tempfile.mkdtemp(prefix="ks_test_logs_")   # keep the real logs/ clean
    os.environ["LOG_DIR"] = log_tmp
    A.answer_question = lambda user, question: {  # type: ignore[assignment]
        "outcome": A.OUTCOME_SERVICE_ERROR, "answer": A.SERVICE_UNAVAILABLE_MESSAGE, "citations": [],
        "conflict_note": None, "clarifying_question": None, "latency_ms": 1.0,
        "input_tokens": 0, "output_tokens": 0, "error_type": secret,
    }
    try:
        at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
        at.text_input[0].set_value("anything")
        next(b for b in at.button if b.label == "Ask").click()
        at.run(timeout=60)
        shown = [m.value for m in at.markdown] + [e.value for e in at.error] + [c.value for c in at.caption]
        assert [e.value for e in at.error] == [A.SERVICE_UNAVAILABLE_MESSAGE], shown
        assert not any("REFUSED" in s or "ANSWER" in s for s in shown), shown
        assert not any(secret in s for s in shown)
        assert not [b for b in at.button if b.key in ("rate_up", "rate_down")]   # no feedback on errors
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
