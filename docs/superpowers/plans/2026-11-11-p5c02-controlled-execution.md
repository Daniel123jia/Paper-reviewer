# P5-C0.2 implementation plan

1. Add failing tests for SQLite authorization and trial uniqueness, concurrent claim, duplicate response, mismatched identity/upstream/budget, and crash-before-response. Keep all test assets synthetic and use a Fake model client.
2. Implement a `scripts/p5c` stage ledger with explicit immutable grants, a stable C1 Run ID, a unique claim, per-request persisted reservation and Usage settlement. Unknown Usage and dispatch interruption latch failure and block further dispatch. No secret values or blind questions in rows or returned audit.
3. Add an explicit C1 process mode and gate all legacy paid API entry points in that mode. Wire P2-A/P2-B/P2-C/P3 session creation to the C1 ledger before SDK calls. Reuse existing scientific stage functions and current P4 provider budget store. C1 mode accepts only one `internal:zh` draft.
4. Add a private, offline stage-grant command that validates the final reviewed execution lock, one-paper PDF and upstream stage snapshot, model configuration, pricing status and explicit budget. The command cannot create a model request. Duplicate grant and dispatch cannot create a second stage.
5. Add read-only C1 audit and recovery views. A pending dispatch after process restart is unknown, not retried. Completed upstream results remain available. Each stage stops after its own result; no automatic next-stage scheduling.
6. Run focused Fake Client tests, complete backend tests, P5 deterministic acceptance, frontend build, browser regressions and GitHub CI. Scan staged diff for credentials and frozen-file changes. Push one PR; do not merge or create the final execution lock.

Changed files: `scripts/p5c/stage_control.py`, `scripts/p5c/authorize_stage.py`, `backend/p5c_runtime.py`, `backend/app.py`, `backend/p4_api.py`, `backend/p4_drafts_api.py`, `backend/tests/test_p5c_stage_control.py`, and `docs/p5c-controlled-execution.md`. Historical P5 runner and P1–P4 science modules stay untouched.

Risk boundary: the existing P2-A–P3 production model SDK path is not idempotent by itself; the C1 gate and request ledger must be integrated at the actual client call boundary. A wrapper that only polls API status would not satisfy this design.
