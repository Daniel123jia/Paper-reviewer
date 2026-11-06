# P5-R2 Model Capability Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, zero-call-by-default configuration audit and a separately authorized one-call capability probe for the frozen `gpt-6.1-sol` P5-R evaluation.

**Architecture:** Keep the existing OpenAI-compatible Chat Completions SDK and frozen P1–P4 code unchanged. Add one isolated P5-R2 script that loads the same root `.env.local`, emits only a redacted public configuration report by default, and constructs the same streaming request shape used by P2-A–P3 only after an explicit execution flag and authorization acknowledgement. Validate behavior with injected fake clients so no test can reach a model service.

**Tech Stack:** Python 3.11, OpenAI Python SDK 2.x, python-dotenv, Pydantic, pytest.

---

### Task 1: Define the zero-cost audit and authorization contract

**Files:**
- Create: `backend/tests/test_p5r2_capability_probe.py`
- Create: `scripts/p5r2/__init__.py`
- Create: `scripts/p5r2/capability_probe.py`

- [ ] **Step 1: Write failing tests for public configuration validation**

Add tests that call `build_configuration_report()` with explicit environment mappings and assert that missing/placeholder credentials, placeholder or malformed endpoints, frozen-model mismatches, and missing price rates remain blocked. Assert that the serialized report contains no API key, Authorization header, raw endpoint, or endpoint hostname.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest backend/tests/test_p5r2_capability_probe.py -q`

Expected: collection fails because `scripts.p5r2.capability_probe` does not exist.

- [ ] **Step 3: Implement the minimal configuration report**

Implement constants for the frozen model, one-call limit, fixed probe input, 64-token output limit, 180-second timeout, zero SDK retries, and the exact authorization acknowledgement. Validate the effective environment without returning secret values or the raw endpoint. Return readiness blockers and a price formula based only on operator-supplied per-million-token rates.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest backend/tests/test_p5r2_capability_probe.py -q`

Expected: configuration tests pass without creating an OpenAI client.

### Task 2: Implement the single-call probe behind a fail-closed gate

**Files:**
- Modify: `backend/tests/test_p5r2_capability_probe.py`
- Modify: `scripts/p5r2/capability_probe.py`

- [ ] **Step 1: Write failing authorization and response tests**

Add tests proving the default CLI path never invokes the injected client factory, `--execute` without the exact acknowledgement is rejected before client creation, and a fake streaming response records requested/actual model, parsed JSON, usage completeness, duration, and one call. Add failure tests for absent usage, invalid JSON, model mismatch, and sanitized provider exceptions.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest backend/tests/test_p5r2_capability_probe.py -q`

Expected: tests fail because the execution gate and probe runner are not implemented.

- [ ] **Step 3: Implement the minimal streaming probe**

Use the existing `openai.OpenAI` SDK with the configured OpenAI-compatible base URL, `timeout=180`, and `max_retries=0`. Make exactly one `chat.completions.create` request with a fixed non-paper prompt, `stream=True`, `stream_options={"include_usage": True}`, and `max_tokens=64`. Parse the compact JSON locally, record actual model and token fields, and expose only exception type/status—not raw provider bodies or credentials.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest backend/tests/test_p5r2_capability_probe.py -q`

Expected: all P5-R2 tests pass with fake clients and zero network access.

### Task 3: Document architecture, operator setup, and unverified capabilities

**Files:**
- Create: `docs/p5r2/model-capability-probe.md`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Document the effective model path and local configuration**

Describe root `.env.local` loading, the exact variables, Git-ignore behavior, restart requirement, frozen model requirement, the existing streaming/non-streaming split, token recording, retry/timeout behavior, and the absence of startup model requests.

- [ ] **Step 2: Document the authorization and cost boundary**

State that the check-only command is free, the execute path is not authorized in this change, pricing must come from the actual provider and be placed in the two rate variables, and one probe cannot validate P4 non-streaming behavior, long context, high-volume reliability, or scientific quality. Keep P5 FAIL and the ChatGPT comparison pending.

- [ ] **Step 3: Verify documentation does not expose secrets or frozen content changes**

Run: `rg -n "sk-[A-Za-z0-9]|Authorization: Bearer" docs/p5r2 README.md .env.example`

Expected: no match.

### Task 4: Verify the no-cost implementation and open the PR

**Files:**
- Modify only files listed above.

- [ ] **Step 1: Run the deterministic configuration check**

Run the script without `--execute` and save a public report. Confirm it reports the missing P5-R2 credential, matching frozen model, unknown pricing, zero calls, and no network attempt.

- [ ] **Step 2: Run regression tests and frontend build**

Run the focused P5-R2 tests, full backend tests, frontend production build, and existing browser regressions. Record the existing Pandoc-related export failures separately if the runtime remains unavailable.

- [ ] **Step 3: Review scope and secrets**

Run `git diff --check`, inspect `git diff --stat`, confirm no frozen Prompt/PDF/reference file changed, and scan tracked changes for key-like content.

- [ ] **Step 4: Commit, push, and create a PR**

Commit the isolated changes, push `feature/p5r2-model-capability-probe`, create a PR against `main`, and wait for required CI. Do not merge and do not execute the paid probe.
