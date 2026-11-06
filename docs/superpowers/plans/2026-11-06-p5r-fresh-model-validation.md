# P5-R Fresh Model Validation Preparation Plan

> **For Codex:** Execute this plan incrementally. Stop before every real-model or paid call until the user explicitly authorizes it.

**Goal:** Prepare a reproducible, privacy-safe P5-R continuation workflow that verifies the frozen papers and evaluation assets, reports the actual runtime configuration without exposing credentials, and makes the first real-model run auditable before any cost is incurred.

**Architecture:** Add an isolated `scripts/p5r` preflight layer around the frozen P5 manifest and reference sets. The preflight is deterministic and read-only: it loads the same runtime environment as the backend, verifies canonical paper identities and PDF hashes, checks blind-reference consistency, calculates hard run ceilings, and emits a sanitized readiness record. It does not alter P1–P4 code, prompts, verdict rules, or frozen evaluation data.

**Tech Stack:** Python 3, Pydantic contracts already used by P5, pytest, existing GitHub Actions and frontend regression suite.

---

### Task 1: Freeze the execution baseline and define failing preflight tests

**Files:**
- Create: `backend/tests/test_p5r_preflight.py`
- Inspect only: `docs/examples/p5-regression/evaluation-manifest.json`
- Inspect only: `docs/examples/p5-holdout-1/blind-reference.json`
- Inspect only: `docs/examples/p5-holdout-2/blind-reference.json`

1. Assert that all three canonical paper IDs resolve to exactly one manifest entry.
2. Assert that historical aliases are recorded but never replace canonical IDs.
3. Assert that exact PDF hashes are required and a mismatched file fails closed.
4. Assert that blind-reference paper IDs and PDF hashes match the manifest.
5. Run the focused test and confirm it fails because the P5-R preflight module does not exist yet.

### Task 2: Implement the deterministic, sanitized preflight

**Files:**
- Create: `scripts/p5r/__init__.py`
- Create: `scripts/p5r/preflight.py`
- Modify: `backend/tests/test_p5r_preflight.py`

1. Reuse the existing local-environment loader and public environment snapshot; never serialize key material or endpoint URLs.
2. Accept explicit `paper_id=pdf_path` mappings and validate canonical identity, expected SHA-256, and reference-set consistency.
3. Record the requested model, endpoint classification, SDK/runtime readiness, configured/unconfigured credential status, and the fact that the capability probe has not run.
4. Record per-paper ceilings of 90 calls, 1,200,000 input tokens, 220,000 output tokens, 10,800 seconds, and two retries, plus aggregate ceilings.
5. Mark USD cost as unknown unless an explicit, non-secret pricing configuration is available; never infer pricing for a custom provider.
6. Emit a versioned JSON record with explicit blockers and `ready_for_paid_run=false` until a separately authorized capability probe succeeds.
7. Run focused tests until green.

### Task 3: Validate the real frozen assets without model calls

**Files:**
- Create locally but do not commit: `data/p5r/sources/*.pdf`
- Create: `docs/examples/p5r/preflight.json`

1. Copy the three already-frozen public PDFs into the ignored P5-R data directory.
2. Verify the copied bytes against all expected hashes before using them.
3. Run the preflight with the three explicit canonical mappings.
4. Inspect the output for absolute paths, secrets, endpoint hosts, tokens, raw prompts, or unpublished material before committing the sanitized record.
5. Confirm that no model/network capability probe was executed.

### Task 4: Document P5-R status and the authorization gate

**Files:**
- Create: `docs/p5r-fresh-model-validation.md`
- Create: `docs/p5r-scientific-quality-results.md`
- Create: `docs/p5r-report-quality-evaluation.md`
- Create: `docs/p5r-final-assessment.md`

1. Document the frozen baseline, branch, canonical papers, exact hashes, and immutable P1–P4 constraints.
2. Document requested versus actual model identification requirements, timeout/retry behavior, and privacy controls.
3. Publish the per-paper and aggregate hard ceilings; separate token ceilings from unknown monetary cost.
4. State that scientific quality, report quality, and ChatGPT web comparison are `pending`/`not assessable`, not passed.
5. State the exact next-step gate: before Shaw, present the forecast and obtain explicit authorization; if Shaw fails, do not proceed to SARDet or SAM.

### Task 5: Verify and deliver the preparation PR

**Files:**
- Test: `backend/tests/test_p5r_preflight.py`
- Test: all backend tests
- Test: frontend production build
- Test: existing browser regression suites

1. Run focused P5-R tests.
2. Run the complete backend regression suite.
3. Install frontend dependencies if absent, then run the production build.
4. Run the existing browser regression suites without starting model work.
5. Review `git diff`, confirm P1–P4 core files and frozen references were not modified, and scan tracked changes for secrets.
6. Commit, push `feature/p5r-fresh-model-validation`, and open an independent PR.
7. Report the commit, PR, CI status, verified assets, budget forecast, and blockers. Stop before any real-model call.

### Explicitly gated follow-up (not part of this execution)

After user authorization, run a minimal capability probe that records requested and actual model IDs, real usage fields (including unknown usage), latency, and endpoint behavior. Only if that succeeds may a separate run start Shaw 2018. SARDet-100K and SAM remain sequentially gated by the prior paper's result.
