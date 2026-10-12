"""Drive app/ui.py headlessly with Streamlit's AppTest and print what each user sees.

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\verify_phase5_ui.py
Makes real answer_question calls (Anthropic API) and writes one feedback line.
"""

from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
UI = str(ROOT / "app" / "ui.py")
FEEDBACK = ROOT / "logs" / "feedback.jsonl"


def visible_text(at: AppTest) -> list[str]:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [w.value for w in at.warning]
    parts += [e.value for e in at.error]
    parts += [i.value for i in at.info]
    return parts


def ask(at: AppTest, user: str, question: str) -> AppTest:
    at.sidebar.selectbox[0].set_value(user)
    at.text_input[0].set_value(question)
    ask_button = next(b for b in at.button if b.label == "Ask")
    ask_button.click()
    return at.run(timeout=180)


def show(title: str, at: AppTest) -> None:
    print(f"\n=== {title} ===")
    print("exceptions:", [e.value for e in at.exception])
    for line in visible_text(at):
        print("  |", line)
    print("  expanders:", [x.label for x in at.expander])


def main() -> int:
    at = AppTest.from_file(UI, default_timeout=180).run()
    print("sidebar label:", at.sidebar.selectbox[0].label)
    print("user options:", at.sidebar.selectbox[0].options)

    # (b) refuse, no sources
    at = ask(at, "employee", "bonus for executives")
    show("(b) employee: bonus for executives", at)
    assert not at.expander, "refusal must not show a Sources expander"

    # (e) switching users clears the result
    at.sidebar.selectbox[0].set_value("exec_user").run(timeout=60)
    show("(e) after switching employee -> exec_user (no result expected)", at)
    assert not any("Outcome" in t for t in visible_text(at)), "result not cleared"

    # (c) exec gets an answer with citations
    at = ask(at, "exec_user", "bonus for executives")
    show("(c) exec_user: bonus for executives", at)

    # (d) employee PTO question, then (f) feedback
    at = ask(at, "employee", "How many PTO days do new hires get?")
    show("(d) employee: PTO days for new hires", at)
    assert not any("Latency" in t or "tokens" in t for t in visible_text(at)), "caption still shown"
    before = FEEDBACK.read_text(encoding="utf-8").count("\n") if FEEDBACK.exists() else 0
    next(b for b in at.button if b.key == "rate_up").click()
    at.run(timeout=60)
    after = FEEDBACK.read_text(encoding="utf-8").count("\n") if FEEDBACK.exists() else 0
    print(f"\n(f) feedback lines: {before} -> {after}")
    if after:
        print("last line:", FEEDBACK.read_text(encoding="utf-8").splitlines()[-1])
    return 0 if after == before + 1 else 1


if __name__ == "__main__":
    sys.exit(main())
