# P2 Unknown-Usage Fail-Closed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent every P2-A through P3 supplier request after the first request with unknown usage while preserving completed partial outputs and redacted audit evidence.

**Architecture:** Add a latched `UnknownUsageError` to the existing shared `RunBudget`, then make each engine persist a safe trace and immediately propagate it. Keep content validation retry behavior unchanged when usage is complete.

**Tech Stack:** Python 3.11, Pydantic, pytest, SQLite/FastAPI integration tests.

---

### Task 1: Shared budget fail-closed gate

**Files:**
- Modify: `backend/p2a_engine.py`
- Test: `backend/tests/test_p2a_engine.py`

- [x] Add failing tests for a fourth-call connection error, missing usage, latched subsequent-call rejection, configuration failure, and valid-usage content repair.
- [x] Run the focused tests and confirm they fail because unknown usage is not terminal.
- [x] Add `UnknownUsageError` and conservative, latched accounting to `RunBudget.call`.
- [x] Persist one P2-A unknown-usage trace and re-raise through `_invoke` and `_aux_json`.
- [x] Prevent global profile fallback and recursive chunk splitting from catching the unknown-usage abort.
- [x] Re-run focused P2-A tests.

### Task 2: P2-B and P2-C propagation

**Files:**
- Modify: `backend/p2b_engine.py`
- Modify: `backend/p2c_grouping.py`
- Test: `backend/tests/test_p2b_engine.py`
- Test: `backend/tests/test_p2c_engine.py`

- [x] Add failing fake-model tests proving no next dimension, pair batch, retry, or reconciliation call occurs after unknown usage.
- [x] Run the tests and confirm the extra calls occur on current code.
- [x] Persist redacted unknown-usage traces and immediately propagate the shared exception.
- [x] Re-run focused P2-B/P2-C tests.

### Task 3: P3 propagation and fallback exclusion

**Files:**
- Modify: `backend/p3_engine.py`
- Test: `backend/tests/test_p3_engine.py`

- [x] Add a failing fake-model test proving unknown usage cannot enter technical/adjudication fallback or the next verification task.
- [x] Persist a redacted unknown-usage trace and re-raise before scientific fallback.
- [x] Re-run focused P3 tests.

### Task 4: API status, restart, and partial-result regression

**Files:**
- Test: `backend/tests/test_p2a_api.py`
- Test: relevant existing P2-B/P2-C/P3 API test modules.

- [x] Verify failed status and null usage are queryable, prior ReviewPlan/PaperProfile remain stored, and ClaimEvidenceMap remains absent.
- [x] Verify recreating the app does not replay failed requests.
- [x] Verify errors contain neither credentials, raw request text, nor raw supplier messages.

### Task 5: Complete verification and delivery

**Files:**
- Update: technical documentation only if behavior needs operator explanation.

- [x] Run focused P2-A/P2-B/P2-C/P3 tests.
- [x] Run the complete backend suite and P5 deterministic acceptance.
- [x] Run the frontend production build and existing browser regressions.
- [x] Scan the diff for frozen assets and sensitive values.
- [ ] Commit, push `fix/p2-unknown-usage-fail-closed`, create a PR to `main`, and wait for CI without merging.
