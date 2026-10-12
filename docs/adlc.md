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
