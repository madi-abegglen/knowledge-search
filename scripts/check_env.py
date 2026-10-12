"""Environment and security sanity checks for the knowledge-search repo.

Stdlib only, so it runs even if installs failed. Never prints secret values.
Run from anywhere:   python scripts/check_env.py
Include pip-audit:   python scripts/check_env.py --audit
Exit code 0 = no failures (warnings allowed); 1 = at least one failure.
"""
import importlib.metadata as md
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_DIRS = ["app", "data", "docs", "evals", "infra", "ingest", "prompts"]
REQUIRED = ["anthropic", "chromadb", "sentence-transformers", "streamlit",
            "python-dotenv", "pypdf", "pydantic"]
IMPORT_NAMES = {"sentence-transformers": "sentence_transformers",
                "python-dotenv": "dotenv"}
MIN_VERSIONS = {"anthropic": "0.87.0", "streamlit": "1.54.0", "pypdf": "6.14.1"}
KNOWN_BAD = {"rlask", "tlask", "rsquests", "nhmpy", "mflux-streamlit"}
STATUSES = []


def record(status, msg):
    STATUSES.append(status)
    print("[%s] %s" % (status, msg))


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def vtuple(version):
    parts = []
    for piece in version.split("."):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return None


def check_layout():
    nested = ROOT / ROOT.name
    if nested.exists():
        record("FAIL", "nested folder found: %s (repo root must not contain a copy of itself)" % nested.name)
    missing = [d for d in EXPECTED_DIRS if not (ROOT / d).is_dir()]
    if missing:
        record("FAIL", "missing folders at repo root: %s" % ", ".join(missing))
    elif not nested.exists():
        record("PASS", "repo layout ok (root: %s)" % ROOT.name)


def check_venv():
    if sys.prefix == sys.base_prefix:
        record("FAIL", "no virtual environment is active")
    elif Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        record("FAIL", "active venv is not this repo's .venv (sys.prefix=%s)" % sys.prefix)
    else:
        record("PASS", "repo .venv is active")


def check_lockfile():
    path = ROOT / "requirements.txt"
    if not path.exists():
        record("FAIL", "requirements.txt is missing")
        return
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        record("FAIL", "requirements.txt is UTF-16; regenerate it as UTF-8")
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        record("FAIL", "requirements.txt is not valid UTF-8")
        return
    if raw.startswith(b"\xef\xbb\xbf"):
        record("WARN", "requirements.txt has a UTF-8 BOM (pip accepts it)")
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            names.add(norm(re.split(r"[=<>~ @\[;]", line, maxsplit=1)[0]))
    missing = [r for r in REQUIRED if norm(r) not in names]
    if missing:
        record("FAIL", "lockfile is missing: %s" % ", ".join(missing))
    else:
        record("PASS", "lockfile is UTF-8 and lists all required packages")


def check_versions_and_imports():
    for pkg in REQUIRED:
        if importlib.util.find_spec(IMPORT_NAMES.get(pkg, pkg)) is None:
            record("FAIL", "cannot import %s" % pkg)
    for pkg, minimum in MIN_VERSIONS.items():
        try:
            installed = md.version(pkg)
        except md.PackageNotFoundError:
            record("FAIL", "%s is not installed" % pkg)
            continue
        if vtuple(installed) < vtuple(minimum):
            record("FAIL", "%s %s is below the security minimum %s" % (pkg, installed, minimum))
        else:
            record("PASS", "%s %s meets the minimum (%s)" % (pkg, installed, minimum))


def check_typosquats():
    installed = set()
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name:
            installed.add(norm(name))
    bad = installed & KNOWN_BAD
    if bad:
        record("FAIL", "known malicious package(s) installed: %s" % ", ".join(sorted(bad)))
    else:
        record("PASS", "no known typosquat or malicious packages installed")


def check_env_file():
    env = ROOT / ".env"
    if not env.exists():
        record("FAIL", ".env is missing (copy .env.example and add your key)")
        return
    key_ok = False
    # utf-8-sig: an editor or PowerShell 5.1 may write a BOM; it must not hide the first key
    for line in env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            key_ok = val.startswith("sk-") and "your-key" not in val
    record("PASS" if key_ok else "WARN", "API key %s in .env (value not shown)" % ("is set" if key_ok else "looks unset"))
    res = git("check-ignore", "-q", ".env")
    if res is None or (res.returncode not in (0, 1)):
        record("WARN", "git unavailable or repo not initialized; cannot confirm .env is ignored")
    elif res.returncode == 0:
        record("PASS", ".env is git-ignored")
    else:
        record("FAIL", ".env is NOT git-ignored; fix .gitignore before committing")


def check_git_tracking():
    res = git("ls-files")
    if res is None or res.returncode != 0:
        record("WARN", "not a git repo yet; skipped tracked-secrets check")
        return
    bad = [f for f in res.stdout.splitlines()
           if f == ".env" or f.startswith((".venv/", "chroma_db/", "logs/"))]
    if bad:
        record("FAIL", "tracked files that must not be committed: %s" % ", ".join(bad[:5]))
    else:
        record("PASS", "no secrets or environment folders are tracked by git")


def load_exceptions():
    """Read scripts/audit_exceptions.txt: lines of `ID | reason`. Returns {id: reason}."""
    path = ROOT / "scripts" / "audit_exceptions.txt"
    found = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                vuln_id, _, reason = line.partition("|")
                found[vuln_id.strip()] = reason.strip()
    return found


def check_chroma_embedded_only():
    """Accepted ChromaDB advisories only hold while we never run or call a Chroma server."""
    banned = ("HttpClient", "AsyncHttpClient", "chroma run", "chromadb.server", "chroma_server")
    hits = []
    for folder in ("app", "ingest", "evals", "infra", "scripts"):
        base = ROOT / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits += ["%s (%s)" % (path.relative_to(ROOT), term) for term in banned if term in text]
    if hits:
        record("FAIL", "Chroma server usage found; accepted advisories no longer apply: %s" % ", ".join(hits[:5]))
    else:
        record("PASS", "ChromaDB used embedded-only (no server or HTTP client in code)")


AUDITED_LOCKFILES = ("requirements.txt", "requirements-dev.txt")  # runtime lock + audit-tooling lock


def run_audit():
    exceptions = load_exceptions()
    failed = []
    for lockfile in AUDITED_LOCKFILES:
        if not (ROOT / lockfile).is_file():
            print("pip-audit: %s is missing" % lockfile)
            failed.append(lockfile)
            continue
        cmd = [sys.executable, "-m", "pip_audit", "-r", lockfile]
        for vuln_id in exceptions:                      # same accepted exceptions, applied to both
            cmd += ["--ignore-vuln", vuln_id]
        res = subprocess.run(cmd, cwd=ROOT)
        if res.returncode != 0:
            failed.append(lockfile)
    if not failed and not exceptions:
        record("PASS", "pip-audit reported no known vulnerabilities")
    elif not failed:
        record("WARN", "pip-audit clean except %d documented, accepted exception(s): %s "
                       "(see docs/security.md; re-review when a fixed version is published)"
               % (len(exceptions), ", ".join(exceptions)))
    else:
        record("FAIL", "pip-audit reported NEW findings or failed to run for: %s; review the output above. "
                       "Do not add exceptions without the user's approval." % ", ".join(failed))


def main():
    check_layout()
    check_venv()
    check_lockfile()
    check_versions_and_imports()
    check_typosquats()
    check_env_file()
    check_git_tracking()
    check_chroma_embedded_only()
    if "--audit" in sys.argv:
        run_audit()
    print("\nSummary: %d pass, %d warn, %d fail" % (
        STATUSES.count("PASS"), STATUSES.count("WARN"), STATUSES.count("FAIL")))
    sys.exit(1 if "FAIL" in STATUSES else 0)


if __name__ == "__main__":
    main()
