# Environment Setup Plan: Knowledge Search

Purpose: get the repo into a clean, reproducible, secret-safe state before building. Run each step, run its check, and report pass/fail. If a step is already done, **verify it and move on; don't redo it.**

## Rules for the agent
- Never ask for, print, log, or commit the API key. The user pastes it into `.env` themselves.
- Ask before installing anything not listed here.
- Stop and report if any check fails; don't work around it silently.
- Don't run `git commit` until step 8's check passes.
- Treat the current working directory (the folder named `knowledge-search`) as the repo root. Never create files or folders in its parent, and never create a nested `knowledge-search/knowledge-search`.

## Shell and quoting rules
- Identify the shell you're running in (PowerShell, Git Bash, cmd) before the first command, and tell the user which one. Use that shell's syntax and venv activation (`.\.venv\Scripts\Activate.ps1` in PowerShell; `source .venv/Scripts/activate` in Git Bash).
- **Never put `!` inside a double-quoted inline command** (Bash treats it as history expansion and the command silently doesn't run). Avoid inline `python -c "..."` for anything with special characters (`!`, `$`, backticks, nested quotes). Use single quotes, or better, a script file under `scripts/`.
- Prefer the repo's checker over hand-written verification: `python scripts/check_env.py` (add `--audit` to include pip-audit). If a verification command fails to *run* (as opposed to failing its check), say so plainly; don't report it as passed.
- Confirm the active interpreter is the repo's `.venv` by running `python scripts/check_env.py`, not by assuming.

## Prerequisites
- Python 3.10+ (`python3 --version`)
- git (`git --version`)
- An Anthropic API key from console.anthropic.com (user-provided)
- Internet access (first run downloads the embedding model, ~90 MB)

## Target layout

**Repo root:** the folder named exactly `knowledge-search`, located at `<parent-directory>/knowledge-search`. The `/` is a path separator: the parent directory and `knowledge-search` are two separate folders. This is the folder already open in VS Code; confirm with `pwd`.

- Every path in this plan is relative to the repo root (`./`).
- Do **not** create a second `knowledge-search` folder inside the root.
- Do **not** merge, concatenate, or rename the parent directory and the root folder into one name.

| Correct | Wrong |
|---|---|
| `<parent-directory>/knowledge-search/` | `<parent-directory>knowledge-search/` (slash dropped) |
| `./data/access.json` | `./knowledge-search/data/access.json` (nested copy) |
| `./app/__init__.py` | `<parent-directory>/app/` (created outside the root) |

```
./                      <- repo root (the existing "knowledge-search" folder)
├── app/
├── data/
├── docs/
├── evals/
├── infra/
├── ingest/
├── prompts/
├── .env                (git-ignored; real secrets)
├── .env.example        (committed; placeholders only)
├── .gitignore
└── requirements.txt
```
Planned later: `./logs/` and `./chroma_db/` (both git-ignored).

## Steps

### 1. Confirm location
```bash
pwd && ls
```
**Check:** `pwd` ends in `/knowledge-search`, `ls` shows the folders above directly (no nested `knowledge-search/` inside), and `data/access.json` and `docs/problem-statement.md` exist. If `pwd` is the parent, `cd knowledge-search` first.

### 2. Virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python --version && which python
```
**Check:** `which python` points inside `.venv`.

### 3. Install dependencies with security minimums and lock
Rationale: see `docs/security.md`. Run commands **one at a time** (not chained with `&&`; Windows PowerShell 5.1 doesn't support it).

Create `requirements.in` (human-edited minimums; names must be spelled exactly, as typosquats are a known attack):
```
anthropic>=0.87.0
chromadb
sentence-transformers
streamlit>=1.54.0
python-dotenv
pypdf>=6.14.1
pydantic
```
Install wheels only (prevents sdist build scripts from running), then lock:
```bash
python -m pip install -U pip
python -m pip install --only-binary=:all: -r requirements.in
python -m pip freeze > requirements.txt
```
**Windows PowerShell 5.1:** `>` writes UTF-16, which pip can't read. Use this for the last command instead:
```powershell
python -m pip freeze | Out-File -Encoding utf8 requirements.txt
```
If `--only-binary` fails for a package with no wheel for this Python version, **report which one and ask**. Don't silently drop the flag.

**Check:** `requirements.txt` lists all seven packages; `python -m pip show streamlit pypdf anthropic` shows versions at or above the minimums.

### 3a. Vulnerability audit
```bash
python -m pip install --only-binary=:all: -r requirements-dev.txt
python -m pip_audit -r requirements.txt
python -m pip_audit -r requirements-dev.txt
```
Install pip-audit **inside the repo `.venv`** with the explicit interpreter (never the system or user-site Python). Its pins live in `requirements-dev.txt`, which is kept separate from the runtime lock `requirements.txt` (see `docs/lockfiles.md`; `python scripts/check_env.py --audit` audits both files). To recreate the audit pins from their inputs: `python -m pip install --only-binary=:all: -r requirements-dev.in`.
**Check:** no *unreviewed* vulnerabilities. If new ones appear, show them to the user and propose a fix; don't unpin or remove packages silently. (pip-audit only knows about *reported* issues; it doesn't prove a package is clean.)

**Accepted risks:** where no fixed version exists, the user may accept a finding only if the stated reason holds. Accepted items live in `scripts/audit_exceptions.txt` (`ID | reason`), are reported as WARN (never PASS), and are re-reviewed whenever a fixed version is published. **Only the user adds lines to that file.** Currently accepted: four ChromaDB server-side advisories (CVE-2026-45829, -45830, -45831, -45833), valid only while ChromaDB is used embedded-only; `check_env.py` fails if server or HTTP-client usage appears in the code. Before accepting any new finding, look up what it affects and ask the user.

### 3b. Check for supply-chain worm persistence
A May-June 2026 worm campaign (Mini Shai-Hulud and copycats) was reported to plant hooks in Claude Code and VS Code settings. **Read-only checks; report findings, don't delete anything:**
- Look for a file named `.bun_ran` in the temp folder (`$env:TEMP` on Windows, `/tmp` on macOS/Linux).
- Read `~/.claude/settings.json` and any `./.claude/` folder; list any `hooks` the user didn't add.
- Read `./.vscode/tasks.json` and `settings.json` if present; flag unexpected auto-run tasks.
- Confirm the repo has no unexpected `.github/` workflows or `setup.js`.

**Check:** nothing unexpected found. If anything is, stop and tell the user (see `docs/security.md`, "If something looks wrong").

### 3c. Consolidated check (run now, and again before every phase commit)
```bash
python scripts/check_env.py --audit
```
It verifies: repo layout (no nested folder), the repo's `.venv` is the active interpreter, `requirements.txt` is UTF-8 and complete, security minimum versions, no known typosquats installed, `.env` exists and is git-ignored (key presence only, value never shown), no secrets tracked by git, and pip-audit.
**Check:** exit code 0 with no `[FAIL]` lines. `[WARN]` lines for the documented ChromaDB exceptions are expected. Show the user the full output, including warnings.

### 4. Make folders importable
```bash
touch ingest/__init__.py app/__init__.py evals/__init__.py infra/__init__.py
```
**Check:** `python -c "import ingest, app, evals, infra"` runs from the repo root.

### 5. `.gitignore` (before the first commit)
```
.venv/
.env
chroma_db/
logs/
__pycache__/
*.pyc
.DS_Store
```
**Check:** `git check-ignore -v .env .venv chroma_db logs` lists every path.

### 6. Env files
`.env.example` (committed):
```
ANTHROPIC_API_KEY=your-key-here
MODEL_NAME=claude-sonnet-5-5
CHROMA_PATH=./chroma_db
EMBED_MODEL=all-MiniLM-L6-v2
LOG_DIR=./logs
```
Then `cp .env.example .env`. **The user** edits `.env` and pastes their real key.
**Check:** `.env` exists and differs from `.env.example` in the key line only. Do not print its contents.

### 7. Verify the toolchain
```bash
python -c "import chromadb, anthropic, sentence_transformers, streamlit, pydantic; print('imports ok')"
python -c "
from dotenv import load_dotenv; import os
load_dotenv()
print('key loaded:', os.getenv('ANTHROPIC_API_KEY','').startswith('sk-'))
print('model:', os.getenv('MODEL_NAME'))
"
```
**Check:** `imports ok`, `key loaded: True`, model name printed. (Prints a boolean only, never the key.)

Optional live test (one tiny call, confirms the key and model work):
```bash
python -c "
from dotenv import load_dotenv; import os, anthropic
load_dotenv()
r = anthropic.Anthropic().messages.create(model=os.getenv('MODEL_NAME'), max_tokens=20, messages=[{'role':'user','content':'Say ok'}])
print(r.content[0].text)
"
```

### 8. Git
```bash
git init            # skip if .git already exists
git add .
git status
```
**Check (critical):** `git status` must NOT list `.env`, `.venv/`, `chroma_db/`, or `logs/`. If it does, fix `.gitignore` and `git rm --cached` before continuing. Then:
```bash
git commit -m "Initial scaffold: problem statement, sample docs, access.json, env setup"
```

### 9. Environment separation (note for the README)
- Local dev uses `.env` and the local `chroma_db/`.
- A production setup would use separate API keys per environment, a managed secret store (not a file), and read-only service accounts for the document sources.
- Record this in `docs/environments.md` (dev vs. prod: keys, data access, who can change what).

### 10. Optional: remote
Create an empty **private** repo, then:
```bash
git remote add origin <url> && git branch -M main && git push -u origin main
```

## Final checklist
- [ ] venv active; all imports succeed
- [ ] `requirements.in` has the security minimums; `requirements.txt` is locked (UTF-8)
- [ ] `pip-audit` clean (or findings reviewed with the user)
- [ ] Worm-persistence checks (3b) clean
- [ ] `python scripts/check_env.py --audit` exits 0 (3c)
- [ ] `.env` holds a key and is git-ignored; `.env.example` has placeholders only
- [ ] First commit exists with no secrets
- [ ] `docs/environments.md` written (short)

## Troubleshooting
| Symptom | Fix |
|---|---|
| `chromadb` build failure | `pip install -U pip`; on Windows install Microsoft C++ Build Tools |
| `key loaded: False` | Run from repo root; no quotes or spaces around the key in `.env` |
| `command not found: python3` | Use `python` |
| `ModuleNotFoundError: ingest` | Run from repo root with `python -m ...`; confirm `__init__.py` files exist |
| Embedding model download stalls | Check network/proxy; retry; it caches after first success |
| `.env` appears in `git status` | `git rm --cached .env`, then verify `.gitignore` |
| `--only-binary` error (no wheel) | Report the package; ask before relaxing the flag for that one package |
| `pip install -r requirements.txt` fails to read the file | It was written as UTF-16; regenerate with `Out-File -Encoding utf8` |
| `&&` syntax error in PowerShell | Run commands separately (5.1 doesn't support `&&`) |
