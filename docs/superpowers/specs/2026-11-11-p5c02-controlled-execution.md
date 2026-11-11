# P5-C0.2: isolated one-paper, one-draft stage control

Status: implementation design. This document does not grant paid execution.

## Observed baseline

The existing P2-A, P2-B, P2-C and P3 APIs create asynchronous stage runs. They have in-process `RunBudget` limits and persist model-call traces after responses. A lost HTTP response can leave the caller uncertain whether a paid run was created. P4 has request-before-SDK persistent reservation and Usage settlement, but its draft creation API is not idempotent. The old P5 runner automatically proceeds through all stages and attempts four drafts. It is excluded from C1.

## C1 mode

Start a dedicated single-worker backend with a new, explicit private data and output directory and `P5C_CONTROLLED_MODE=1`. In this mode, P2-A through P4 paid-entry HTTP routes require a matching, persistent C1 authorization. Legacy chat and review-case model paths are blocked at session construction before SDK dispatch. P1 PDF upload and read-only routes remain offline and usable. The normal app mode and full four-draft P5 runner do not change.

The trial row binds one holdout paper to its accepted PDF SHA, the P5-C manifest byte SHA, an externally reviewed execution-lock file SHA, the actual code Commit/Tree, and the model configuration fingerprint. It never contains blind scientific question text or credential values. The final execution lock is produced only after the C0.2 final code is independently accepted and merged.

Each paid stage has a distinct authorization row. It binds stage, paper, source document, upstream stage Run ID, immutable model configuration identity, explicit request/input/output/time/retry/planned-cost budgets, price status and approval acknowledgement. The row receives a stable C1 Run ID before dispatch. A database uniqueness constraint and `BEGIN IMMEDIATE` claim make duplicate and concurrent HTTP requests return that same ID without another provider request. An HTTP response lost after dispatch leaves an unknown state; replays return the existing ID and never create a second stage. A new attempt after failure requires fresh human authorization, not an automatic restart.

P2-A through P3 requests use a model-session guard that persists each supplier-request reservation before SDK dispatch and settles validated Usage after the stream ends. A possibly dispatched request without complete Usage stays unknown and latches the stage; later requests fail closed. Input reservations are conservative estimates, not guaranteed supplier input counts. Output `max_tokens` is explicit in C1 mode. SDK retry remains zero and no parameter fallback is allowed. P4 reuses its existing provider-call reservation and settlement tables, with C1 stage authorization and idempotency layered around the draft endpoint. The P4 plan's actual internal group count is checked against the authorized successful-path request budget before dispatch.

The stage response or read-only audit shows stage status, C1 and underlying Run IDs, known Usage, unknown Usage count, planned cost, locally computed Usage cost, and provider-billed cost as unknown unless independently supplied. It never labels local limits as supplier account hard limits. P3 completion cannot automatically create or authorize P4. C1 only permits `internal:zh`; full-P5 four-draft rules are independent.

## Explicit non-goals

No real model invocation or holdout run in this PR. No Prompt, scientific rule, frozen Manifest, reference, historical database, or P1-P4 scientific algorithm change. No automatic next stage, whole-paper retry, blind-reference scoring, or final execution lock.

## Compatibility and failure behavior

Without C1 mode, legacy APIs behave as before. In C1 mode, missing/conflicting configuration, private-dir violation, absent/changed reviewed lock, incorrect upstream Run ID, stale model configuration, missing budget, excess budget, unknown Usage, duplicate in-flight claim, or runtime interruption blocks further paid requests. Database reservation failure blocks SDK dispatch. An in-flight request interrupted after dispatch is treated as possibly billable and never automatically replayed.
