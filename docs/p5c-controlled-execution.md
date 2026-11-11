# P5-C0.2 controlled one-paper trial

This is a code path for a future, separately approved P5-C1 trial. This PR does
not create the final execution lock, grant a paid authorization, or run a model.
The historical P5 four-draft runner and its acceptance rules are unchanged.

## Isolation and binding

Run the backend from the independently reviewed, clean execution Commit. Set
`P5C_CONTROLLED_MODE=1` and `PAPER_REVIEW_WORKER_COUNT=1` in the process. Use a
fresh private `PAPER_REVIEW_DATA_DIR` and a distinct private `P5C_OUTPUT_DIR`;
both directories must have mode `0700`. The C1 server rejects a pre-existing
`review.db` without its C1 ledger marker. Do not reuse a historical database.
Backend startup holds an exclusive private worker lock before recovering
interrupted requests. A second backend process on the same trial directory
fails to start; do not use reload mode or multiple workers for C1.

The following process settings identify the frozen execution assets:

- `P5C_PAPER_ID`: exactly one of the two frozen holdout IDs.
- `P5C_H1_PDF`, `P5C_H2_PDF`: local frozen PDF paths for the existing P5-C
  preflight. Only the selected paper may be used for a paid stage.
- `P5C_MANIFEST`: path to the unchanged P5-C v2.0 Manifest.
- `P5C_EXECUTION_LOCK`, `P5C_EXPECTED_LOCK_SHA256`: separately reviewed lock
  file and its actual byte SHA-256. Create the final lock only after C0.2 is
  merged and the execution Commit is independently accepted.

The existing production model parser still requires an explicit API Key,
HTTPS Base URL and `gpt-6.1-sol`, and fails on process/file conflicts. Never
place credentials or blind reference questions in C1 budget or authorization
files. Every paid-stage claim and every P2-A–P3 provider request rechecks the
execution identity. The lock binds Commit, Tree, Manifest and frozen assets;
the per-stage row binds one paper, upstream ID, model configuration fingerprint
and one immutable budget. For P4 it records both the offline project ID and
the originating P3 Run ID. An identity mismatch stops before SDK dispatch.

## Stage authorization

Each stage needs a separate, explicit local command. `--budget-json` is a
private file containing a `C1Budget` object with all fields explicit:
`max_calls`, `max_input_tokens`, `max_output_tokens`,
`max_output_tokens_per_request`, `max_duration_ms`,
`max_transport_retries` (must be zero),
`content_repair_policy` (must be `valid_usage_within_stage_budget`),
`max_planned_cost_usd`, `input_usd_per_million`,
`output_usd_per_million`, and `pricing_status`. Positive prices may be marked
`estimated_unverified`; that label is not supplier verification.
`provider_verified` requires a nonempty SHA-256 of separately retained supplier
pricing evidence; the hash alone does not prove the invoice or account limit.
Network errors
never receive automatic retry. Existing content repair may be attempted only
after valid Usage and while the same stage budget admits another request.

```bash
python -m scripts.p5c.authorize_stage \
  --root /REVIEWED/CHECKOUT --data-dir /PRIVATE/C1/data \
  --stage p2a --upstream-run-id DOCUMENT_ID \
  --budget-json /PRIVATE/C1/p2a-budget.json \
  --acknowledge P5C_STAGE_AUTHORIZED \
  --output /PRIVATE/C1/p2a-authorization.json
```

The command is offline and prints only the stable Run ID. The output file has
mode `0600` and contains the bearer authorization token. Submit it as the
`X-P5C-Stage-Authorization` header to the existing stage API. Do not paste the
token into a shared log or URL. C1 supports, in order, P1 document upload
(offline), P2-A, P2-B, P2-C, P3, creation of the P4 project (offline), and one
`internal:zh` draft. Each paid stage must complete and be separately authorized
before the next. The existing runner that generates four drafts is excluded.

The P4 authorization budget must exactly match the existing nine explicit P4
environment settings. At draft creation, the actual selected internal group
count is checked by `P4BudgetController.ensure_minimum_success_path()` before
the first provider request. C1 rejects `author`, `editor` and English drafts.

## Idempotency and accounting

SQLite `BEGIN IMMEDIATE`, a token-hash unique constraint and a unique active
stage index allow only one claim to create a stage Run ID. Repeated HTTP POSTs
with the same authorization return that ID and never schedule another worker.
An interrupted or failed run is not restarted by a repeated POST. A new attempt
requires a separate human authorization; completed upstream results are kept.
Only backend startup reconciles interrupted dispatches. The offline grant
command does not run recovery while a backend request may still be active.

For P2-A–P3 the C1 SDK wrapper persists a request reservation before calling
the SDK. It sends `max_tokens`, enables streaming Usage, and uses zero SDK
retries. Valid nonnegative integral Usage with a consistent total and exact
returned model is settled. A possibly dispatched request with missing or
invalid Usage is marked unknown and latches the stage; restart recovery never
replays it. The P4 writer retains its existing request-before-SDK ledger and
Usage settlement, with the C1 stage gate added around draft creation.

`GET /api/p5c/stage-runs/{run_id}` returns a redacted stage audit. Input
planning uses UTF-8 bytes; this is an estimate, not an actual supplier token
guarantee. The known Usage cost uses operator-entered prices, while
`provider_billed_cost_usd` remains unknown until separately checked against the
supplier account. The local request, token, time and planned-cost limits are
software controls, not an account-side hard spending cap. SDK timeout is not a
physical wall-clock guarantee.

No C1 request path loads the blind reference scientific questions into a model
prompt. The frozen reference files are used only by the preflight to verify
their identity and byte hashes; scoring occurs after a result is sealed.
