# P5-R4 PR 2: P4 Whole-Report Model Budget

P4 model-backed draft generation now fails closed unless one immutable budget
snapshot can be built from nine explicit settings. This control covers P4
writing, consistency checking, content repair, and every report batch. It does
not claim to cap P2-A through P3, the provider account, or the provider's final
invoice.

## Required configuration

All settings are mandatory and have no production defaults:

| Variable | Contract |
| --- | --- |
| `P4_MAX_PROVIDER_REQUESTS` | Positive integer; includes writing, checks, and repairs |
| `P4_MAX_INPUT_TOKENS` | Positive cumulative provider-Usage ceiling |
| `P4_MAX_OUTPUT_TOKENS` | Positive cumulative provider-Usage ceiling |
| `P4_MAX_OUTPUT_TOKENS_PER_REQUEST` | Positive request `max_tokens`; no larger than the cumulative output ceiling |
| `P4_MAX_DURATION_SECONDS` | Positive finite whole-report duration budget |
| `P4_MAX_PLANNED_COST_USD` | Positive finite local planning ceiling |
| `P4_INPUT_USD_PER_MILLION` | Positive finite operator-supplied input price |
| `P4_OUTPUT_USD_PER_MILLION` | Positive finite operator-supplied output price |
| `P4_PRICING_STATUS` | `estimated_unverified` or `provider_verified` |

The original process environment and `.env.local` remain separate sources.
Different non-empty values for the same setting block generation before the
SDK client is created. Application startup does not copy these variables into
`os.environ`, so source conflicts cannot be hidden by import order. Zero prices
are rejected; a genuinely free provider plan requires a separately reviewed
verification contract rather than a zero-value bypass.

## Two-layer budget enforcement

Before a request, P4 constructs the exact messages and uses the documented
`utf8-bytes/3 + message-overhead` estimator. It checks the remaining request,
estimated input, reserved output, duration, and planned-cost budgets. This is a
planning estimate, not provider tokenization. The exact writing, generated
report, consistency context, and repair instruction are included before each
admission decision.

After a response, `prompt_tokens`, `completion_tokens`, and `total_tokens` are
validated as non-negative non-boolean integers. Contradictory totals, missing
Usage, request-output overruns, and report-budget overruns terminate the run.
Unknown Usage is never converted to zero and prevents subsequent requests.

Each request explicitly supplies the validated session model, `max_tokens`,
and a timeout no greater than the remaining local duration. SDK retries are
zero. Timeout and connection failures are not retried, parameters are not
silently changed, and no model or endpoint fallback exists. A local timeout is
not an absolute physical wall-clock guarantee because scheduling, transport
teardown, and persistence can finish later.

## Durable request lifecycle

Every potentially billable request has stable `generation_run_id` and
`model_call_id` values. Before the SDK call, one SQLite transaction inserts the
legacy-compatible call row plus its `p4_model_call_budget` sidecar and commits
the reservation. A second transaction commits `dispatch_pending`. If either
write fails, no SDK request is permitted.

`dispatch_pending` means transmission may have occurred. If the provider
raises, Usage is absent, or final settlement cannot be persisted, the
reservation is not refunded. Restart recovery converts uncertain dispatches
to `usage_unknown`, never replays them, and marks reserved-but-never-dispatched
calls separately as `interrupted_not_sent`.

Generation states include `configuration_failed`, `ready`,
`request_reserved`, `dispatch_pending`, `usage_valid`, `usage_unknown`,
`usage_invalid`, `budget_exhausted`, `configuration_changed`, `interrupted`,
and `completed`. Historical `p4_model_calls`, drafts, and older schemas remain
readable.

Only a writing chunk that passes its structure/evidence validation and its
matching consistency check is saved as validated partial output. Raw or
unchecked provider text is never presented as validated output. An interrupted
run never creates an approvable or exportable complete draft.

After report assembly and validation, P4 checks the same monotonic whole-run
duration once more immediately before the draft transaction. An expired run
keeps validated partials and call audits but creates no completed draft. This
check cannot eliminate the narrow race between that check and the SQLite
commit, and the local duration is not a provider-side wall-clock guarantee.

Authenticated read-only endpoints expose project run lists and run detail.
They return stable states, counts, known Usage totals, local cost fields, and a
validated-partial presence flag. They exclude credentials, endpoints, prompts,
raw responses, `.env.local` paths, and partial content.

## Cost terminology and limits

- `planned_cost_usd` is the local pre-request calculation from estimated input
  plus reserved output.
- `usage_calculated_cost_usd` is the local calculation from valid provider
  Usage.
- `provider_billed_cost_usd` remains nullable until separately reconciled with
  provider billing.
- `provider_account_limit_usd` remains nullable; this change creates no
  provider-side hard limit.

The current `$0.80/$4.00` figures may be supplied with
`estimated_unverified`; they are not embedded defaults and are not proof of
Yaoxing billing. Local admission cannot guarantee the provider will not charge
more.

All automated tests use fake clients or deterministic data. This work makes no
real model call and does not alter the P5-R3B historical FAIL, frozen prompts,
Manifest, scientific rules, or blind references.

## Deployment boundary

P4 restart recovery is supported only in a single application worker. Deploy
with `PAPER_REVIEW_WORKER_COUNT=1` and a matching server setting such as
`uvicorn backend.app:app --workers 1`. Startup rejects a declared worker count
other than one and rejects conflicts with `WEB_CONCURRENCY` before opening the
database or recovering requests. Process and `.env.local` worker declarations
are resolved separately; different non-empty values fail closed. Predeployment
mode also rejects an undeclared
worker count; ordinary local development retains the server's one-worker
default for compatibility. Supervisors that start multiple processes
without declaring either variable cannot be detected reliably; such a setup is
unsupported. A future multi-worker deployment requires an explicit ownership
or lease protocol before this restriction can be removed.
