# P2-A N3 Checkpoint Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operator-authorized P2-A continuation that inherits validated N1/N2 results, persists validated N3 partitions, and resumes only missing N3 work without changing historical failed runs.

**Architecture:** A focused `p2a_resume` module owns lineage, stable N3 partition descriptors, checkpoint validation, and inherited metadata rebinding. The existing P2-A engine exposes its unchanged N3 mapping phase as a reusable function, while FastAPI/SQLite provide transactional continuation creation, checkpoint persistence, status queries, and background execution.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic v2, pytest, existing Fake OpenAI clients.

---

### Task 1: Freeze the N3 partition contract

**Files:**
- Create: `backend/p2a_resume.py`
- Create: `backend/tests/test_p2a_resume.py`

- [ ] **Step 1: Write failing descriptor tests**

Add tests that construct the existing two-unit fixture and assert that descriptors are stable across different run IDs, but change when ordered unit text, PaperProfile content, target journal, special requirements, Source Bundle ID, snapshot, PDF SHA, prompt version, or schema version changes.

- [ ] **Step 2: Verify RED**

Run:

```bash
python -m pytest backend/tests/test_p2a_resume.py -q
```

Expected: collection or assertion failure because `backend.p2a_resume` and descriptor functions do not exist.

- [ ] **Step 3: Implement the minimal descriptor API**

Create immutable `N3PartitionDescriptor` records and `build_n3_partition_descriptors(...)`. Use the current 9,000-character chunking, current contribution-mapper prompt/version, exact prompt payload, ordered source-unit IDs, and canonical JSON. Exclude only the run-specific ReviewPlan/PaperProfile `review_run_id` from fingerprint material.

- [ ] **Step 4: Validate checkpoint payloads**

Add `validate_checkpoint(...)` that verifies partition identity, source metadata, prompt/schema version, result SHA-256, Pydantic `ClaimEvidenceMap`, exact included-unit coverage, and `validate_source_references` against the frozen bundle.

- [ ] **Step 5: Verify GREEN and commit**

Run the new test file and commit the descriptor/checkpoint contract.

### Task 2: Extract the existing N3 execution phase

**Files:**
- Modify: `backend/p2a_engine.py`
- Modify: `backend/tests/test_p2a_engine.py`

- [ ] **Step 1: Write failing engine tests**

Add a Fake Model test with five N3 partitions where partitions 1–3 succeed and partition 4 raises a connection exception. Assert an `on_n3_checkpoint` callback receives exactly three fully validated maps before `UnknownUsageError`, and no fifth request occurs.

Add a second test supplying validated partitions 1–3 to a resume function. Assert it calls only partitions 4–5 plus `contribution-normalizer`, never calls planner/understanding/source-consistency, and returns a complete ClaimEvidenceMap bound to the new run ID.

- [ ] **Step 2: Verify RED**

Run the two tests and confirm failure because N3 cannot be invoked independently and no checkpoint callback exists.

- [ ] **Step 3: Implement `run_p2a_n3`**

Move only the existing contribution-mapper loop and `_merge_claim_maps` call behind a reusable function. Accept validated checkpoint maps indexed by partition ID plus `on_n3_checkpoint`. Persist/callback immediately after `_invoke` finishes Schema and Evidence validation. Keep all prompts, chunk size, merge behavior, budget logic, and fail-closed exceptions unchanged.

- [ ] **Step 4: Integrate full P2-A**

Make `run_p2a` call `run_p2a_n3` with no inherited checkpoints. Add an optional callback so future full runs also persist valid N3 work. Confirm existing P2-A outputs are unchanged.

- [ ] **Step 5: Verify GREEN and commit**

Run `test_p2a_engine.py` and commit the engine extraction.

### Task 3: Add lineage/checkpoint storage and migration

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/p2a_resume.py`
- Modify: `backend/tests/test_p2a_api.py`

- [ ] **Step 1: Write failing database compatibility tests**

Start from both a fresh database and the historical `paper_understanding_runs` schema fixture. Assert creation of `paper_understanding_run_lineage`, `paper_understanding_n3_checkpoints`, their indexes, and the one-active-P2-A-per-document partial unique index without altering old rows.

- [ ] **Step 2: Write failing transaction tests**

Inject checkpoint persistence failure after a validated partition and assert the supplier-call count does not advance to the next partition. Assert no half-written validated checkpoint is visible. Simulate restart and verify the run becomes failed without replay while committed checkpoints remain.

- [ ] **Step 3: Implement schema and storage helpers**

Create both tables exactly as specified in the design. Add helpers that insert generated checkpoints transactionally, copy inherited checkpoints with explicit origin IDs, validate stored hashes before reuse, and query redacted checkpoint metadata.

- [ ] **Step 4: Integrate full-run checkpoint persistence**

Pass a database checkpoint callback into ordinary `run_understanding`. Persist only after engine validation. Do not change existing result or model-call schemas.

- [ ] **Step 5: Verify GREEN and commit**

Run migration, transaction, restart, and existing P2-A API tests; commit storage integration.

### Task 4: Add the operator-authorized continuation API

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/p2a_resume.py`
- Modify: `backend/tests/test_p2a_api.py`

- [ ] **Step 1: Write failing API tests**

Cover:

- failed parent with valid N1/N2 and no map creates one new queued continuation;
- the new run records parent/source lineage and copied N1/N2 with the new run ID;
- model calls contain only missing N3/normalizer calls;
- duplicate/concurrent starts return 409;
- completed parent, missing N1/N2, existing final map, snapshot/PDF mismatch, prompt/schema drift, corrupt checkpoint, bad hash, bad Evidence reference, and malformed environment file fail before supplier dispatch;
- secrets and raw provider messages never appear in responses or stored errors.

- [ ] **Step 2: Verify RED**

Run focused API tests and confirm the route is absent.

- [ ] **Step 3: Implement transactional creation**

Add `POST /api/understanding-runs/{parent_run_id}/n3-resume-runs`. Perform model preflight, `BEGIN IMMEDIATE`, parent/current-document validation, N1/N2 rebinding and validation, checkpoint validation/copy, active-run check, run creation, and lineage creation before scheduling one background task.

- [ ] **Step 4: Implement continuation execution**

Before creating a model session, revalidate current configuration, lineage, bundle, inherited N1/N2, and checkpoints. Run only missing N3 partitions through the shared `RunBudget`. Persist traces/checkpoints, final map, and `completed` only after `validate_understanding_snapshot`. On any error, preserve inherited content and checkpoints, store a redacted terminal error, and do not auto-create another run.

- [ ] **Step 5: Extend read-only run details**

Return lineage and redacted checkpoint metadata/counts from `GET /api/understanding-runs/{run_id}`. Historical runs return `lineage: null` and an empty checkpoint list.

- [ ] **Step 6: Verify GREEN and commit**

Run focused API and concurrency tests and commit the continuation API.

### Task 5: Prove P2-B compatibility and cost audit

**Files:**
- Modify: `backend/tests/test_p2a_api.py`
- Modify: `backend/tests/test_p2b_engine.py`
- Update: `docs/p2a-paper-understanding.md`

- [ ] **Step 1: Add end-to-end Fake Model continuation test**

Simulate N1/N2 inherited, N3 partitions 1–3 committed, partition 4 unknown Usage, then an operator-created child continuation. Assert only partitions 4–5 and normalization are called; the final run is completed; `validate_understanding_snapshot` passes; and creating P2-B from it is accepted.

- [ ] **Step 2: Add usage/audit assertions**

Assert inherited N1/N2 and inherited checkpoints create no model-call rows, no token totals, and no cost. Unknown Usage remains null. Generated calls belong only to their generating run.

- [ ] **Step 3: Document operations**

Document the route, required parent state, lineage fields, checkpoint summary, explicit authorization requirement, first-continuation all-five-partition cost, later missing-only behavior, and fail-closed conditions.

- [ ] **Step 4: Run focused regressions and commit**

Run P2-A/P2-B/API tests and commit compatibility/docs.

### Task 6: Complete verification and PR delivery

**Files:**
- No production changes unless verification exposes a defect covered by a new failing test.

- [ ] **Step 1: Run all backend tests**

```bash
python -m pytest backend/tests -q
```

- [ ] **Step 2: Run the frontend build and browser regressions**

Use isolated data and no real model environment. Run the production build, PDF/legacy browser tests, and P4 report browser test.

- [ ] **Step 3: Run repository safety checks**

Confirm no Prompt, Manifest, PDF, reference answer, historical private database, API key, provider response, or frozen result entered the diff. Confirm no real model request occurred.

- [ ] **Step 4: Push and create one PR**

Push `feature/p2a-n3-checkpoint-resume`, create a PR to `main`, record HEAD and Tree SHA, wait for all required CI, and do not merge.
