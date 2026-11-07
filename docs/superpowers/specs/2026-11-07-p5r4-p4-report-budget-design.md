# P5-R4 PR 2: P4 Whole-Report Model Budget Design

Date: 2026-11-07

Status: proposed for independent review

Scope: P4 report writing, consistency checks, content-repair attempts, and their provider requests only.

## Objective

Add a fail-closed, auditable budget around one complete P4 draft-generation attempt. No P4 model request may start unless every required request, Token, duration, and local planned-cost limit is explicitly configured and internally consistent. The budget covers every report-writing batch, consistency check, content-repair attempt, and provider request made before the draft is complete.

This PR must not change P1-P3 outputs, P4 scientific admission rules, report Prompts, evidence validation, frozen P5 assets, or the historical P5-R3B result.

## Current call graph and risk

`POST /api/review-report-projects/{project_id}/drafts` builds a frozen `ReportPlan` and `ReportEvidencePack`, then calls `write_report()` when the plan contains model-generated groups.

`write_report()` currently behaves as follows:

- author reports send all groups in one batch;
- internal and editor reports send one group per batch;
- each batch allows two content attempts;
- each content attempt can perform one writing request and one consistency request;
- `p4_drafts_api.call_model()` independently allows a second provider request after a connection or timeout failure;
- the SDK session has `max_retries=0`, but requests have no explicit output-Token cap;
- Usage and elapsed time are recorded per call, but no whole-report controller stops later requests;
- missing Usage is stored as `NULL`, yet it does not currently prevent later provider requests;
- no P4-specific input, output, call, duration, or planned-cost configuration is required.

The theoretical current upper bound is therefore eight provider requests per report batch: two content attempts, two logical calls per attempt, and up to two provider attempts per logical call. Internal/editor reports multiply that bound by the number of groups. The actual count can be lower when validation fails before consistency checking.

## Approaches considered

### A. One P4 budget controller per draft-generation attempt — selected

Create an immutable configuration plus a mutable, request-aware ledger. `create_p4_draft()` resolves the complete budget before constructing a model session. The same controller is passed through `write_report()` and the injected model caller. It reserves budget before every provider request, validates actual Usage afterwards, and owns the terminal stop decision.

This keeps P4's existing model configuration/session boundary, gives one source of truth for all writing and consistency requests, and avoids changing P1-P3.

### B. Add counters independently in `p4_writer.py` and `p4_drafts_api.py` — rejected

This would split logical retries from provider retries. Neither layer could prove the whole-report request or cost ceiling, and audit records could disagree.

### C. Generalize the P2-A `RunBudget` for every production stage — deferred

The existing class does not validate provider Usage, price status, per-request output limits, or persistent P4 audit state. Generalizing it would broaden this PR into P1-P4 behavior changes.

## Explicit configuration contract

The following variables are all required when a P4 plan contains model-generated groups:

| Variable | Type and rule | Meaning |
| --- | --- | --- |
| `P4_MAX_PROVIDER_REQUESTS` | positive integer | Maximum actual `chat.completions.create` calls, including every content repair and retry |
| `P4_MAX_INPUT_TOKENS` | positive integer | Maximum cumulative provider-reported input Tokens |
| `P4_MAX_OUTPUT_TOKENS` | positive integer | Maximum cumulative provider-reported output Tokens |
| `P4_MAX_OUTPUT_TOKENS_PER_REQUEST` | positive integer, no greater than cumulative output limit | Request-level `max_tokens` value |
| `P4_MAX_DURATION_SECONDS` | positive decimal | Maximum monotonic wall time for the complete generation attempt |
| `P4_MAX_PLANNED_COST_USD` | positive decimal | Maximum local planned cost; not an account-side hard limit |
| `P4_INPUT_USD_PER_MILLION` | non-negative decimal | Operator-supplied input price used only for local calculations |
| `P4_OUTPUT_USD_PER_MILLION` | non-negative decimal | Operator-supplied output price used only for local calculations |
| `P4_PRICING_STATUS` | `estimated_unverified` or `provider_verified` | Provenance label for the operator-supplied prices |

There are no production defaults and no fallback to the P5-R3B probe's `OPENAI_*_USD_PER_MILLION` variables. This prevents a probe estimate from silently becoming a production report budget.

The P4 variables use the same source isolation rule as the model runtime configuration: process environment and `.env.local` remain distinguishable; different non-empty values fail closed; identical values are accepted; values are never included in errors. Application startup must not copy these variables from the file into `os.environ`.

`P4_PRICING_STATUS=provider_verified` remains an operator assertion recorded for audit. It is not independent proof of the provider bill. `estimated_unverified` is allowed for controlled engineering runs but cannot by itself satisfy a formal P5 price-verification or release gate.

Invalid, incomplete, unreadable, conflicting, non-finite, negative, or internally inconsistent configuration returns a stable `p4_budget_configuration_invalid` error before model-session construction and before the first provider request. P4 local read, edit, approval, and export operations remain available without these variables.

## Components

### `P4BudgetConfiguration`

A frozen value object contains the nine validated settings above. Decimal values are parsed with `Decimal`, never binary floating point. Its representation excludes raw source values where they could reveal operational configuration.

### `P4BudgetController`

One controller exists for one draft-generation attempt. It tracks:

- configured ceilings and their pricing status;
- provider request count;
- known cumulative input and output Tokens;
- unknown-Usage request count;
- monotonic elapsed time;
- request-time input estimates and output reservations;
- local planned cost and local Usage-derived cost;
- terminal status and stable stop reason.

The controller is not shared between reports and is not reused after a terminal state.

### Request integration

The P4 model caller is the only component allowed to call the PR 1 request-time model validator. Immediately before that call it must also obtain a reservation from the P4 controller. The request uses:

- the validated session model;
- `max_tokens=P4_MAX_OUTPUT_TOKENS_PER_REQUEST`;
- a request timeout no greater than the remaining whole-report duration;
- SDK `max_retries=0` so one controller reservation corresponds to one provider request.

The implementation must not retry with `max_completion_tokens`, a different output limit, another model, or another endpoint if the provider rejects these parameters.

## Whole-report planning and reservation

Before the first request, P4 computes the number of report batches using the existing rule. A complete retry-capable run requires at most four logical calls per batch: writing plus consistency for each of two content attempts. Because SDK automatic retries remain zero, this is also the planned maximum provider-request count after transport-level automatic retries are removed.

The configured provider-request ceiling must be at least the minimum successful path of two requests per batch. A smaller value blocks before the first request. A ceiling below the full four-per-batch retry path is permitted only if the controller deterministically stops before an unaffordable repair attempt; it must never silently exceed the configured ceiling.

Before every request, the controller checks:

1. the run is not already terminal;
2. another provider request is available;
3. elapsed time and the bounded request timeout fit the remaining duration;
4. the current prompt's deterministic input estimate fits the remaining input budget;
5. one full per-request output reservation fits the remaining cumulative output budget;
6. estimated input cost plus the full reserved output cost fits the remaining planned-cost budget;
7. when entering a content attempt, the remaining budget can cover every mandatory call needed to finish that attempt.

Input estimation uses a documented deterministic UTF-8 byte-based calculation plus fixed message overhead. It is a planning estimate, not a provider Token guarantee. Consistency-call planning includes the full output reservation from the preceding writing call because that output becomes part of its input. Repair-prompt text is included. The estimate and method version are stored in the audit record.

The controller never describes the local planned-cost ceiling as a provider account limit. A provider can tokenize differently, return Usage above the estimate, or bill additional categories.

## Usage validation and failure rules

After each provider response, P4 validates `prompt_tokens`, `completion_tokens`, and, when supplied, `total_tokens`:

- values must be integers but not booleans;
- values must be non-negative;
- `total_tokens` must equal input plus output when present;
- actual output must not exceed the request-level output setting;
- cumulative actual input/output must not exceed their report ceilings.

Valid Usage replaces that request's planning reservation in the known totals. Local Usage-derived cost is calculated with the immutable price snapshot and `Decimal`.

If Usage is missing, malformed, contradictory, or exceeds a configured ceiling, the current response and any previously validated report chunks remain in the private generation record, but no further model request is allowed and no completed draft is created. The run becomes terminal with `usage_unknown`, `usage_invalid`, or `budget_exceeded_after_response`. Unknown Tokens are never converted to zero and local actual cost remains incomplete.

A connection error or timeout normally provides no auditable Usage. P4 therefore records the failed request and stops without a transport retry. Content/schema/evidence validation may still start the existing second content attempt only when the preceding provider request returned valid Usage and the controller grants a new reservation. This preserves controlled repair behavior without risking an uncounted duplicate charge.

## Cost terminology

The API and audit records keep four concepts separate:

1. `planned_cost_usd`: local pre-request estimate using operator-supplied prices and conservative reservations;
2. `usage_calculated_cost_usd`: local calculation from valid provider-reported Usage;
3. `provider_billed_cost_usd`: nullable external billing value; this PR never invents it;
4. `provider_account_limit_usd`: nullable external account control; this PR does not claim to create one.

The current `$0.80` input and `$4.00` output values may be configured with `estimated_unverified`, but are not embedded as code defaults and are not described as verified Yaoxing prices.

## Persistent audit design

Keep the existing `p4_model_calls` table and response fields backward compatible. Add sidecar records instead of rewriting historical calls.

### `p4_generation_runs`

One row per attempted model-backed draft stores:

- run, draft, project, kind, and language identifiers;
- sanitized configuration snapshot: all limits, prices, pricing status, and estimator version, but no key or endpoint;
- started/completed timestamps and monotonic duration;
- request count, known Token totals, unknown-Usage count;
- planned and Usage-derived local costs;
- nullable provider-billed cost and account-limit fields;
- terminal status and stable stop reason;
- validated partial `ReportWritingResult` chunks when generation stops after useful work.

Partial content remains private, is never marked approved, and cannot be exported as a formal report.

### `p4_model_call_budget`

A one-to-one sidecar keyed by the existing model-call ID stores request ordinal, generation-run ID, input estimate, output reservation, request output limit, remaining duration at dispatch, Usage status, calculated local cost, pricing status, and post-request budget state.

No table stores API keys, endpoint values, full prompts, or raw provider exceptions. Existing `p4_model_calls.error` continues to store exception class or stable budget code only.

Schema initialization must be idempotent for existing SQLite databases. Historical P4 drafts and calls remain readable with no fabricated budget values.

## API behavior

- Missing or invalid budget configuration: HTTP 503 with `p4_budget_configuration_invalid`, stable blocker codes, and variable names only.
- Budget unavailable before a later request: HTTP 409 with `p4_budget_exhausted` and a stable reason.
- Missing or invalid Usage after a response: HTTP 502 with `p4_usage_unverifiable`; no retry.
- Provider or request-parameter failure: existing safe 502 behavior, with the generation run marked failed.
- Completed generation: existing 201 draft response plus backward-compatible budget summary fields.

The budget summary may expose ceilings, counts, statuses, and local cost categories. It must not expose environment-file paths, endpoint hosts, credentials, raw prompts, or raw exceptions.

## Test strategy

All tests use fake clients and temporary configuration files. No test may access a model endpoint.

### Configuration tests

- every required variable missing independently blocks before client construction;
- malformed, zero, negative, non-finite, and conflicting values fail safely;
- process/file source conflicts expose names only;
- per-request output greater than cumulative output is rejected;
- estimated prices retain `estimated_unverified` in every record;
- pure P4 read/edit/approve/export and empty local drafts remain available without budget configuration.

### Request and retry tests

- a normal writing plus consistency path uses two provider requests;
- internal/editor multi-batch reports share one whole-report budget;
- valid content repair consumes a new request and remains within the same run;
- every provider call receives the explicit output limit and bounded timeout;
- SDK automatic retries remain zero;
- connection/timeout with unknown Usage records one request and never retries;
- call exhaustion before a writing, consistency, or repair request blocks without incrementing SDK calls;
- a configuration change detected by PR 1 remains terminal and is not converted into a budget retry.

### Token, duration, and cost tests

- cumulative input and output are updated from valid Usage;
- missing, negative, boolean, non-integer, contradictory, and over-limit Usage stops the run;
- unknown Usage is not zero and makes Usage-derived cost incomplete;
- pre-request input estimate and full output reservation can block a request;
- elapsed duration and per-request timeout cannot exceed the whole-report limit;
- local planned cost, Usage-derived cost, provider billed cost, and account limit remain distinct;
- Decimal rounding is deterministic and never permits a request above the configured local planned cost.

### Persistence and compatibility tests

- every attempted provider request has both the existing call row and budget sidecar;
- interruption retains prior completed calls and validated partial chunks;
- restart/readback preserves terminal status without resuming automatically;
- old databases and P4 Schema 1.0/1.1 drafts remain readable;
- P1-P3, P4 evidence admission, P5 deterministic acceptance, frontend build, and browser edit/export regressions remain unchanged.

## Rollout and operational guidance

The PR should ship fail-closed. Deployments must set and review all P4 variables before re-enabling model-backed P4 generation. A zero-network preflight should report configuration readiness and the calculated worst-case request/output/cost reservation for the actual report plan.

No real paper run is authorized by configuring these values. Formal P5 execution still requires independent provider-price verification, an approved per-paper budget, model capability evidence, and explicit real-call authorization.

## Out of scope

- changing report Prompts, grouping, admission, evidence rules, or scientific wording;
- changing P1-P3 budgets or model behavior;
- provider billing reconciliation UI or account-side spend controls;
- proving Yaoxing's underlying model identity, price, routing, or data handling;
- retrying with alternative request parameters or models;
- any real model call or paper review.

## Acceptance criteria

- no model-backed P4 draft starts with incomplete or conflicting budget configuration;
- every actual provider request consumes one auditable reservation before dispatch;
- the complete draft-generation attempt cannot exceed configured call, Token, output-per-request, time, or local planned-cost controls without becoming terminal;
- missing or invalid Usage prevents all later requests and is never counted as zero;
- existing validated partial output and redacted audit evidence survive interruption;
- no local estimate is represented as provider billing or an account hard limit;
- P1-P4 scientific behavior, frozen P5 assets, and the historical P5-R3B FAIL remain unchanged;
- all deterministic regressions pass with zero real model calls.
