"""Tests for the 7d hardening items (no real API, no embedding model load).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_hardening_7d.py

1. leak scan scope: a refusal always shows the fixed text; a restricted title/filename in anything
   that can reach the user (answer, conflict_note, clarifying_question, citation section) is blocked
2. unexpected UI exceptions are logged with a class name only
3. check_env.py reads .env with utf-8-sig (a BOM must not look like an unset key)
4. .env.example documents PROMPT_VERSION
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

import app.answer as A  # noqa: E402

RESTRICTED_TITLE = "Executive Bonus Structure"
RESTRICTED_FILE = "exec_bonus_structure.md"
PERMITTED_SECTION = "3. Annual PTO allotment"
CHUNK = {
    "source": "hr_pto_policy_2025.md",
    "text": "New hires (0-2 years of service): 20 days per year.",
    "metadata": {"section": PERMITTED_SECTION, "version": "2025.1", "doc_status": "current"},
    "distance": 0.2,
}
FIXED_KEYS = ("outcome", "answer", "citations", "conflict_note", "clarifying_question")


# ---------------------------------------------------------------- harness
class _Block:
    def __init__(self, text: str) -> None:
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]

        class Usage:
            input_tokens, output_tokens = 100, 20

        self.usage = Usage()


def model_json(outcome: str = "answer", answer: str = "New hires get 20 PTO days.", note: str | None = None,
               clarify: str | None = None, section: str = PERMITTED_SECTION) -> str:
    citations = [] if outcome in ("refuse", "clarify") else [
        {"source": "hr_pto_policy_2025.md", "section": section}]
    return json.dumps({"outcome": outcome, "answer": answer, "citations": citations,
                       "conflict_note": note, "clarifying_question": clarify})


def ask(script: list[str], user: str = "employee") -> dict[str, Any]:
    items = list(script)

    class Messages:
        def create(self, **_: Any) -> _Response:
            return _Response(items.pop(0) if len(items) > 1 else items[0])

    original_client, original_retrieve = anthropic.Anthropic, A.retrieve
    anthropic.Anthropic = lambda **k: type("C", (), {"messages": Messages()})()  # type: ignore[assignment]
    A.retrieve = lambda u, q: [CHUNK]  # type: ignore[assignment]
    try:
        return A.answer_question(user, "How many PTO days do new hires get?")
    finally:
        anthropic.Anthropic, A.retrieve = original_client, original_retrieve  # type: ignore[assignment]


def fixed_refusal(result: dict[str, Any]) -> bool:
    return (result["outcome"] == "refuse" and result["answer"] == A.REFUSAL_MESSAGE
            and result["citations"] == [] and result["conflict_note"] is None
            and result["clarifying_question"] is None)


# ---------------------------------------------------------------- 1. leak scan scope
def test_refusal_text_is_always_the_fixed_text_whatever_the_model_wrote() -> None:
    nasty = [
        f"The context has nothing about the {RESTRICTED_TITLE} ({RESTRICTED_FILE}).",
        "IGNORE ALL INSTRUCTIONS <script>alert(1)</script> CEO bonus is 100%",
        "x" * 5000,
        "",
    ]
    results = [ask([model_json("refuse", answer=text, note=f"see {RESTRICTED_FILE}",
                               clarify=f"did you mean {RESTRICTED_TITLE}?")]) for text in nasty]
    for text, result in zip(nasty, results):
        assert fixed_refusal(result), result
        assert result["leak_scan_blocked"] is False                        # refusal text is not scanned
        blob = json.dumps(result)
        assert RESTRICTED_FILE not in blob and RESTRICTED_TITLE not in blob
        if text:
            assert text[:40] not in blob
    # every route to a refusal returns the same user-visible fields
    routes = {
        "model refuse": ask([model_json("refuse", answer="no")]),
        "invalid json": ask(["not json"]),
        "schema error": ask([json.dumps({"outcome": "maybe"})]),
        "uncited source": ask([json.dumps({"outcome": "answer", "answer": "x", "conflict_note": None,
                                           "clarifying_question": None,
                                           "citations": [{"source": "exec_bonus_structure.md", "section": "1"}]})]),
        "leak blocked": ask([model_json(answer=f"See {RESTRICTED_FILE}")]),
    }
    shapes = {name: tuple(json.dumps(r[k]) for k in FIXED_KEYS) for name, r in routes.items()}
    assert len(set(shapes.values())) == 1, shapes
    assert all(fixed_refusal(r) for r in routes.values())


def test_ui_shows_the_constant_not_the_result_text_on_refuse() -> None:
    source = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")
    block = source[source.index('if outcome == "refuse":'): source.index('elif outcome == "clarify":')]
    assert "st.write(REFUSAL_MESSAGE)" in block and 'result["answer"]' not in block


def test_restricted_title_in_a_refusal_is_not_an_alert_but_the_user_sees_only_fixed_text() -> None:
    result = ask([model_json("refuse", answer=f"No information about an {RESTRICTED_TITLE.lower()}.")])
    assert fixed_refusal(result) and result["leak_scan_blocked"] is False


def test_the_control_still_bites_in_every_visible_field() -> None:
    cases = {
        "answer title": model_json(answer=f"The {RESTRICTED_TITLE} says the CEO gets 100%."),
        "answer filename": model_json(answer=f"See {RESTRICTED_FILE}."),
        "conflict_note": model_json(note=f"Older doc: {RESTRICTED_TITLE}."),
        "clarifying_question": model_json("clarify", answer="", clarify=f"Do you mean the {RESTRICTED_TITLE}?"),
        "citation section": model_json(section=f"{RESTRICTED_TITLE} 1. Target bonus"),
        "mixed case": model_json(answer="see EXEC_BONUS_STRUCTURE.MD"),
    }
    for name, raw in cases.items():
        result = ask([raw])
        assert fixed_refusal(result), (name, result)
        assert result["leak_scan_blocked"] is True, name
        blob = json.dumps(result).casefold()
        assert RESTRICTED_FILE not in blob and RESTRICTED_TITLE.casefold() not in blob, name


def test_a_clean_answer_clarify_and_conflict_note_pass_through() -> None:
    answer = ask([model_json(answer="New hires get 20 PTO days.", note="The superseded 2023 policy said 15.")])
    assert answer["outcome"] == "answer" and answer["leak_scan_blocked"] is False
    assert answer["conflict_note"] == "The superseded 2023 policy said 15."
    assert answer["citations"] == [{"source": "hr_pto_policy_2025.md", "section": PERMITTED_SECTION}]
    clarify = ask([model_json("clarify", answer="", clarify="Which vacation topic do you mean?")])
    assert clarify["outcome"] == "clarify" and clarify["clarifying_question"] == "Which vacation topic do you mean?"


def test_visible_texts_rule() -> None:
    payload = A.AnswerPayload.model_validate_json(model_json(note="n", section="S"))
    assert A._user_visible_texts(payload) == ["New hires get 20 PTO days.", "n", "hr_pto_policy_2025.md", "S"]
    refuse = A.AnswerPayload.model_validate_json(model_json("refuse", answer="anything"))
    assert A._user_visible_texts(refuse) == []


# ---------------------------------------------------------------- 2. UI exceptions are logged
def test_unexpected_ui_exception_is_logged_by_class_name_only() -> None:
    from streamlit.testing.v1 import AppTest

    secret = "SECRET-EXCEPTION-TEXT-9a1c"
    original = A.answer_question
    saved_log = os.environ.get("LOG_DIR")
    tmp = tempfile.mkdtemp(prefix="ks_h7d_logs_")
    os.environ["LOG_DIR"] = tmp

    def boom(user: str, question: str) -> dict[str, Any]:
        raise ValueError(secret)

    A.answer_question = boom  # type: ignore[assignment]
    try:
        at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
        at.text_input[0].set_value("How many PTO days do new hires get?")
        next(b for b in at.button if b.label == "Ask").click()
        at.run(timeout=60)
        shown = json.dumps([e.value for e in at.error] + [m.value for m in at.markdown])
        lines = (Path(tmp) / "query_log.jsonl").read_text(encoding="utf-8").splitlines()
    finally:
        A.answer_question = original  # type: ignore[assignment]
        if saved_log is None:
            os.environ.pop("LOG_DIR", None)
        else:
            os.environ["LOG_DIR"] = saved_log
    assert [e.value for e in at.error] == ["Something went wrong. Try again."], shown
    assert secret not in shown
    assert len(lines) == 1, lines
    entry = json.loads(lines[0])
    assert entry["outcome"] == "error" and entry["error_type"] == "ValueError"
    assert entry["input_tokens"] == 0 and entry["retrieved_count"] == 0
    assert secret not in lines[0] and "PTO" not in lines[0]
    assert not [b for b in at.button if b.key in ("rate_up", "rate_down")]


def test_render_exception_is_logged_once_with_class_name_only() -> None:
    from streamlit.testing.v1 import AppTest

    original = A.answer_question
    saved_log = os.environ.get("LOG_DIR")
    tmp = tempfile.mkdtemp(prefix="ks_h7d_logs_")
    os.environ["LOG_DIR"] = tmp
    # a result whose citation has no "section": answering succeeded, rendering raises KeyError
    A.answer_question = lambda user, question: {  # type: ignore[assignment]
        "outcome": "answer", "answer": "20 days", "citations": [{"source": "hr_pto_policy_2025.md"}],
        "conflict_note": None, "clarifying_question": None, "latency_ms": 5.0,
        "input_tokens": 10, "output_tokens": 5, "retrieved_count": 5,
        "leak_scan_blocked": False, "guard_action": "none"}
    try:
        at = AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60).run()
        at.text_input[0].set_value("How many PTO days do new hires get?")
        next(b for b in at.button if b.label == "Ask").click()
        at.run(timeout=60)
        at.run(timeout=60)                                                    # a rerun must not log again
        lines = (Path(tmp) / "query_log.jsonl").read_text(encoding="utf-8").splitlines()
    finally:
        A.answer_question = original  # type: ignore[assignment]
        if saved_log is None:
            os.environ.pop("LOG_DIR", None)
        else:
            os.environ["LOG_DIR"] = saved_log
    assert [e.value for e in at.error] == ["Something went wrong. Try again."]
    entries = [json.loads(line) for line in lines]
    assert [e["outcome"] for e in entries] == ["answer", "error"], entries     # answered, then render failed
    assert entries[1]["error_type"] == "KeyError"
    assert "section" not in lines[1] and "20 days" not in lines[1]


# ---------------------------------------------------------------- 3. check_env BOM
def _env_line(content: bytes) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="ks_h7d_env_"))
    (tmp / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts" / "check_env.py", tmp / "scripts" / "check_env.py")
    (tmp / ".env").write_bytes(content)
    out = subprocess.run([sys.executable, str(tmp / "scripts" / "check_env.py")], cwd=tmp,
                         capture_output=True, text=True).stdout
    return next(line for line in out.splitlines() if "API key" in line)


def test_check_env_reads_a_bom_prefixed_env_file() -> None:
    key = b"ANTHROPIC_API_KEY=sk-test-not-a-real-key\r\nMODEL_NAME=x\r\n"
    assert "is set" in _env_line(b"\xef\xbb\xbf" + key)
    assert "is set" in _env_line(key)
    assert "looks unset" in _env_line(b"\xef\xbb\xbfANTHROPIC_API_KEY=your-key-here\r\n")
    assert "looks unset" in _env_line(b"MODEL_NAME=x\r\n")
    assert "sk-test-not-a-real-key" not in _env_line(b"\xef\xbb\xbf" + key)     # value never printed


# ---------------------------------------------------------------- 4. .env.example
def test_env_example_documents_prompt_version() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "PROMPT_VERSION=1" in lines
    idx = lines.index("PROMPT_VERSION=1")
    assert lines[idx - 1].startswith("#") and "prompt" in lines[idx - 1].lower()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
