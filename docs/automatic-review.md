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
- `GET /api/documents/{id}/auto-review-budget-suggestion`: offline short-paper
  recommendation based on the parsed source. Saved tasks keep their frozen limits.

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

For new tasks the UI defaults to the short-paper limits only with 1–8 pages, at
most 350 reading units and at most 40,000 UTF-8 bytes of extracted unit text.
The user can choose the standard preset or edit individual limits before
creating the task. The short preset uses up to 60 calls, 900,000 input and
162,000 output Tokens, and 1.45 USD planned cost at the unverified default rates.
These are software ceilings with room above the measured short-paper run.
A dense paper or one that produces more verification issues can need the standard
preset. The short-paper N2 path uses 18,000-character partitions instead of
12,000 while preserving all reading units, the N1 planning sample, N3 partitions
and checkpoints. Six review dimensions and independent counterevidence checks
stay in place.

Automatic P4 can write exactly two compact internal topics together before a
separate consistency request. The same selected batch count controls P4 admission
and writing. The combined plan and projected pack are bounded to 16,000 UTF-8
bytes, and the configured per-request output must allow at least 2,000 Tokens.
Larger packs retain the separate writing/check requests. Group, source and
evidence validation runs for every topic. Standalone and C1 keep their original
batching.

An offline paired check with the public 6-page GLOBECOM paper (real parser,
engines and ledgers, Fake SDK) reduced calls from 34 to 30 and input message
bytes from 529,321 to 507,309. The supplied 14-page check used the standard
preset and went from 52 to 50 calls. The fixture establishes routing and
coverage preservation. Actual provider Usage and the scientific quality of
larger batches require live evaluation.


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

## Bounded parallel performance (ordinary auto review only)

New tasks may include `execution_policy` with `max_concurrency` 1 or 2,
`requests_per_minute` (1–120, default 60), and
`input_reservation_per_minute` (1–2,000,000, default 1,000,000).
API clients omitting it retain serial execution; the web form explicitly saves
its two-worker policy with the pending task for budget confirmation. Existing
tasks are not rewritten. Limits are local; the supplier/account rates are
**unverified** and must be checked before a paid acceptance run. The input rate
uses the same conservative UTF-8 byte reservation as the budget, not measured
supplier tokens or a guarantee about hidden gateway tokens.

P2-A independent N2 parts and N3 partitions, P2-B independent dimensions, and
P3 independent VerificationTasks run in waves of at most two. N1, global
synthesis, source consistency, contribution normalization, P2-C, and P4 checks
keep their dependencies. All N3 partitions still cap at 9,000 characters.
For ordinary auto papers up to 15 pages, N2 parts use 18,000 characters instead
of the long-paper 12,000 default. This covers every reading unit; larger per-part
outputs could still truncate or lose detail, requiring real blind quality review.
JSON separator compaction removes whitespace, not scientific fields or text.

SQLite transactions account for **all** in-flight input/output/cost reservations
against total and stage budgets. Dispatch is the durable linearization point.
Unknown Usage latches immediately in memory as well as SQLite, including if
persisting the error fails; no next wave or new SDK dispatch is permitted.
Already-dispatched peers drain and settle their own real Usage or remain unknown.
The engine-local concurrent budget is also atomic and measures elapsed wall time.
Rate waiting rechecks the latch and deadlines and does not retry a provider call.
P3's PDF handle and region operations have one owner thread; model waits alone
overlap. P4 remains serial with the original two-ledger reconciliation.

Task queries include `execution_policy` and `performance`. Per-call timestamps,
operation, input bytes, hashes (no raw content), measured Token/cost and status
are persisted. Stage `model_wait_sum_ms` sums completed SDK spans;
`model_wait_union_ms` counts their overlap once. The critical-path number is
only the union of model spans in sequential stages, not total end-to-end latency.
It includes transport/local settlement, not just inference. Historical missing
timestamps remain unrecorded, never invented as zero. P1 parsing is timed by the
offline benchmark; stage persistence/assembly and user confirmation wait must be
reported separately in real acceptance. The web audit panel shows these limits.

Run a **Fake-only** identity-checked benchmark from a checkout using:

```sh
python scripts/auto_review/benchmark.py --pdf /private/input.pdf --paper short6 \
  --manifest docs/examples/auto-review/papers.json --output /private/new-run \
  --concurrency 2
```

Its fixture budget/rates are deliberately generous to exercise the engines,
not a recommended paid budget. It cannot execute the provider. Fixed PDFs,
source coverage, request bytes, stage calls, draft and identity hashes are saved;
Fake Token counts do not measure real usage. The scientific blind evaluation
form and independent acceptance gates are in `automatic-review-quality-rubric.md`.
Five-minute performance and academic quality remain **unverified**, regardless
of how fast Fake SDK runs. No P50/P90 of real runs is currently available.

阶段墙钟另行持久化实际开始和结束时间（含本地处理）；请求跨度与累计模型等待分开显示。历史记录缺少计时时显示“未记录”，不反推为零；P1解析时间由离线基准的上传端点墙钟单列。
