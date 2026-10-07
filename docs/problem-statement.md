# Problem Statement: Knowledge Search

## Problem
Employees waste time hunting for answers across scattered documents, or interrupt colleagues who already know. Answers are often outdated or conflicting, and people may find (or be shown) content they are not cleared to see.

## Who asks, and what
- **Primary users:** all employees (HR, IT, finance, procurement, sales policies and processes).
- **Typical questions:** "How many PTO days do new hires get?", "Who approves a $10k vendor?", "How do I report a phishing email?"
- **Frequency (assumed for demo):** ~5 lookups per employee per week.

## Baseline (today, assumed)
- Finding one policy answer: ~10-15 min (search Drive, open several files, ask a colleague or HR).
- Conflicting/old versions are common; no citation or "last updated" signal.
- Access is controlled per file, with no check at answer time.

## Success metrics
| # | Metric | Target |
|---|---|---|
| 1 | Answer accuracy with correct citation on in-scope questions | >= 80% (stretch: 90%) |
| 2 | Time to answer | < 10 seconds |
| 3 | Permission leaks (restricted content shown to unauthorized user) | **0** |
| 4 | Correct refusal when the answer isn't in the docs | 100% on test set (no invented answers) |

## Out of scope (for tonight)
- Real SSO / live ACL sync (demo uses `data/access.json`; production mirrors source ACLs)
- OCR / scanned documents
- Slack/Teams interface

## Open questions
- Which real source first (Drive, SharePoint, Confluence)?
- How stale can an answer be before it is harmful?
- Which doc categories need a "verify with HR/Legal" footer?
