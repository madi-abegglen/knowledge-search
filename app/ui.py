"""Streamlit UI for the permission-aware knowledge search demo."""

import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.answer import REFUSAL_MESSAGE, answer_question  # noqa: E402

ACCESS_PATH = ROOT / "data" / "access.json"
FEEDBACK_PATH = ROOT / "logs" / "feedback.jsonl"
GENERIC_ERROR = "Something went wrong. Try again."
BADGES = {"answer": ":green[ANSWER]", "refuse": ":red[REFUSED]", "clarify": ":orange[CLARIFY]"}


def load_users() -> dict[str, str]:
    access = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
    return {uid: rec.get("display", uid) for uid, rec in access.get("users", {}).items()}


def append_feedback(result: dict[str, Any], rating: str) -> None:
    """Log one rating. Never writes answer text or chunk text."""
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": result["user"],
        "question": result["question"],
        "outcome": result["outcome"],
        "rating": rating,
        "latency_ms": result["latency_ms"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }
    with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


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
            try:
                with st.spinner("Searching..."):
                    response = answer_question(user, question)
                st.session_state["result"] = {**response, "user": user, "question": question}
            except Exception:
                st.session_state.pop("result", None)
                st.error(GENERIC_ERROR)

    result = st.session_state.get("result")
    if result and result.get("user") == user:
        try:
            render_result(result)
        except Exception:
            st.error(GENERIC_ERROR)


main()
