"""Streamlit UI for the permission-aware knowledge search demo."""

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.answer import (  # noqa: E402
    CONFIG_ERROR_MESSAGE,
    CONFIG_ERROR_TYPE,
    OUTCOME_INVALID_INPUT,
    OUTCOME_SERVICE_ERROR,
    QUERY_TOO_LONG_MESSAGE,
    REFUSAL_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE,
    answer_question,
    check_config,
)

from infra.query_log import FEEDBACK_NAME, log_dir, log_query  # noqa: E402

ACCESS_PATH = ROOT / "data" / "access.json"
GENERIC_ERROR = "Something went wrong. Try again."
BADGES = {"answer": ":green[ANSWER]", "refuse": ":red[REFUSED]", "clarify": ":orange[CLARIFY]"}


def load_users() -> dict[str, str]:
    access = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    return {uid: rec.get("display", uid) for uid, rec in access.get("users", {}).items()}


def append_feedback(result: dict[str, Any], rating: str) -> None:
    """Log one rating. Never writes answer text or chunk text. The raw question text is
    stored ONLY for a thumbs-down (so the failure can be reviewed); thumbs-up stores none."""
    feedback_path = log_dir() / FEEDBACK_NAME
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query_id": result.get("query_id"),
        "user": result["user"],
        "outcome": result["outcome"],
        "rating": rating,
        "latency_ms": result["latency_ms"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }
    if rating == "down":
        record["question"] = result["question"]
    with feedback_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_unexpected_error(user: str, question: str, exc: Exception, started: float) -> None:
    """Query-log an unexpected failure: outcome error and the exception CLASS NAME only.
    The exception text, the question and any answer text are never logged."""
    log_query(user, question, {
        "outcome": "error",
        "error_type": type(exc).__name__,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "input_tokens": 0,
        "output_tokens": 0,
        "retrieved_count": 0,
        "leak_scan_blocked": False,
        "guard_action": "none",
    })


def record_rating(rating: str) -> None:
    result = st.session_state.get("result")
    if not result or result.get("rated"):
        return
    try:
        append_feedback(result, rating)
        result["rated"] = rating
    except OSError:
        result["feedback_error"] = True


def render_result(result: dict[str, Any]) -> None:
    outcome = result["outcome"]
    # Runtime states (not model outcomes): fixed constant text, no badge, no feedback buttons,
    # never exception text.
    if outcome == OUTCOME_SERVICE_ERROR:
        if result.get("error_type") == CONFIG_ERROR_TYPE:
            st.error(CONFIG_ERROR_MESSAGE)
        else:
            st.error(SERVICE_UNAVAILABLE_MESSAGE)
        return
    if outcome == OUTCOME_INVALID_INPUT:
        st.warning(QUERY_TOO_LONG_MESSAGE)
        return

    st.markdown(f"**Outcome:** {BADGES.get(outcome, outcome)}")

    if outcome == "refuse":
        st.write(REFUSAL_MESSAGE)
    elif outcome == "clarify":
        st.write(result["clarifying_question"])
    else:
        st.write(result["answer"])
        if result.get("conflict_note"):
            st.warning(f"Conflict note: {result['conflict_note']}")
        with st.expander("Sources"):
            for citation in result["citations"]:
                st.markdown(f"- `{citation['source']}`: {citation['section']}")

    if result.get("rated"):
        st.caption(f"Thanks, feedback recorded ({result['rated']}).")
    else:
        up, down, _ = st.columns([1, 1, 8])
        up.button("👍", key="rate_up", on_click=record_rating, args=("up",))
        down.button("👎", key="rate_down", on_click=record_rating, args=("down",))
    if result.get("feedback_error"):
        st.caption("Could not save feedback.")


def main() -> None:
    st.set_page_config(page_title="Knowledge Search", page_icon="🔎")
    st.title("Knowledge Search")

    # Startup check: a misconfigured service shows one fixed message (no setting names or values).
    if check_config():
        st.error(CONFIG_ERROR_MESSAGE)
        st.stop()

    users = load_users()
    with st.sidebar:
        user = st.selectbox(
            "Demo user switcher (production would use SSO)",
            options=list(users),
            format_func=lambda uid: f"{users[uid]} ({uid})",
        )

    # Results are tied to the user who asked; switching users drops them.
    current = st.session_state.get("result")
    if current and current.get("user") != user:
        del st.session_state["result"]

    question = st.text_input("Ask a question about internal documents")
    if st.button("Ask", type="primary"):
        if not question.strip():
            st.info("Type a question first.")
        else:
            started = time.perf_counter()
            try:
                with st.spinner("Searching..."):
                    response = answer_question(user, question)
                query_id = log_query(user, question, response)
                st.session_state["result"] = {
                    **response, "user": user, "question": question, "query_id": query_id,
                }
            except Exception as exc:
                st.session_state.pop("result", None)
                log_unexpected_error(user, question, exc, started)
                st.error(GENERIC_ERROR)

    result = st.session_state.get("result")
    if result and result.get("user") == user:
        try:
            render_result(result)
        except Exception as exc:
            if not result.get("render_error_logged"):       # once per result, not on every rerun
                result["render_error_logged"] = True
                log_unexpected_error(user, result.get("question", ""), exc, time.perf_counter())
            st.error(GENERIC_ERROR)


main()
