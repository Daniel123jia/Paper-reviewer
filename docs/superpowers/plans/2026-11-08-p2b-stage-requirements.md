# P2-B Stage Requirements Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give P2-B an auditable optional review requirement without inheriting P2-A execution constraints.

**Architecture:** Extend the P2-B API request and run row with one nullable field. Persist the P2-B value at creation, read it from the P2-B row during execution, and pass it to the existing `run_p2b` input without modifying prompts or schemas.

**Tech Stack:** FastAPI, Pydantic, SQLite, pytest.

---

### Task 1: Reproduce the stage leak and define the API contract

**Files:**
- Modify: `backend/tests/test_p2b_api.py`

- [ ] Add a Fake Model test whose completed P2-A row stores a sentinel P2-A
  `special_requirements`; assert none of six P2-B user messages contains it.
- [ ] Add a request-body test with `review_requirements`; assert every P2-B
  user message contains it and the run detail returns the persisted value.
- [ ] Add a default-body test proving all six dimensions execute with no
  forced candidate count.
- [ ] Run the tests before implementation and confirm failures are caused by
  the missing P2-B request contract and the existing P2-A leak.

### Task 2: Implement the minimal persistence and routing change

**Files:**
- Modify: `backend/app.py`

- [ ] Add `ProfessionalReviewRunRequest` with nullable, bounded
  `review_requirements`.
- [ ] Add the nullable column to new table definitions and migrate existing
  `professional_review_runs` tables when the column is absent.
- [ ] Accept the optional request body, persist its value, and pass the P2-B
  row's value to `run_p2b`; retain P2-A `target_journal` and remove only the
  P2-A `special_requirements` data path.
- [ ] Run the new API tests and confirm all pass.

### Task 3: Verify failure safety and downstream gating

**Files:**
- Modify: `backend/tests/test_p2b_api.py`
- Modify: `backend/tests/test_p2c_api.py` only if existing coverage cannot
  express the required assertion.

- [ ] Add or tighten a Fake Model unknown-usage test showing D6 stops without
  a seventh supplier call and persists D1-D5 plus the unknown trace.
- [ ] Assert a `partial_failed` P2-B row cannot create P2-C, while an existing
  complete/limited fixture remains accepted.
- [ ] Assert historical rows migrate with null `review_requirements` and P2-A
  data remains byte-for-byte unchanged.

### Task 4: Document and verify

**Files:**
- Modify: `docs/p2b-professional-review.md`

- [ ] Document stage isolation, API body, migration compatibility, the real
  run audit, and the non-binding 240k input / 8-call recommendation.
- [ ] Run P2-B/P2-C targeted tests, full backend tests, P5 deterministic
  acceptance, frontend production build, and relevant browser regression.
- [ ] Scan the diff and repository for exposed credentials.
- [ ] Commit, push the branch, create one PR, and wait for GitHub CI without
  merging.
