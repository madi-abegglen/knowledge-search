# Lockfile rebuild proposal (7b3-C): PENDING APPROVAL

> **Revised by the owner: NO-DRIFT split.** The runtime/dev split below is approved, but with the exact current
> pins (zero version changes). The 15 drifted packages in the table are deferred as "available upgrades" for a
> separate cycle. The `*.proposed.txt` files and `after_7b3_newlock_v1.json` referenced below were removed and
> are not part of the repo; the numbers are kept as the evidence behind the deferred-upgrade list.

Nothing here replaces the working `.venv` or the committed `requirements.txt`.

## What was done

A clean venv was created in a scratch folder (not the working venv; short path because Windows' 260 character
limit broke the first attempt under a long path), then:

1. `pip install --only-binary=:all: -r requirements.in` -> freeze -> `requirements.proposed.txt` (118 packages).
2. `pip install --only-binary=:all: -r requirements-dev.in` on top -> freeze; the 16 packages added are
   `requirements-dev.proposed.txt` (pip-audit and its dependency tree).

## Problem with the committed lockfile

`requirements.txt` is one freeze of the working venv (134 packages). It mixes the app's runtime packages with the 16
audit-tooling packages (pip-audit, cyclonedx-python-lib, CacheControl, license-expression, ...), so anything that
installs "the app" also installs the audit tool chain, and the runtime lock is not reviewable on its own.
`requirements.in` and `requirements-dev.in` were already separate; only the lock was not.

## Proposed split

| File | Contents | Used for |
|---|---|---|
| `requirements.in` | direct runtime dependencies (unchanged) | human-edited minimums |
| `requirements.txt` | pinned runtime lock, 118 packages | running the app; `check_env.py` completeness check; **pip-audit (runtime)** |
| `requirements-dev.in` | `pip-audit` (unchanged) | human-edited |
| `requirements-dev.txt` | pinned dev/audit tooling, 16 packages | recreating the audit tool; **pip-audit (dev)** |

**Which file the audit reads.** Today `check_env.py --audit` runs `pip_audit -r requirements.txt`. After the split
it should audit BOTH files (two runs, same accepted exceptions), which is stricter, not looser. This needs a small
`scripts/check_env.py` change that was NOT made here (the audit script is a security control; it changes only with
your approval). Recreating the environment becomes
`pip install --only-binary=:all: -r requirements.txt -r requirements-dev.txt`.

## Version diff, committed lockfile -> proposed

No package was added to or removed from the runtime set; 16 moved from the single lock into the dev lock.
15 runtime packages differ, all because the new venv resolved the newest compatible release at build time (nothing
in `requirements.in` was changed):

| Package | Old | New | Kind | Note |
|---|---|---|---|---|
| anthropic | 1.11.0 | 1.13.0 | direct | minor bump; API calls, error classes and timeouts re-tested |
| pydantic | 2.13.5 | 2.14.1 | direct | strict answer schema; schema and guard tests re-run |
| pydantic_core | 2.46.5 | 2.50.1 | transitive | pairs with pydantic |
| pypdf | 6.19.0 | 6.20.0 | direct | still above the security minimum 6.14.1 |
| aiohttp | 3.14.4 | 3.14.5 | transitive | patch |
| filelock | 4.0.12 | 4.1.1 | transitive | minor |
| httptools | 0.8.0 | 0.9.0 | transitive | minor |
| kubernetes | 37.0.0 | 37.0.1 | transitive (chromadb) | patch |
| multidict | 6.9.1 | 7.1.0 | transitive (aiohttp/yarl) | major; no direct use |
| narwhals | 2.26.0 | 2.27.1 | transitive (streamlit/altair) | minor |
| numpy | 2.5.3 | 2.5.4 | transitive | patch |
| onnxruntime | 1.30.0 | 1.31.0 | transitive (chromadb) | minor |
| orjson | 3.12.0 | 3.13.0 | transitive | minor |
| tenacity | 9.1.4 | 9.2.1 | transitive | minor |
| tokenizers | 0.23.2 | 0.23.3 | transitive | patch; tokenization is checked by the chunk-offset tests |

Dev tooling: `platformdirs` 4.12.3 -> 4.13.0 (only change among the 16).
Unchanged and still the accepted-advisory subjects: `chromadb==1.5.9`. `streamlit==1.65.0`, `torch==2.14.1`,
`sentence-transformers==6.1.0`, `transformers==5.19.0` are identical.

## Evidence the proposed set works

- `pip-audit -r requirements.proposed.txt` (same 4 accepted ChromaDB exceptions): exit 0, "No known vulnerabilities
  found, 5 ignored". `pip-audit -r requirements-dev.proposed.txt`: exit 0, no known vulnerabilities.
- Running the repo against the new set (clean venv): test_select_final 8, test_phase6e_fixes 13, test_monitoring 26,
  test_exact_string_guard 27, test_chunk_offsets 7, test_config_error 9 all passed; Q1-Q25 v1 on the real index:
  25/25, 0 leaks, 8/8 conflicts flagged, 0 false conflict notes (`evals/results/after_7b3_newlock_v1.json`).
- Not run under the new set: `verify_phase5_ui` and `edge_cases` (run on the working venv only).

## To adopt (after approval)

1. Copy the two proposed files over `requirements.txt` and add `requirements-dev.txt` (UTF-8).
2. Update `scripts/check_env.py --audit` to audit both files; keep exceptions identical.
3. Update `docs/environment-setup.md` steps 3 and 3a/3b and the Security notes.
4. Recreate the working venv from the new files and re-run the full verification.
