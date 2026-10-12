# Runbook: Knowledge Search

For an operator who has never seen this repo. Commands are Windows PowerShell, run from the repo root (a short
path such as `C:\ks` avoids the Windows 260-character limit), using the repo's virtual environment:
`.\.venv\Scripts\python.exe`. Everything here was run on Windows 11 with Python 3.14.6; "healthy output" blocks are
real output unless marked synthetic.

> This is a demo system on 15 synthetic documents (see `README.md`). Treat it as a pattern, not a production service.

## 1. Start, check and stop the app

| Task | Command | Healthy |
|---|---|---|
| Start | `.\.venv\Scripts\streamlit.exe run app\ui.py --server.address localhost` | prints `You can now view your Streamlit app in your browser.` and `URL: http://localhost:8501` |
| Health | `Invoke-WebRequest http://localhost:8501/_stcore/health -UseBasicParsing` | `200` and `ok` |
| Bound to localhost only | `Get-NetTCPConnection -LocalPort 8501 -State Listen` | `LocalAddress 127.0.0.1` |
| Stop (foreground) | `Ctrl+C` in the terminal that runs it | process exits, port closes |
| Stop (background/headless) | `Get-CimInstance Win32_Process \| Where-Object { $_.CommandLine -match 'streamlit' } \| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }` | `Get-NetTCPConnection -LocalPort 8501 -State Listen` returns nothing |

- Always bind to localhost (`--server.address localhost`); see `docs/security.md`.
- To run without opening a browser or asking questions, add `--server.headless true` (verified: starts and answers
  the health check). On a machine that has never run Streamlit it may ask for an optional email on first start;
  press Enter to skip. Not observed here (a Streamlit credentials file already exists on this machine).
- First query after a start is slower: the embedding model loads on first use (`all-MiniLM-L6-v2`; downloaded from
  Hugging Face if not cached). A harmless "unauthenticated requests to the HF Hub" warning may print.
- If the page shows a red "Service is not configured" message, see section 4.
- After any re-ingest or index rollback, **restart the app**: the collection handle is cached in the process.

## 2. Settings (`.env`)

Copy `.env.example` to `.env` and edit it in an editor. `.env` is git-ignored; never commit it or paste the key.
Do not create it with `Set-Content -Encoding utf8` in Windows PowerShell 5.1: that writes a UTF-8 BOM, the app still
works, but `check_env.py` then wrongly reports "API key looks unset".

| Variable | Required | Default | What it does |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | none | API key. Missing, empty or whitespace-only gives the "not configured" error state; nothing is sent. |
| `MODEL_NAME` | yes | none (`.env.example`: `claude-sonnet-5-5`) | Model id used for answers. Missing or blank gives the same "not configured" state. The prompt files do not select the model. |
| `CHROMA_PATH` | no | `./chroma_db` | Where the vector index lives. Read by the app, `ingest.index` and `verify_ingest.py`. |
| `EMBED_MODEL` | no | `all-MiniLM-L6-v2` | Embedding model. Must match the model the index was built with: changing it needs a full re-ingest and a re-baseline of the evals. |
| `LOG_DIR` | no | `./logs` | Folder for `query_log.jsonl` and `feedback.jsonl` (both git-ignored). |
| `PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK` | no | blank | USD per million tokens. Used for `cost_usd` in the query log and in eval cost. Blank means cost is `null` ("N queries without a price" in the report). |
| `DAILY_COST_LIMIT_USD` | no | blank | Daily spend limit for the report's cost alert. Blank disables that alert (the report says so). |
| `QUERY_LOG_SALT` | no | blank | Salt for the question hash in the query log. See section 9. |
| `PROMPT_VERSION` | no | `1` | Which `prompts/answer_v{N}.md` the app uses (digits only; anything else raises an error that surfaces as "Something went wrong. Try again."). **Not listed in `.env.example`**; the default is v1 and v2 is a candidate only (see `docs/eval-results.md`, "Prompt default"). |

Check settings without revealing them: `.\.venv\Scripts\python.exe scripts\check_env.py` prints `API key is set in
.env (value not shown)`.

## 3. Health check and alerts

```powershell
.\.venv\Scripts\python.exe -m infra.report --days 7
```
Reads `<LOG_DIR>\query_log.jsonl` (and `feedback.jsonl` for ratings). Exit code `0` = healthy, `1` = at least one
alert. It prints no question text, answer text or document names: the log does not contain them.

Healthy output (synthetic log, 30 queries; produced by `scripts\make_sample_log.py`):
```
Query log report, last 7 day(s)
--------------------------------------------
queries:            30
outcome mix:        answer=25, clarify=0, refuse=5
refusal rate:       16.7% (of answer/refuse/clarify)
invalid input:      0 (rejected before retrieval; excluded from refusal rate)
error rate:         0.0%
latency p50 / p95:  1960 ms / 2520 ms
tokens in / out:    31200 / 5700
cost:               $0.0000 (30 queries without a price)
thumbs-down rate:   n/a (0/0 ratings)
leak_scan_blocked:  0
exact-string guard: repaired=0, blocked=0 (previous window blocked=0)
malformed lines:    0 (skipped)

STATUS: HEALTHY
```
With no traffic yet it prints `no queries in window` and `STATUS: HEALTHY` (exit 0). In a real log the cost line
shows dollars when `PRICE_*` are set.

How to read it: the **outcome mix** counts model outcomes (`answer`, `refuse`, `clarify`) plus `error` (service
failure) and `invalid_input` (query over 2,000 characters, rejected before retrieval). The **refusal rate**
excludes `error` and `invalid_input`. **Malformed lines** are skipped and counted, never fatal.

### Alert response

Thresholds live in one place, `infra/thresholds.py`. Rate alerts need at least 20 queries in the window. Alert
example (synthetic log):
```
ALERTS:
  [CRITICAL] LEAK_SCAN_BLOCKED: 1 response(s) blocked by the leak scan (allowed: 0). Treat as a potential permission leak.
  [WARNING] GUARD_BLOCKED_RISING: exact-string guard blocked 1 answer(s), up from 0 in the previous window: ...
  [WARNING] CONSECUTIVE_ERRORS: 3 errors in a row (limit 3).
  [WARNING] ERROR_RATE: error rate 10.0% > 5%.
STATUS: ALERT
```

| Alert | Fires when | Likely causes | First check | Action |
|---|---|---|---|---|
| `LEAK_SCAN_BLOCKED` (**CRITICAL**) | any query had `leak_scan_blocked: true` | (a) **known false positive**: a refusal whose wording echoes a restricted title the user asked about (see below); (b) a real problem: a restricted chunk reached the model (wrong `grp_*` metadata or filter) or the model named a restricted document | `scripts\diagnose_leak_scan.py` on a similar question: is the outcome `refuse` and the matched label a rephrasing of the user's own words (a), or `answer` / a label the user never mentioned (b)? Then `scripts\verify_ingest.py` | (a): note it; the user only ever saw the fixed refusal. (b): treat as an incident: stop the app, audit `data\access.json`, re-ingest, re-run `edge_cases.py` permissions group and the Q10/Q11/Q13 evals; do not restart until clean |
| `ERROR_RATE` (warning, over 5%) | error outcomes / all queries | API outage or rate limit, expired or wrong key, timeouts (30 s, one retry) | count error types: `Get-Content logs\query_log.jsonl \| ConvertFrom-Json \| Group-Object error_type` | see section 4; wait out an outage; fix the key |
| `CONSECUTIVE_ERRORS` (warning, 3 in a row) | 3 or more `error` outcomes consecutively (any sample size) | same as above, usually fast to appear | one query through the CLI: `.\.venv\Scripts\python.exe -m app.answer --user employee "How many PTO days do new hires get?"` and read `error_type` | same as above |
| `P95_LATENCY` (warning, over 10 s) | p95 of `latency_ms` | slow API, retries, cold start after a restart, very long questions | compare p50 vs p95; look for a restart just before; check API status | usually transient; if persistent, check tokens in/out and the 2,000-character cap |
| `REFUSAL_RATE` (warning, over 40%) | refuse / (answer + refuse + clarify) | empty or wrong index (`retrieved_count` 0), `CHROMA_PATH` pointing elsewhere, many out-of-scope or non-English questions, a permission change | `Get-Content logs\query_log.jsonl \| ConvertFrom-Json \| Where-Object retrieved_count -eq 0 \| Measure-Object`; then `verify_ingest.py` | re-ingest if the index is wrong; otherwise review what users ask (the log keeps only length and hash, not text) |
| `DAILY_COST` (warning) | any UTC day's `cost_usd` above `DAILY_COST_LIMIT_USD` | traffic, long prompts, prompt v2 (+13% input tokens), a retry loop | report per-day cost; tokens in/out | owner decides: raise the limit or investigate abuse |
| `GUARD_BLOCKED_RISING` (warning, never critical) | blocked count above 0 and above the previous window | the model is emitting an email or URL that is not in the permitted context | the log never stores the string; reproduce with `evals.run_evals --only 5 --repeat 20` and a few email/URL questions | usually model behavior; if it keeps rising, review the prompt version |

**Known false positive for `LEAK_SCAN_BLOCKED`.** The scan runs on the model's refusal text too. When a user asks
about a restricted document in words close to its title, the model's refusal often repeats that phrase ("does not
contain any information about an executive bonus structure"), the scan matches the restricted title, and the flag
is set. The user still sees only the fixed "I couldn't find that." Measured by `diagnose_leak_scan.py --all-eval`:
1 of 25 eval questions (Q10, "What's the exec bonus structure?"). Until fixed (see `docs/build-plan.md`, 7b list)
this CRITICAL alert can fire during normal use; the logged outcome is `refuse` in both the false-positive and the
real case, so use the diagnosis tool, not the log alone.

## 4. Error states: what users see, what is logged

| State | User sees | Logged (`query_log.jsonl`) | Check |
|---|---|---|---|
| Not configured | red: "Service is not configured. Contact the administrator." (also at startup, then the page stops) | `outcome: error`, `error_type: ConfigError` | `.env` has non-blank `ANTHROPIC_API_KEY` and `MODEL_NAME` (`check_env.py`); a variable set but empty in the shell overrides `.env` |
| API failure | red: "Service unavailable. Please try again." | `outcome: error`, `error_type` = exception class name, e.g. `APITimeoutError`, `APIConnectionError`, `RateLimitError`, `AuthenticationError`, `InternalServerError` | provider status; key validity; network |
| Question too long | yellow: "Please shorten your question (2,000 characters max)." | `outcome: invalid_input`, 0 tokens | nothing to fix |
| No answer / restricted / not in docs | "REFUSED" with "I couldn't find that." (identical for all three causes) | `outcome: refuse` | see `retrieved_count` and section 3 |
| Unexpected exception in the UI | "Something went wrong. Try again." | **not logged**: the UI swallows it | run the CLI query above to reproduce with a traceback |

Error and invalid-input states show no feedback buttons. Messages are fixed strings, identical for every user and
query. Exception text, setting names and values are never shown or logged (only the class name).

## 5. Add or change a document

1. Put the file in `data\` (`.md`, `.txt` or `.pdf`). Front matter is required and validated:
   ```
   ---
   title: Remote and Hybrid Work Policy
   version: "2025.1"
   effective_date: 2025-01-01
   doc_status: current          # or: superseded (optionally superseded_by: <file>)
   ---
   ```
2. Add its entry to `data\access.json` under `documents`, with groups from `all, hr, finance, sales, exec`:
   `"my_doc.md": { "allowed_groups": ["all"] }`. A file without an entry fails ingestion.
3. Stop the app, back up the index (section 6), then re-ingest:
   ```powershell
   .\.venv\Scripts\python.exe -m ingest.index
   ```
   Healthy: one `<file>: N chunks` line per document and `Total chunks: <N>`. Validation is all-or-nothing: any bad
   file aborts the run and **all** problems are listed together; nothing is indexed. Re-running is safe (same chunk
   ids are upserted; stale ids are removed).
4. Verify: `.\.venv\Scripts\python.exe scripts\verify_ingest.py`. Healthy: a table of per-source chunk counts and
   `grp_*` flags, ending `Verified 15 sources and 65 chunks.` (numbers change when you add documents).
5. Add eval questions for the new content to `evals\questions.csv` (columns `id, user, question, expected_outcome,
   expected_source, must_contain, must_not_contain, notes, expects_conflict`), then run the evals and compare:
   ```powershell
   .\.venv\Scripts\python.exe -m evals.run_evals --prompt-version 1 --name after_doc_change
   .\.venv\Scripts\python.exe scripts\compare_evals.py after_7b3c_v1 after_doc_change
   ```
   Required: permission leaks `0`; no question worse without an explanation. Start the app again.

**Change a permission.** A document's groups are baked into the index at ingest, so changing a document's
`allowed_groups` needs steps 2 to 4 again (re-ingest) and the app restarted. A user's groups are read from
`data\access.json` on every query: no re-ingest, but check it. After either change **re-check Hard Rule 1** (zero
permission leaks, restricted looks identical to not-found):
```powershell
.\.venv\Scripts\python.exe scripts\edge_cases.py
```
Healthy: `TOTAL: {'PASS': 30, 'FAIL': 0, 'FINDING': 2}` (the 2 are the documented Spanish and Japanese findings) and
`real index and data/ unchanged: True`. It makes small real API calls. Then re-run Q10, Q11, Q13 style questions.
`data\access.json` is demo-only; production would mirror the source system's access control at query time.

## 6. Roll back

**Index.** Back up before any re-ingest (the name pattern `chroma_db_backup*\` is git-ignored):
```powershell
Copy-Item -Recurse chroma_db chroma_db_backup_<label>
```
Roll back: stop the app, then
```powershell
Rename-Item chroma_db chroma_db_bad
Rename-Item chroma_db_backup_<label> chroma_db
.\.venv\Scripts\python.exe scripts\verify_ingest.py
```
and restart the app. `scripts\compare_indexes.py <old_path> <new_path>` compares two indexes (ids, metadata,
embeddings, text, retrieval for the eval questions). `chroma_db_backup_pre7b2` is the pre-7b2 index (decoded chunk
text); delete it when you no longer need it.

**Code.** `git log --oneline`, then `git revert <commit>` (creates a new commit and keeps history). Re-run the
test suites and the evals. Do not force-push. Pushes need the owner's approval (`CLAUDE.md`).

## 7. Update dependencies

There are two lock files: `requirements.txt` (runtime) and `requirements-dev.txt` (audit tooling); inputs are
`requirements.in` and `requirements-dev.in`. `docs\lockfiles.md` lists 15 **available upgrades, deferred**.
Do them as a separate cycle: resolve in a clean venv at a short path (`%TEMP%\kslock`), diff both lock files,
run `.\.venv\Scripts\python.exe scripts\check_env.py --audit` (audits both files), all test suites, Q1 to Q25 on
v1 and v2, and `edge_cases.py`; only then replace the lock files and rebuild the working venv.
**Never edit `scripts\audit_exceptions.txt`**: only the owner accepts an advisory (four ChromaDB server advisories
are accepted today, valid only while ChromaDB is used embedded-only; see `docs\security.md`).

## 8. Test suites (all safe to run)

```powershell
foreach ($t in 'select_final','phase6e_fixes','monitoring','exact_string_guard','chunk_offsets','config_error') {
  .\.venv\Scripts\python.exe "scripts\test_$t.py"
}
```
Expected counts (from `docs/eval-results.md`): 8, 13, 26, 27, 7, 9 passed. `test_chunk_offsets` needs the index to
exist at `CHROMA_PATH`. `scripts\test_audit_bites.py` proves the audit still fails on a vulnerable pin (needs network).

## 9. Log hygiene

- **Logged** (`query_log.jsonl`, one line per query): id, timestamp, user, outcome, latency, tokens, cost, error type
  name, count of chunks after the permission filter, `leak_scan_blocked`, `guard_action`, prompt version, model,
  question length, and a 12-character salted sha256 prefix of the question.
- **Never logged** (Hard Rule 1): question text, answer text, restricted document names, counts before the permission
  filter, exception text. The one exception: `feedback.jsonl` stores the raw question text for a **thumbs-down**
  only (so a failure can be reviewed); treat that file as sensitive.
- **Retention suggestion** (not enforced by the code): keep `query_log.jsonl` about 90 days and `feedback.jsonl`
  about 30 days; rotate by renaming the file (the report reads only `query_log.jsonl` in `LOG_DIR`). `logs\` is
  git-ignored; keep it that way.
- **Salt.** `QUERY_LOG_SALT` lets repeats be counted without storing text. Treat it like a secret and keep it in
  `.env`. Changing it breaks hash matching across the change. Blank means an unsalted hash, which short, guessable
  questions can be confirmed against.

## 10. Rotate the Anthropic API key

1. Create a new key in the Anthropic console (do not delete the old one yet).
2. Edit `.env` in an editor and replace the `ANTHROPIC_API_KEY=` value. Never print or paste it.
3. Restart the app. Run `.\.venv\Scripts\python.exe scripts\check_env.py` (expect `API key is set in .env (value not
   shown)`), then one CLI query (section 3) that returns an answer.
4. Revoke the old key in the console.
5. Watch `python -m infra.report` for `AuthenticationError` in the error types for a day (a wrong key shows to
   users as "Service unavailable").

If a key may have been exposed, follow "If something looks wrong" in `docs\security.md`.

## 11. Known limitations

Details and evidence: `docs/eval-results.md` and `docs/edge-cases.md`.

- **Non-English questions** are refused: the embedding model is English-only (best-chunk distance 0.8459 for the
  Spanish and 0.7429 for the Japanese question vs 0.1950 for the English one; `docs/edge-cases.md`). The fix needs
  a multilingual model and a full re-baseline.
- **No relevance cutoff**: the top results are always passed to the model; refusal depends on the model, not a
  similarity threshold.
- **Exact-string guard gaps**: `clarifying_question` is not scanned; a capitalized label after a stray ". " and
  spaces inside an email's local part fail closed (blocked).
- **Leak-scan false positive** on refusals that echo a restricted title (section 3).
- **The older-version conflict note is written by the model**: the older section is always retrieved and shown
  to it, but one captured UI run omitted the note (`docs/eval-results.md`, 7c findings, row 8).
- **Tiny corpus and eval set**: 15 documents, 65 chunks, 25 questions; one superseded document pair; the held-out
  questions Q21 to Q24 are not blind and Q25 does not discriminate; the injection test is 3 samples of one style.
- **Single user, demo access control**: the sidebar user switcher is not authentication; `access.json` is a stand-in
  for source-system ACLs.
- **Unexpected UI exceptions are not logged** (section 4).
