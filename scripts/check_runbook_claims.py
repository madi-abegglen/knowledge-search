"""Verify specific operator-facing claims made in docs/runbook.md (no API calls).

Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\check_runbook_claims.py
Prints one PASS/FAIL line per claim.
"""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.answer as A  # noqa: E402

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


# 1. a variable that is set but empty in the process environment overrides .env (real dotenv, no patching)
saved = {k: os.environ.get(k) for k in ("MODEL_NAME", "ANTHROPIC_API_KEY")}
try:
    os.environ["MODEL_NAME"] = ""
    os.environ["ANTHROPIC_API_KEY"] = "sk-placeholder-value"
    check("empty MODEL_NAME set in the shell is NOT filled in from .env -> ConfigError",
          A.check_config() == "ConfigError")
    os.environ.pop("MODEL_NAME")
    check("MODEL_NAME absent from the shell IS filled in from .env -> configured",
          A.check_config() is None, "requires MODEL_NAME in .env")
finally:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

# 2. invalid PROMPT_VERSION raises (UI turns any exception into "Something went wrong. Try again.")
saved_pv = os.environ.get("PROMPT_VERSION")
try:
    for bad in ("abc", "1.5", "../x", ""):
        os.environ["PROMPT_VERSION"] = bad
        try:
            path = A.prompt_path()
            ok = bad == "" and path.name == "answer_v1.md"            # blank falls back to the default
        except ValueError:
            ok = bad != ""
        check(f"PROMPT_VERSION={bad!r} -> {'default v1' if bad == '' else 'ValueError'}", ok)
    os.environ["PROMPT_VERSION"] = "2"
    check("PROMPT_VERSION=2 -> answer_v2.md", A.prompt_path().name == "answer_v2.md")
    os.environ.pop("PROMPT_VERSION")
    check("PROMPT_VERSION unset -> answer_v1.md", A.prompt_path().name == "answer_v1.md")
finally:
    if saved_pv is None:
        os.environ.pop("PROMPT_VERSION", None)
    else:
        os.environ["PROMPT_VERSION"] = saved_pv

# 3. the fixed messages quoted in the runbook
check("config message", A.CONFIG_ERROR_MESSAGE == "Service is not configured. Contact the administrator.")
check("API message", A.SERVICE_UNAVAILABLE_MESSAGE == "Service unavailable. Please try again.")
check("too-long message", A.QUERY_TOO_LONG_MESSAGE == "Please shorten your question (2,000 characters max).")
check("refusal message", A.REFUSAL_MESSAGE == "I couldn't find that.")
check("query cap", A.MAX_QUERY_CHARS == 2000)
check("API timeout / retries", (A.API_TIMEOUT_SECONDS, A.API_MAX_RETRIES) == (30.0, 1))

failed = results.count(False)
print(f"\n{results.count(True)} passed, {failed} failed")
sys.exit(1 if failed else 0)
