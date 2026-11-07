# P2 Unknown-Usage Fail-Closed Design

## Goal

Stop the active P2-A, P2-B, P2-C, or P3 workflow immediately after a supplier request may have been dispatched but no reliable token usage was returned. Preserve completed partial results and a redacted audit trace without retrying, splitting, falling back, or resuming the failed request.

## Scope

This change only affects model-call failure handling shared by P2-A through P3. It does not change prompts, scientific validation, source schemas, frozen P5 assets, model selection, budgets, or successful response processing.

## Failure classification

- `ModelConfigurationError` occurs before supplier dispatch and remains a configuration failure. It does not increment unknown usage.
- A model-call exception after `RunBudget.call` starts, or a returned reply without valid non-negative integer input and output usage, becomes `UnknownUsageError`.
- JSON, Pydantic, evidence-reference, or other content validation failures after reliable usage was returned keep the existing single content-repair attempt.

## Control flow

`RunBudget.call` is the single fail-closed gate. It records the request against the call budget before dispatch. Unknown usage consumes conservative estimated input/output reservations, increments `unknown_usage_calls`, sets a latched stop reason, and raises `UnknownUsageError`. The latch prevents every later call even if a caller catches the first exception.

P2-A, P2-B, P2-C, and P3 catch `UnknownUsageError` only to persist one redacted trace (`status=unknown_usage`, token fields null, sanitized exception type), then re-raise. They must not enter content retry loops, recursive chunk splitting, deterministic scientific fallbacks, later dimensions/tasks, or global synthesis fallbacks.

## Partial results and restart

Already persisted ReviewPlan, PaperProfile, dimensions, batches, observations, or adjudications remain unchanged. The active run becomes failed or partial-failed through the existing application-level handlers. No startup path replays P2-A through P3 failed runs. ClaimEvidenceMap and other final outputs are stored only if their normal validation completed.

## Audit and privacy

Unknown token usage is never stored as zero. Error fields contain stable codes and exception class names only; they never include prompts, response bodies, endpoints, credentials, or raw supplier errors. The private Shaw failure artifacts remain outside the repository.

## Compatibility

Successful calls and valid-usage content repairs retain their current behavior. Existing result schemas remain readable. No database migration is required because current trace/status/error fields can represent the new state.
