# P5-R4 P4 Whole-Report Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed, durable request, Token, duration, and local planned-cost controls around every model-backed P4 draft-generation attempt.

**Architecture:** A new `backend/p4_budget.py` owns explicit configuration parsing, immutable snapshots, request admission, Usage validation, and SQLite state transitions. `p4_drafts_api.py` remains the orchestration boundary: it constructs exact requests, persists a reservation before dispatch, invokes the PR 1 model session once, settles Usage before parsing content, and saves only consistency-validated partial chunks. Existing P4 report logic and historical tables remain compatible through sidecar audit tables.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic, OpenAI-compatible Chat Completions, pytest, Decimal, React/Vite, Playwright.

---

### Task 1: Freeze the budget configuration contract

**Files:**
- Create: `backend/p4_budget.py`
- Create: `backend/tests/test_p4_budget.py`
- Modify: `backend/app.py:45-61`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration tests**

Add parametrized tests for all nine required keys, positive integers/Decimals, non-finite numbers, per-request output exceeding cumulative output, zero prices, process/file conflicts, unreadable files, and redacted errors. The desired API is:

```python
from backend.p4_budget import (
    P4_BUDGET_ENV_KEYS,
    P4BudgetConfigurationError,
    require_p4_budget_configuration,
)

with pytest.raises(P4BudgetConfigurationError) as caught:
    require_p4_budget_configuration(path, process_environment={})
assert caught.value.safe_detail()["code"] == "p4_budget_configuration_invalid"
assert set(caught.value.safe_detail()["variables"]).issubset(P4_BUDGET_ENV_KEYS)
```

Also prove `load_local_environment()` does not copy any P4 budget variable into `os.environ`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py backend/tests/test_local_environment.py
```

Expected: collection/import failure because `backend.p4_budget` does not exist.

- [ ] **Step 3: Implement the minimal immutable resolver**

Create:

```python
P4_BUDGET_ENV_KEYS = (
    "P4_MAX_PROVIDER_REQUESTS", "P4_MAX_INPUT_TOKENS",
    "P4_MAX_OUTPUT_TOKENS", "P4_MAX_OUTPUT_TOKENS_PER_REQUEST",
    "P4_MAX_DURATION_SECONDS", "P4_MAX_PLANNED_COST_USD",
    "P4_INPUT_USD_PER_MILLION", "P4_OUTPUT_USD_PER_MILLION",
    "P4_PRICING_STATUS",
)

@dataclass(frozen=True)
class P4BudgetConfiguration:
    max_provider_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_output_tokens_per_request: int
    max_duration_seconds: Decimal
    max_planned_cost_usd: Decimal
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    pricing_status: Literal["estimated_unverified", "provider_verified"]
```

Parse Decimals with `Decimal`, reject booleans/non-finite/zero/negative values, reject different non-empty dual-source values, and expose only stable blocker codes plus variable names. Extend `load_local_environment()` exclusion to `REQUIRED_MODEL_ENV_KEYS + P4_BUDGET_ENV_KEYS`. Do not read or log real values.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass with no network access.

- [ ] **Step 5: Commit**

```bash
git add backend/p4_budget.py backend/tests/test_p4_budget.py backend/tests/test_local_environment.py backend/app.py .env.example
git commit -m "feat: validate explicit P4 report budgets"
```

### Task 2: Add durable audit tables and the state machine

**Files:**
- Modify: `backend/p4_api.py:23-68`
- Modify: `backend/p4_budget.py`
- Modify: `backend/tests/test_p4_budget.py`
- Modify: `backend/tests/test_p4_api.py`

- [ ] **Step 1: Write failing migration and state-transition tests**

Tests must create an old P4 database, call `initialize_p4_tables()`, and assert the old rows remain readable plus these new tables exist:

```sql
p4_generation_runs
p4_model_call_budget
```

Exercise these store methods with a temporary SQLite connection:

```python
store.create_run(...)
store.reserve_request(...)
store.mark_dispatch_pending(...)
store.settle_valid_usage(...)
store.fail_usage_unknown(...)
store.save_validated_partial(...)
```

Assert duplicate `model_call_id` insertion fails and all run/call transitions use stable IDs.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py backend/tests/test_p4_api.py
```

Expected: missing tables/store methods.

- [ ] **Step 3: Implement idempotent tables and `P4BudgetStore`**

Add `p4_generation_runs` with sanitized configuration JSON, run totals, cost fields, validated partial JSON, state/reason, and timestamps. Add `p4_model_call_budget` keyed by the existing model-call ID with request ordinal, estimates/reservations, dispatch state, Usage state, and local cost.

`reserve_request()` must use one transaction to insert the legacy `p4_model_calls` reservation row, insert its sidecar, update run totals/state, and commit before returning. `mark_dispatch_pending()` must commit in a separate transaction before SDK invocation. Every update uses `WHERE` clauses for the expected prior state and raises a stable state-conflict error if exactly one row is not changed.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/p4_api.py backend/p4_budget.py backend/tests/test_p4_budget.py backend/tests/test_p4_api.py
git commit -m "feat: persist P4 request reservations"
```

### Task 3: Implement request admission and Usage settlement

**Files:**
- Modify: `backend/p4_budget.py`
- Modify: `backend/tests/test_p4_budget.py`

- [ ] **Step 1: Write failing controller tests**

Cover exact-message estimation, cumulative request/input/output exhaustion, per-request output reservations, planned cost, monotonic duration, immutable configuration snapshots, and valid Usage replacement. Test Usage values that are missing, negative, boolean, non-integer, contradictory, or above request/report limits.

The desired controller interface is:

```python
reservation = controller.plan_request(
    model_call_id="call-1",
    stage="writing-1-attempt-1",
    messages=messages,
)
assert reservation.max_tokens == config.max_output_tokens_per_request
settlement = controller.validate_usage(
    reservation,
    SimpleNamespace(prompt_tokens=120, completion_tokens=40, total_tokens=160),
)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py -k 'controller or usage or cost or duration'
```

Expected: controller methods missing.

- [ ] **Step 3: Implement the controller**

Use a UTF-8 byte estimator over the exact role/content pairs plus documented fixed message overhead. Use `Decimal` for both reservation and Usage-derived cost:

```python
cost = (
    Decimal(input_tokens) * config.input_usd_per_million
    + Decimal(output_tokens) * config.output_usd_per_million
) / Decimal(1_000_000)
```

`plan_request()` returns an immutable reservation without changing durable totals. `apply_reserved()` and `apply_settlement()` update the in-memory snapshot only after the matching store transaction succeeds. Unknown/invalid Usage makes the controller terminal and never refunds the request/output/cost reservation.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/p4_budget.py backend/tests/test_p4_budget.py
git commit -m "feat: enforce P4 request and token budgets"
```

### Task 4: Route every P4 model request through the durable budget gate

**Files:**
- Modify: `backend/model_runtime.py:202-229`
- Modify: `backend/p4_api.py:119-420`
- Modify: `backend/p4_drafts_api.py:38-180`
- Modify: `backend/p4_writer.py:53-130`
- Modify: `backend/app.py:160-190, 1808-1815`
- Modify: `backend/tests/test_p4_drafts_api.py`
- Modify: `backend/tests/test_model_entrypoint_safety.py`

- [ ] **Step 1: Write failing request-lifecycle tests**

Use fake clients to assert:

- normal writing plus consistency makes exactly two SDK calls;
- `max_tokens` and remaining timeout are explicit on every request;
- multi-batch internal reports share one controller;
- content repair consumes additional budget only after valid Usage;
- connection/timeout errors make one call and never retry;
- configuration drift between requests stops before the next SDK call;
- no P4 production source contains a direct `chat.completions.create` outside the PR 1 gate.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_drafts_api.py backend/tests/test_model_entrypoint_safety.py
```

Expected: missing budget configuration/preflight and existing retry/output-limit behavior violates assertions.

- [ ] **Step 3: Integrate the gate with the smallest call-site change**

Add a PR 1 helper that validates a `ModelRuntimeSession` without invoking the SDK; keep `create_validated_chat_completion()` as the sole SDK caller. In P4:

1. allocate generation/draft IDs;
2. persist the run and immutable budget snapshot;
3. create one validated model session;
4. build exact messages;
5. prevalidate current session configuration;
6. plan and persist the request reservation;
7. persist `dispatch_pending`;
8. invoke once with `model`, `messages`, `max_tokens`, and bounded `timeout`;
9. validate and persist Usage before parsing content;
10. return content to existing report validation.

Remove the transport retry loop. Preserve the existing two-attempt content repair in `write_report()`, but propagate all budget, Usage, and model-configuration errors without content retry.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command with the bundled Pandoc path when export tests are selected. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/model_runtime.py backend/p4_api.py backend/p4_drafts_api.py backend/p4_writer.py backend/app.py backend/tests/test_p4_drafts_api.py backend/tests/test_model_entrypoint_safety.py
git commit -m "feat: gate all P4 model requests by budget"
```

### Task 5: Preserve only consistency-validated partial output

**Files:**
- Modify: `backend/p4_writer.py`
- Modify: `backend/p4_drafts_api.py`
- Modify: `backend/p4_budget.py`
- Modify: `backend/tests/test_p4_writer.py`
- Modify: `backend/tests/test_p4_drafts_api.py`

- [ ] **Step 1: Write failing partial-result tests**

Test that a structurally valid writing response followed by failed consistency produces no validated partial. Test that the first batch passing writing and consistency is retained when the second batch is interrupted. Assert no `p4_drafts` row exists and partial output cannot be approved/exported.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_writer.py backend/tests/test_p4_drafts_api.py -k partial
```

Expected: no partial callback or persisted partial result.

- [ ] **Step 3: Add the validated-chunk callback**

Extend `write_report(..., on_validated_chunk=None)` and invoke the callback only after the consistency response is parsed and accepted. Persist the validated `ReportWritingResult` chunk through `P4BudgetStore.save_validated_partial()`. Do not store raw provider text or unvalidated writing output.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/p4_writer.py backend/p4_drafts_api.py backend/p4_budget.py backend/tests/test_p4_writer.py backend/tests/test_p4_drafts_api.py
git commit -m "feat: retain validated P4 partial output"
```

### Task 6: Recover interrupted runs and expose redacted read-only status

**Files:**
- Modify: `backend/p4_budget.py`
- Modify: `backend/p4_api.py`
- Modify: `backend/app.py:340-375`
- Modify: `backend/tests/test_p4_budget.py`
- Modify: `backend/tests/test_p4_api.py`
- Modify: `backend/tests/test_p4_drafts_api.py`

- [ ] **Step 1: Write failing recovery and API tests**

Create database states for `reserved_not_dispatched` and `dispatch_pending`, restart `create_app()`, and assert:

- reserved-only calls become `interrupted_not_sent` and do not increment potentially billable requests;
- dispatch-pending calls become `usage_unknown` and increment unknown Usage;
- neither run resumes or invokes a fake SDK;
- list/detail endpoints return only counts, safe statuses, costs, and `has_validated_partial_output`;
- responses exclude keys, endpoint values, environment paths, raw prompts/responses, and unvalidated content.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py backend/tests/test_p4_api.py backend/tests/test_p4_drafts_api.py -k 'recover or generation_run or interrupted'
```

Expected: recovery and endpoints absent.

- [ ] **Step 3: Implement deterministic recovery and query routes**

Call `recover_p4_generation_runs(db, now())` after table initialization. Add authenticated project-list and run-detail GET routes. Build responses from an allowlist of safe columns and include `generation_run_id` in safe runtime error details.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass and SDK call count remains zero.

- [ ] **Step 5: Commit**

```bash
git add backend/p4_budget.py backend/p4_api.py backend/app.py backend/tests/test_p4_budget.py backend/tests/test_p4_api.py backend/tests/test_p4_drafts_api.py
git commit -m "feat: recover and inspect P4 budget runs"
```

### Task 7: Fault-injection closure and documentation

**Files:**
- Modify: `backend/tests/test_p4_budget.py`
- Modify: `backend/tests/test_p4_drafts_api.py`
- Modify: `docs/p5r4-model-endpoint-safety.md`
- Create: `docs/p5r4-p4-report-budget.md`

- [ ] **Step 1: Add failing persistence fault tests**

Inject failures at reservation insert, dispatch transition, Usage settlement, and validated-partial persistence. Assert zero SDK calls before dispatch persistence, unknown Usage after response-settlement failure, stable IDs without duplicate rows, and conservative restart recovery.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py backend/tests/test_p4_drafts_api.py -k 'persistence or settlement or duplicate'
```

Expected: one or more new fault-injection assertions fail before implementation adjustments.

- [ ] **Step 3: Make fault behavior deterministic and document operations**

Keep transaction boundaries explicit. Document all nine variables, state transitions, zero-price rule, Usage/cost terminology, no-real-call test policy, and the limits of Token estimates, SDK timeouts, and local cost ceilings.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_p4_budget.py backend/tests/test_p4_drafts_api.py docs/p5r4-model-endpoint-safety.md docs/p5r4-p4-report-budget.md
git commit -m "test: close P4 budget interruption gaps"
```

### Task 8: Full deterministic verification and PR delivery

**Files:**
- Inspect: all branch changes
- Modify only if a verified regression requires a scoped correction

- [ ] **Step 1: Run focused and full backend tests**

```bash
PYTHONPATH=. .venv/bin/pytest -q backend/tests/test_p4_budget.py backend/tests/test_p4_writer.py backend/tests/test_p4_api.py backend/tests/test_p4_drafts_api.py backend/tests/test_model_entrypoint_safety.py backend/tests/test_p5_acceptance.py
PYTHONPATH=. .venv/bin/pytest -q backend/tests
```

Expected: all tests pass; only established optional skips remain.

- [ ] **Step 2: Build and run browser regressions**

```bash
cd frontend && npm run build
npx playwright test tests/real-pdf.spec.ts tests/cross-formula.spec.ts tests/review-modes.spec.ts tests/chat-failure.spec.ts --workers=1
npx playwright test tests/p4-report.spec.ts --workers=1
```

Expected: production build succeeds, legacy browser suite passes 6/6, and P4 report suite passes 1/1.

- [ ] **Step 3: Audit scope and sensitive data**

```bash
git diff --check main...HEAD
git diff --name-only main...HEAD
rg -n 'sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9._-]{16,}' $(git diff --name-only main...HEAD)
```

Expected: no Prompt, Manifest, PDF, blind-reference, credential, or real model artifact changes.

- [ ] **Step 4: Push and create the independent PR**

Push `feature/p5r4-p4-report-budget`, create a PR to `main`, and wait for P2-A, P2-B, P2-C, P3, P4, and P5 deterministic CI. `fresh-model-run` must remain skipped.

- [ ] **Step 5: Stop for independent review**

Report HEAD, Tree SHA, changed files, configuration contract, state machine, request/audit matrix, local and GitHub test results, compatibility risks, and merge recommendation. Do not merge or execute any real model request.
