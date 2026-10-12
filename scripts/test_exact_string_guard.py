"""Tests for the exact-string guard (synthetic chunks, mocked model, no API, no model load).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\test_exact_string_guard.py
"""

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402

import app.answer as A  # noqa: E402
from app.guard import ACTION_BLOCKED, ACTION_NONE, ACTION_REPAIRED, apply_exact_string_guard  # noqa: E402

EMAIL = "security@northwind.example"
URL = "https://portal.northwind.example/it"
CHUNKS = [
    f"Report immediately using the Report Phish button, or email {EMAIL}. Do not forward the message.",
    f"The IT portal is at {URL} (open it from the company network).",
]


def guard(answer: str | None, note: str | None = None, chunks: list[str] | None = None):
    return apply_exact_string_guard(answer, note, CHUNKS if chunks is None else chunks)


# ---------------------------------------------------------------- unit: repair
def test_spaced_email_is_repaired() -> None:
    result = guard("Email the security address (security @ northwind. example). Do not click links.")
    assert result.action == ACTION_REPAIRED and result.repaired == 1, result
    assert result.answer == f"Email the security address ({EMAIL}). Do not click links."


def test_spaced_email_variants_and_case() -> None:
    for text in ("security @northwind.example", "security@ northwind . example", "security @ northwind.example",
                 "SECURITY@NORTHWIND.EXAMPLE", "Security @ Northwind.Example"):
        result = guard(f"Contact {text} now.")
        assert result.action == ACTION_REPAIRED, (text, result)
        assert result.answer == f"Contact {EMAIL} now.", (text, result.answer)


def test_spaced_url_is_repaired() -> None:
    for text in ("https: //portal.northwind.example/it", "https://portal. northwind.example/it",
                 "https://portal.northwind. example / it", "HTTPS://PORTAL.NORTHWIND.EXAMPLE/IT"):
        result = guard(f"Open {text} to continue.")
        assert result.action == ACTION_REPAIRED, (text, result)
        assert result.answer == f"Open {URL} to continue.", (text, result.answer)


def test_repair_in_conflict_note_and_both_fields() -> None:
    result = guard("See security @ northwind. example.", "Older note: security @ northwind. example (old).")
    assert result.action == ACTION_REPAIRED and result.repaired == 2
    assert EMAIL in result.answer and EMAIL in result.conflict_note
    assert " @ " not in result.answer and " @ " not in result.conflict_note


def test_sentence_boundary_after_repaired_address_is_kept() -> None:
    result = guard("Write to security @ northwind. example. Do not click links.")
    assert result.action == ACTION_REPAIRED
    assert result.answer == f"Write to {EMAIL}. Do not click links.", result.answer


# ---------------------------------------------------------------- unit: tokenizer-decoded chunks
# The index stores chunk text as the embedding tokenizer decoded it (lowercased, spaced
# punctuation). This is the real Q5 chunk; it is why the model sometimes copies a spaced address.
DECODED = [
    '1. * * report immediately * * ( within 1 hour ) using the " report phish " button in your email client, '
    "or email security @ northwind. example. 2. * * do not * * click links, open attachments, or forward "
    "the suspicious message.",
    "the it portal is at https : / / portal. northwind. example / it ( open it from the company network ).",
]


def test_decoded_chunk_known_string_is_whitespace_free_target() -> None:
    result = guard("Email security@northwind.example. Do not click links.", chunks=DECODED)
    assert result.action == ACTION_NONE and result.answer == "Email security@northwind.example. Do not click links."
    result = guard("Email security @ northwind. example. Do not click links.", chunks=DECODED)
    assert result.action == ACTION_REPAIRED
    assert result.answer == "Email security@northwind.example. Do not click links.", result.answer


def test_decoded_chunk_url_is_repaired_to_whitespace_free_target() -> None:
    result = guard("Open https://portal.northwind.example/it now.", chunks=DECODED)
    assert result.action == ACTION_NONE, result
    result = guard("Open https: //portal. northwind.example / it now.", chunks=DECODED)
    assert result.action == ACTION_REPAIRED
    assert result.answer == "Open https://portal.northwind.example/it now.", result.answer


def test_decoded_chunk_does_not_accept_longer_or_different_strings() -> None:
    for text in ("Email security@northwind.example.com now.", "Email xsecurity@northwind.example now.",
                 "Email helpdesk@northwind.example now."):
        assert guard(text, chunks=DECODED).action == ACTION_BLOCKED, text


# ---------------------------------------------------------------- unit: original-case chunks (7b2)
# After 7b2 the stored chunk text is the original source span, so it keeps its casing and has no
# stray spaces. The repair target is the chunk's own string, in the chunk's casing.
ORIGINAL = [
    "## 4. Contacts\nWrite to Compliance.Desk@Northwind.example for policy questions, or open "
    "https://Portal.Northwind.example/IT/Help in your browser.",
]


def test_original_case_chunk_repair_target_is_the_chunks_own_string() -> None:
    cases = {
        "compliance.desk @ northwind. example": "Compliance.Desk@Northwind.example",
        "compliance.desk@northwind.example": "Compliance.Desk@Northwind.example",   # case-only difference
        "COMPLIANCE.DESK@NORTHWIND.EXAMPLE": "Compliance.Desk@Northwind.example",
    }
    for given, target in cases.items():
        result = guard(f"Write to {given} today.", chunks=ORIGINAL)
        assert result.action == ACTION_REPAIRED, (given, result)
        assert result.answer == f"Write to {target} today.", result.answer
    url = guard("Open https: //portal. northwind.example / it / help to continue.", chunks=ORIGINAL)
    assert url.action == ACTION_REPAIRED
    assert url.answer == "Open https://Portal.Northwind.example/IT/Help to continue.", url.answer


def test_known_limitation_spaces_inside_the_local_part_fail_closed() -> None:
    # Spaces are tolerated around '@', '.', '/' in the domain/URL, not inside the local part
    # (a spaced hyphen is ordinary prose: "email - security@..."). Fails closed (blocked).
    assert guard("Write to compliance . desk @ northwind. example today.", chunks=ORIGINAL).action == ACTION_BLOCKED
    # ...and prose with a spaced dash before a verbatim address is untouched
    assert guard(f"Contact us - {EMAIL} today.").action == ACTION_NONE


def test_original_case_chunk_verbatim_string_is_untouched() -> None:
    for text in ("Write to Compliance.Desk@Northwind.example for details.",
                 "Open https://Portal.Northwind.example/IT/Help, then sign in.",
                 "Write to Compliance.Desk@Northwind.example. Do not forward it."):
        result = guard(text, chunks=ORIGINAL)
        assert result.action == ACTION_NONE and result.answer == text, (text, result)


def test_original_case_chunk_still_blocks_unknown_and_longer_strings() -> None:
    for text in ("Write to other.desk@northwind.example today.",
                 "Write to Compliance.Desk@Northwind.example.com today.",
                 "Open https://portal.northwind.example/it/help/admin now."):
        assert guard(text, chunks=ORIGINAL).action == ACTION_BLOCKED, text


# ---------------------------------------------------------------- unit: untouched
def test_exact_email_and_url_untouched() -> None:
    for text in (f"Email {EMAIL} today.", f"Email {EMAIL}. Do not click links.", f"({EMAIL})",
                 f"Open {URL}, then sign in.", f"Both: {EMAIL} and {URL}."):
        result = guard(text)
        assert result.action == ACTION_NONE and result.answer == text, (text, result)


def test_no_emails_or_urls_is_untouched() -> None:
    for text in ("New hires get 20 PTO days per year.", "See hr_pto_policy_2025.md, section 3.",
                 "Version 2025.1 costs $10,000.50. e.g. approvals apply.", "", "Ratio is 3 @ 5 apiece"):
        result = guard(text)
        assert result.action == ACTION_NONE and result.answer == text, (text, result)
    assert guard(None, None).action == ACTION_NONE


# ---------------------------------------------------------------- unit: blocked
def test_email_not_in_chunks_is_blocked() -> None:
    for text in ("Email helpdesk@northwind.example.", "Email helpdesk @ northwind. example please.",
                 f"Email {EMAIL}.com instead", "Visit https://evil.example/login"):
        result = guard(text)
        assert result.action == ACTION_BLOCKED and result.blocked >= 1, (text, result)
        assert result.answer == text                       # blocked results are never rewritten


def test_one_unknown_blocks_even_if_another_is_repairable() -> None:
    result = guard("Use security @ northwind. example or helpdesk@northwind.example.")
    assert result.action == ACTION_BLOCKED, result


def test_ambiguous_match_is_blocked_but_verbatim_string_is_not() -> None:
    two = [f"Write to {EMAIL} or to Security@Northwind.example for escalations."]
    altered = guard("Write to security @ northwind. example please.", chunks=two)
    assert altered.action == ACTION_BLOCKED, altered           # two chunk strings fit the altered text
    verbatim = guard(f"Write to {EMAIL} please.", chunks=two)
    assert verbatim.action == ACTION_NONE, verbatim            # one of them, exactly


def test_email_only_in_a_chunk_not_permitted_is_blocked() -> None:
    permitted = ["Contact the IT help desk through the portal."]
    restricted_chunk = "HR only: write to compensation-committee@northwind.example."
    # the guard only ever receives permitted chunks; the restricted one is not a known string
    result = guard("Write to compensation-committee@northwind.example.", chunks=permitted)
    assert result.action == ACTION_BLOCKED
    # sanity: it would have been "known" had the restricted chunk been passed (so the test is real)
    assert guard("Write to compensation-committee@northwind.example.",
                 chunks=permitted + [restricted_chunk]).action == ACTION_NONE


def test_known_limitation_capitalized_label_after_stray_dot_space_fails_closed() -> None:
    # ". Example" (dot, space, capitalized word) is read as the start of a new sentence, so this
    # altered address is not repaired; it is blocked (fail closed) instead of risking a bad merge.
    assert guard("Contact Security @ Northwind. Example now.").action == ACTION_BLOCKED


def test_known_limitation_prose_at_sign_that_looks_like_an_email_is_blocked() -> None:
    # Documented false positive: "word @ word. Word" is email-like once spaces are tolerated.
    assert guard("Let's meet @ noon. See you there.").action == ACTION_BLOCKED


# ---------------------------------------------------------------- unit: never raises
def test_guard_never_raises() -> None:
    weird: list[Any] = [None, 42, b"bytes", "\x00\x00", "@" * 5000, "@ " * 3000, "a" * 200_000,
                        "https://" * 2000, ("x. " * 4000) + "@ y. z", "日本語 @ ドメイン. 例"]
    for value in weird:
        for answer in (value, "ok"):
            result = apply_exact_string_guard(answer, value, [value, "text", None, 7])  # type: ignore[list-item]
            assert result.action in {ACTION_NONE, ACTION_REPAIRED, ACTION_BLOCKED}
    assert apply_exact_string_guard("x", None, []).action == ACTION_NONE
    assert apply_exact_string_guard("security@northwind.example", None, []).action == ACTION_BLOCKED


# ---------------------------------------------------------------- integration with answer_question
CHUNK = {
    "source": "it_security_incident_reporting.md",
    "text": CHUNKS[0],
    "metadata": {"section": "What to do", "version": "2025.1", "doc_status": "current"},
    "distance": 0.2,
}


def _model_json(answer: str, note: str | None = None) -> str:
    return json.dumps({"outcome": "answer", "answer": answer,
                       "citations": [{"source": "it_security_incident_reporting.md", "section": "What to do"}],
                       "conflict_note": note, "clarifying_question": None})


class _Block:
    def __init__(self, text: str) -> None:
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]

        class Usage:
            input_tokens, output_tokens = 100, 20

        self.usage = Usage()


def run_answer(script: list[str], chunks: list[dict] | None = None) -> tuple[dict[str, Any], int]:
    calls = {"n": 0}
    items = list(script)

    class Messages:
        def create(self, **_: Any) -> _Response:
            calls["n"] += 1
            return _Response(items.pop(0) if len(items) > 1 else items[0])

    original_client, original_retrieve = anthropic.Anthropic, A.retrieve
    anthropic.Anthropic = lambda **k: type("C", (), {"messages": Messages()})()  # type: ignore[assignment]
    A.retrieve = lambda user, question: [CHUNK] if chunks is None else chunks  # type: ignore[assignment]
    try:
        return A.answer_question("employee", "How do I report a phishing email?"), calls["n"]
    finally:
        anthropic.Anthropic, A.retrieve = original_client, original_retrieve  # type: ignore[assignment]


def test_answer_question_repairs_spaced_email() -> None:
    result, calls = run_answer([_model_json("Email security @ northwind. example to report it.")])
    assert result["outcome"] == "answer" and calls == 1
    assert result["answer"] == f"Email {EMAIL} to report it."
    assert result["guard_action"] == "repaired"


def test_answer_question_blocks_unknown_email_then_refuses() -> None:
    result, calls = run_answer([_model_json("Email helpdesk@northwind.example to report it.")])
    assert calls == 2                                          # existing retry-once path
    assert result["outcome"] == "refuse" and result["answer"] == A.REFUSAL_MESSAGE   # text unchanged
    assert result["guard_action"] == "blocked"
    assert "helpdesk" not in json.dumps(result)


def test_blocked_then_recovered_counts_as_blocked_and_answers() -> None:
    result, calls = run_answer([_model_json("Email helpdesk@northwind.example."),
                                _model_json(f"Email {EMAIL} to report it.")])
    assert calls == 2 and result["outcome"] == "answer" and EMAIL in result["answer"]
    assert result["guard_action"] == "blocked"                 # the invention is still counted


def test_answer_question_blocks_email_from_a_chunk_that_was_not_permitted() -> None:
    result, calls = run_answer([_model_json("Write to compensation-committee@northwind.example.")])
    assert result["outcome"] == "refuse" and result["answer"] == A.REFUSAL_MESSAGE and calls == 2
    assert result["guard_action"] == "blocked"
    assert "compensation-committee" not in json.dumps(result)


def test_clean_answer_has_guard_action_none() -> None:
    result, calls = run_answer([_model_json(f"Email {EMAIL}.")])
    assert result["outcome"] == "answer" and result["guard_action"] == "none" and calls == 1
    assert result["answer"] == f"Email {EMAIL}."


def test_refusal_text_and_log_shape_identical_for_restricted_and_not_found() -> None:
    from infra.query_log import build_entry

    blocked, _ = run_answer([_model_json("Email helpdesk@northwind.example.")])
    no_chunks, _ = run_answer(["{}"], chunks=[])
    assert blocked["answer"] == no_chunks["answer"] == A.REFUSAL_MESSAGE
    entry_a = build_entry("employee", "q1", blocked, query_id="a", model="m", prompt_ver="1")
    entry_b = build_entry("employee", "q2", no_chunks, query_id="b", model="m", prompt_ver="1")
    assert list(entry_a) == list(entry_b)
    assert entry_a["guard_action"] == "blocked" and entry_b["guard_action"] == "none"
    assert "helpdesk" not in json.dumps(entry_a)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
