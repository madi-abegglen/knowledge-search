# Lockfiles: runtime and audit tooling (7b3-C, done)

## Layout

| File | Contents | Edited by |
|---|---|---|
| `requirements.in` | direct runtime dependencies with security minimums | a person |
| `requirements.txt` | exact pins of the runtime set: 118 packages | regenerated, then reviewed |
| `requirements-dev.in` | `pip-audit` | a person |
| `requirements-dev.txt` | exact pins of pip-audit and its 15 dependencies: 16 packages | regenerated, then reviewed |

Before this change `requirements.txt` was one freeze of the working venv (134 packages) and mixed the app with the
audit tool chain. The split moved 16 existing lines from `requirements.txt` to `requirements-dev.txt` **with no
version changes**: every pin is identical to the previous lockfile (`scripts/split_lockfile.py` proves it: every
original line appears exactly once across the two files byte for byte; `git diff` of `requirements.txt` is 16
deletions and 0 additions).

Moved to `requirements-dev.txt`: boolean.py, CacheControl, cyclonedx-python-lib, defusedxml, license-expression,
msgpack, packageurl-python, pip-requirements-parser, pip_api, pip_audit, platformdirs, py-serializable,
pyparsing, sortedcontainers, tomli, tomli_w.

## Install and audit

- Run the app: `python -m pip install --only-binary=:all: -r requirements.txt`
- Developer / audit tooling: add `-r requirements-dev.txt`
- `python scripts/check_env.py --audit` runs pip-audit against BOTH files with the same accepted exceptions
  (`scripts/audit_exceptions.txt`, unchanged). Any new finding in either file, or a missing file, is a FAIL.
  `scripts/test_audit_bites.py` proves it still bites: a known-vulnerable pin appended to a scratch copy of either
  file makes the audit exit non-zero.

## Verification of the split

- Clean venv in a short path (`%TEMP%\kslock`) from `requirements.txt` ONLY: installed set equals the file
  (118 = 118, 0 differences), `pip check` clean, pip-audit not importable (so the runtime lock really is
  tool-free), app and tooling imports OK, test suites 8 + 13 + 26 + 27 + 7 + 9 passed.
- `requirements-dev.txt` installed on top: `pip check` clean, pip-audit 2.10.1 runs, both files audit with exit 0
  (4 accepted ChromaDB advisories ignored for the runtime file; none needed for the dev file); the combined freeze
  equals both lock files (134 = 134, 0 differences).
- Working venv and real index: Q1-Q25 25/25, 0 leaks, 8/8 conflicts; `verify_phase5_ui` exit 0; edge cases
  30 PASS + 2 documented findings; real index 65 chunks; `data/` fingerprint `82be1429dbfe5dd4`.

## Available upgrades, deferred

A fresh resolve of `requirements.in` today (clean venv, 2026-10-12) picks newer releases of 15 runtime packages
than the pins above. **Deferred: do as a separate cycle with a full eval run** (all suites, Q1-Q25 on v1 and v2,
edge cases, audit). They are listed here so the choice is visible, not because they are needed.

| Package | Locked | Available | Kind | Why it needs care |
|---|---|---|---|---|
| anthropic | 1.11.0 | 1.13.0 | direct | the model client: timeouts, retries, error classes |
| pydantic | 2.13.5 | 2.14.1 | direct | strict answer schema validation |
| pydantic_core | 2.46.5 | 2.50.1 | transitive | pairs with pydantic |
| pypdf | 6.19.0 | 6.20.0 | direct | PDF ingestion (still above the 6.14.1 security minimum) |
| multidict | 6.9.1 | 7.1.0 | transitive (aiohttp/yarl) | **major** version, no direct use |
| aiohttp | 3.14.4 | 3.14.5 | transitive | patch |
| filelock | 4.0.12 | 4.1.1 | transitive | minor |
| httptools | 0.8.0 | 0.9.0 | transitive | minor |
| kubernetes | 37.0.0 | 37.0.1 | transitive (chromadb) | patch |
| narwhals | 2.26.0 | 2.27.1 | transitive (streamlit/altair) | minor |
| numpy | 2.5.3 | 2.5.4 | transitive | patch |
| onnxruntime | 1.30.0 | 1.31.0 | transitive (chromadb) | minor |
| orjson | 3.12.0 | 3.13.0 | transitive | minor |
| tenacity | 9.1.4 | 9.2.1 | transitive | minor |
| tokenizers | 0.23.2 | 0.23.3 | transitive | patch; tokenization underlies chunking (chunk-offset tests) |

Dev tooling: `platformdirs` 4.12.3 -> 4.13.0 is the only available change.
An earlier trial of this full set under a clean venv passed all suites and Q1-Q25 (25/25, 0 leaks, 8/8
conflicts), so the upgrade is expected to be uneventful, but it was not adopted. Unchanged by design:
`chromadb==1.5.9` (accepted advisories, see `docs/security.md`), `streamlit`, `torch`, `sentence-transformers`,
`transformers`.
