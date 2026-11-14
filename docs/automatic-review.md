# Single-paper automatic review

The **完整AI审稿** tab uses the existing PDF uploader and scientific stage engines.
It creates a pending task, freezes its PDF/model configuration and budgets, and
requires an explicit budget confirmation before starting P2-A → P2-B → P2-C → P3
→ a single Chinese internal P4 draft. This is independent of the frozen C1
assessment controller. Legacy review-case and standalone stage APIs remain available.

## API

- `POST /api/auto-review-tasks`: document_id, optional target_journal,
  special_requirements (P2-A only), review_requirements (P2-B only), total_budget,
  and p2a_budget/p2b_budget/p2c_budget/p3_budget/p4_budget.
- `POST /api/auto-review-tasks/{id}/start`: `{"confirm_budget":true}`.
  Retrying the same task returns its original run/status without dispatching again.
- `GET /api/auto-review-tasks/{id}`: stage/run identities, source hashes, frozen
  budgets, calls, known Usage, estimated cost, unknown Usage and report link.
- `GET /api/documents/{id}/auto-review-tasks`: persisted task list for refresh.

The existing authentication middleware guards all these routes. Uploading,
creating, refreshing and querying do not send model requests. The frontend passes
professional review requirements separately; it never inherits P2-A execution
instructions into P2-B.

Each budget specifies max_calls, max_input_tokens, max_output_tokens,
max_output_tokens_per_request, max_duration_seconds, max_cost_usd,
input_usd_per_million and output_usd_per_million. Prices must be positive and
identical across total and stage budgets. They remain **estimated_unverified**;
provider billing and provider account hard limits are unknown. UI defaults are
editable software limits, not a quotation or a promise to finish every PDF.

## Persistence and failure closure

The task and its total/stage budgets are created together in SQLite. Stage creation
and task/run linkage also commit together. Each subsequent stage reuses the existing
source/Schema validators and checks the prior artifact hash. A changed PDF, changed
configuration, invalid upstream product or exhausted budget stops the task.

Every automatic SDK request reserves a conservative UTF-8 byte input bound and its
maximum output in the task ledger before dispatch, marks dispatch durably, then
settles only complete valid Usage with the requested model identity. SDK retries
are zero. Unknown Usage blocks the entire task and retains null Usage/cost for that
request, rather than treating it as free. Known totals explicitly exclude unknown
consumption. A budget overrun with reliable Usage retains the Usage and stops.

A single worker file lock prevents duplicate workers/starts. On restart, dispatched
unsettled requests become unknown; other interrupted tasks stop without replay.
Completed stage outputs remain readable. This first version has no automatic paid
resume: it does not reconstruct or splice a failed task from older runs.

## P4 dual-ledger contract

P4 continues to use P4BudgetController/P4BudgetStore for minimum writing + consistency
capacity, request limits, report-local reservation, settlement, partial-result
persistence and atomic draft completion. The bridge uses the **same request UUID**
in the total task ledger. Only the task ledger contributes to whole-task totals;
the P4 ledger is not added a second time.

P4 first reserves/marks its local call; the bridge must durably reserve/mark the
whole-task call before invoking the SDK. If whole-task admission fails, P4 records
the request as not sent. On return, the whole-task ledger settles, followed by P4.
Before another P4 request and before draft commit, the bridge compares both ledgers'
request identity, model, tokens and exact Decimal cost. Any persistence error,
missing settlement or mismatch fails closed. These are conservative separate
transactions: a crash in between may interrupt a report despite valid Usage; it
never authorizes replay or another request. Startup recovery takes the worker lock
before reconciling, so a second app instance cannot invalidate a live P4 request.

Zero-topic internal reports use P4's existing explanatory path and persist a completed
generation identity with zero calls. No scientific finding, approval or Usage is
fabricated. Nonempty reports preserve P3 verdicts, coverage gaps, pending human
review and the strict P4 output contract. Final export still needs valid approval.

## Offline verification

```sh
python -m pytest -q backend/tests
cd frontend && npm run build
```

`backend/tests/test_auto_review_full_chain.py` exercises actual HTTP routes, SQLite,
P2-A/B/C/P3/P4 engines and real Schema/source validation. Only OpenAI's SDK boundary
is replaced. It covers nonempty consolidation and verification, P4 writing and
consistency, unknown Usage at several stages, duplicate/concurrent starts,
post-dispatch DB failure, deadlines, source drift, requirement isolation and
P4 ledger mismatch. P2-A integration and budget tests retain auth/restart/preflight
coverage. Real browser P4 editing/approval and exports remain regression tested.

For a new **private disposable directory** only:

```sh
AUTO_REVIEW_TEST_DATA=/private/disposable-test-data \
  python -m uvicorn backend.tests.serve_auto_review_fake:build_app --factory --port 8139
VITE_API_TARGET=http://127.0.0.1:8139 npm --prefix frontend run dev -- --port 5199
PLAYWRIGHT_BASE_URL=http://127.0.0.1:5199 \
  npm --prefix frontend exec playwright test -- tests/auto-review.spec.ts --workers=1
```

The test server hardwires a Fake SDK and synthetic credentials. It must never be
used as a production server or pointed at historical data. It optionally accepts
AUTO_REVIEW_TEST_PDF; model answers are still fixtures, not scientific evaluation.
The P5 deterministic CI also runs this browser path; paid CI remains disabled.

Passing these tests demonstrates software integration, **not real-model scientific
review quality**. Real paid end-to-end acceptance requires separate authorization,
a reviewed code version, actual provider configuration and account risk checks.
