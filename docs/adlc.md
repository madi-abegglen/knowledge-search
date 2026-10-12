# ADLC: how this project is built

ADLC = agent development life cycle. It is the loop used for every phase of this build. The goal: small steps, evidence at each step, and a human review before anything is committed or pushed.

## The loop

| # | Step | Evidence it leaves |
|---|---|---|
| 1 | Specify the phase and its "Done when" | Phase entry in `docs/build-plan.md` |
| 2 | Build one phase only | Files in the repo |
| 3 | Verify files and paths | File listing |
| 4 | Run the Done-when check | Raw command output |
| 5 | Audit the environment | `check_env.py --audit` exit 0 |
| 6 | Review gate (human) | `git status`, diff, my approval |
| 7 | Commit, then push on approval | Commit hash, push output |
| 8 | Stop and wait | No next-phase work |

## Why each step exists (lessons from this build)

- **Verify files and paths (3).** In Phase 3 the agent said it had written the ingest files, but they were missing. Only a file listing proved it.
- **Raw output (4).** Summaries can hide failures. Raw output let a reviewer catch a pattern that matched the wrong line (`trust_remote_code=False` flagged by a naive search).
- **No inline `python -c` (4).** Shell quoting breaks differently on PowerShell and Git Bash. Scripts under `scripts/` run the same everywhere.
- **Audit (5).** Dependencies are a security surface. The check blocks typosquats and known-vulnerable versions; accepted risks are written down in one file only the owner edits.
- **Review gate (6).** The owner reads the evidence before commit. This caught a design flaw (permissions stored as a delimited string) before it spread.
- **Stop (8).** One phase at a time keeps changes reviewable and rollbacks cheap.

## Phase template

For each phase, record:

1. Goal in one sentence
2. Done-when check (exact command)
3. Result (raw output or link to it)
4. What failed, and why
5. Decisions made, and reasons
6. Open items carried forward

## Guardrails vs. gates

The repo uses `KS-G1` to `KS-G10` for its runtime gates (load, chunk, index, input guard, permission filter, prompt delimiters, schema, citations, leak scan, fixed refusal). These are separate from the "G1 to G4" gates on the ADLC map. Repo gate `KS-G5` (permission filter inside the vector query) maps to the map's authorization gate.

### Where each `KS-G` gate is enforced

Verified against the code (file, function or constant, and what it does). Line numbers drift; search for the
names. All ten were located. Gate numbers follow the order of the list above.

| Gate | Name | Enforced in | What it does |
|---|---|---|---|
| KS-G1 | load | `ingest/load.py`: `load_documents`, `_load_one`, `REQUIRED_FRONT_MATTER` | every file needs a valid `access.json` entry, known groups and complete front matter; all errors are collected and the whole ingest aborts, indexing nothing |
| KS-G2 | chunk | `ingest/chunk.py`: `chunk_document`, `MAX_TOKENS = 256` | heading-aware chunks capped by the embedding tokenizer; `assert count <= max_tokens` per chunk and for the whole document |
| KS-G3 | index | `ingest/index.py`: `_chunk_metadata`, `main` | one boolean `grp_*` flag per group derived from `access.json`, token-cap assertion before embedding, upsert by deterministic id, stale ids deleted |
| KS-G4 | input guard | `app/answer.py`: `MAX_QUERY_CHARS`, `check_config`; `app/retrieve.py`: `retrieve` | 2,000-character cap (rejected before retrieval, no truncation); misconfiguration returns a fixed error state before anything else; blank question or a user with no valid groups retrieves nothing. **Split across two files; there is no single "input guard" function** |
| KS-G5 | permission filter | `app/retrieve.py`: `get_user_groups`, `retrieve` | the user's groups become a `{"$or": [{"grp_<g>": True}, ...]}` filter inside the vector query, so restricted chunks are never retrieved; an unknown or malformed user gets no groups (fail closed) |
| KS-G6 | prompt delimiters | `app/answer.py`: `_context_block`, `CONTEXT_START`, `CONTEXT_END`; `prompts/answer_v*.md` | retrieved text is wrapped in a delimited block marked as untrusted data, and a literal end delimiter inside a chunk is escaped |
| KS-G7 | schema | `app/answer.py`: `AnswerPayload`, `Citation` (strict, `extra="forbid"`), retry loop `for _ in range(2)` | the model must return one strict JSON object with a valid outcome; invalid output is retried once, then refused. Code fences are stripped only as a matched pair (`_strip_code_fence`) |
| KS-G8 | citations | `app/answer.py`: `allowed_sources` check in `_answer_question` | every cited source must be among the chunks that were retrieved for this user, else the attempt is rejected |
| KS-G9 | leak scan | `app/answer.py`: `_restricted_labels`, `_user_visible_texts`, `_restricted_hits` | the file names and titles of documents the user may not see must not appear in any model text that can reach the user (answer, conflict note, clarifying question, citation source and section); a hit is replaced by the fixed refusal and flagged. Refusal text is not scanned because it is never shown |
| KS-G10 | fixed refusal | `app/answer.py`: `REFUSAL_MESSAGE`, `_refusal`; `app/ui.py`: refuse branch | every refusal route returns the same constant text with no citations or notes, and the UI prints the constant, never result text |

Controls that exist but are **not** among the ten gates: the exact-string guard (`app/guard.py`, called after
schema validation), the fixed error states for configuration and API failures (`app/answer.py`), and the query log
and alerts (`infra/`).


## Change control

- Architecture decisions in `CLAUDE.md` change only with the owner's approval, and the reason is written down.
- New dependencies: ask first, then pass the audit.
- Eval changes: tune once per eval cycle. Record baseline and after-tuning scores. Bump the prompt version. Remember the eval set is small; a gain on the same 20 questions is evidence, not proof.
- Status stays in sync: `CLAUDE.md` and `docs/build-plan.md` are updated in the same commit that finishes a phase.

## Definition of done (per phase)

- Done-when check passes, with raw output saved
- Audit exits 0
- Zero permission leaks, where the phase touches retrieval, answers, UI, or logs
- Status files updated
- Committed after review; pushed only on approval
