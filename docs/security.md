# Security Notes: Knowledge Search

Short, proportional notes for a local demo. Not a full security program.

## Scope and threat model
- Local app on a developer machine, trusted sample documents, not exposed to a network.
- Main risks: (1) dependency/supply-chain compromise, (2) leaked secrets, (3) permission leaks in answers, (4) prompt injection via document text, (5) untrusted PDFs or model files.

## Controls
| Risk | Control | Where |
|---|---|---|
| Supply chain (worms, typosquats) | Exact package names; minimum versions; wheels only (`--only-binary=:all:`); `pip-audit`; review lockfile diffs; worm-persistence checks | `environment-setup.md` steps 3, 3a, 3b |
| Secrets | `.env` git-ignored; key never printed, logged, or pasted into chat; `.env.example` has placeholders only | `environment-setup.md` steps 5-8 |
| ChromaDB server CVEs | Use the **embedded persistent client only**. Never run `chroma run` or expose a Chroma server | Architecture decision |
| Streamlit (Windows NTLM leak, fixed in 1.54.0) | `streamlit>=1.54.0`; run bound to localhost: `streamlit run app/ui.py --server.address localhost` | `requirements.in`, README |
| Model loading | Fixed embedding model (`all-MiniLM-L6-v2`); never set `trust_remote_code=True`; don't load unknown model repos | `ingest/index.py` |
| Malicious PDFs | `pypdf>=6.14.1`; ingest trusted documents only; wrap parsing in a timeout/try-except | `ingest/load.py` |
| Permission leaks | Filter in the vector query before generation; identical refusal wording; eval target = 0 leaks | `CLAUDE.md` rules 1-2, Phase 6 |
| Prompt injection | Treat retrieved text as data, not instructions; injection test cases in evals | `CLAUDE.md` rule 4, Phase 6c |

## Accepted risks (reviewed 2026-10-07)
pip-audit reports four ChromaDB advisories on 1.5.9, which is the newest release on PyPI, so no fixed version exists yet (one source mentions 1.6.0, which is not published).

| Advisory | What it is | Why accepted |
|---|---|---|
| CVE-2026-45829 | Unauthenticated RCE in the Python API server | Server-only; we never run a Chroma server |
| CVE-2026-45830 | Cross-tenant data access (authenticated) | Server-only; no multi-tenant server |
| CVE-2026-45831 | Cross-tenant bypass in `SimpleRBACAuthorizationProvider` | Server-only; no RBAC provider |
| CVE-2026-45833 | Code injection via `trust_remote_code` on the server | Server-only; `trust_remote_code` never set |

**Conditions that keep this acceptable:** embedded `PersistentClient` only; no `chroma run`, `HttpClient`, or exposed port; `check_env.py` fails if server or HTTP-client usage appears in the code. **Revisit** when a fixed ChromaDB version is published (then upgrade and delete the exceptions), or if the project ever needs a Chroma server. Exceptions are recorded in `scripts/audit_exceptions.txt`; only the user adds to it.

## Ongoing habits
- Run `python scripts/check_env.py --audit` before each phase commit and before any demo. It covers the active venv, lockfile encoding and completeness, security minimum versions, known typosquats, `.env` handling, secrets not tracked by git, and pip-audit.
- Review `requirements.txt` and `requirements-dev.txt` diffs before committing; unexpected new packages are a red flag. `--audit` runs pip-audit against both files with the same accepted exceptions.
- Never `pip install` a package suggested by a chat or search result without checking the exact spelling on pypi.org.

## If something looks wrong (worm indicators, unexpected hooks, odd network calls)
1. Stop; don't run more installs or open more projects in this environment.
2. Don't delete evidence; note file paths and times.
3. Rotate the Anthropic API key and any GitHub/PyPI/npm tokens that were accessible from this machine.
4. Remove the affected virtual environment and recreate it from the lockfile after the audit is clean.
5. Review the git history and any `.github/` workflows for unexpected commits.

## Advisories reviewed (2026-10-06; secondary sources, re-verify)
- ChromaDB server: CVE-2026-45829 (unauthenticated RCE), CVE-2026-45833, CVE-2026-45830; the reports say local-only use is not affected; fix status was unclear.
- Streamlit: CVE-2026-33682 (Windows SSRF/NTLM, fixed 1.54.0); CVE-2026-10804 (low, fixed 1.53.1).
- pypdf: CVE-2026-59936 (DoS, fixed 6.14.1).
- Anthropic SDK: CVE-2026-34450 (memory-tool file permissions, fixed 0.87.0; not used here).
- Campaigns: Mini Shai-Hulud (May 2026) and a June 2026 copycat (typosquats `rlask`, `tlask`, `rsquests`, `nhmpy`; poisoned `mflux-streamlit`).

## Out of scope here (production upgrade path)
Hash-pinned lockfiles (`--require-hashes`), SBOM generation, secret scanning in CI, container or sandbox isolation, SSO, live ACL sync, a managed secret store, and a formal dependency-update policy.
