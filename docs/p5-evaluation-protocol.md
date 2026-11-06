# P5 evaluation protocol

Status: **frozen before real P5 model execution**  
Protocol version: `p5-evaluation/v1.1`
Baseline: `c4b613f486b4db6f118dc0ef99b7c0c103676843`

## Purpose and separation of conclusions

P5 evaluates the existing P1–P4 chain; it does not tune its prompts or scientific admission rules. The final report must make separate decisions for engineering readiness, scientific review quality, product usability, and benchmark competitiveness. A pass in one dimension is not evidence of a pass in another.

The ChatGPT webpage comparison is `pending`. No reference output has been supplied, and no result may claim parity with ChatGPT or expert peer review.

## Execution modes

Every artifact and ledger entry uses exactly one mode:

- `fresh_model_run`: a new API request made by the frozen code and prompts.
- `recorded_model_replay`: deterministic re-adjudication from an earlier recorded response; not a new call.
- `deterministic_regression`: mocks, fixtures, validators, parsers, or failure injection.
- `manual_review`: source reading, visual checking, issue classification, editing, or approval by a person.

Replays and regressions never count toward the minimum fresh-run requirement. Unknown token usage is counted as an unknown-cost call, never as zero-cost usage.

P5.1 changes only the acceptance tooling. It does not alter the frozen papers, reference
questions, P1–P4 prompts, or scientific gates. Manifest `1.1` records historical paper-ID
aliases, and RunLedger `1.1` separately records the execution ID, canonical paper ID,
final-stage reachability, completed and limited stages, outstanding verification items,
lineage-audit outcome, and whether usage accounting is incomplete. Readers for both
contracts continue to accept version `1.0`.

## Frozen software and environment

| Item | Frozen value |
|---|---|
| Git baseline and initial P5 code | `c4b613f486b4db6f118dc0ef99b7c0c103676843` |
| Source Bundle schema | `1.0` |
| P2-A result schema | `1.1` |
| P2-B result schema | `1.0` |
| P2-C result schema | `1.1` |
| P3 result schema / decision rules | `1.1` / `p3.1` |
| P4 report schema / rules | `1.1` / `p4.1` |
| Python | `3.11.15` |
| Node.js / npm | `22.21.1` / `10.9.4` |
| OS | macOS `26.6.2` |
| Pandoc | `3.9` (bundled executable) |
| Playwright Chromium | installed (`chromium-1223`) |
| Requested model | `gpt-6.1-sol` unless the frozen run manifest records an explicit override |
| Endpoint disclosure | non-sensitive label only; host and account details are not published |

Active prompt versions are `paper-type-planner/v1`, `paper-understanding/v1`, `source-consistency/v1`, `contribution-evidence-mapper/v2`, `contribution-normalizer/v2`, the six P2-B dimension prompts plus shared rules at `v1`, `candidate-semantic-grouping/v1`, `candidate-group-reconciliation/v2`, P3 source/counterevidence/technical prompts at `v1`, `issue-adjudication/v2`, and P4 planning/writing/consistency prompts at `v1`. `scripts.p5.environment.prompt_inventory()` records each file hash without copying prompt text into public evaluation artifacts.

## Preflight result

| Check | Result |
|---|---|
| Backend baseline | 225 passed, 4 skipped |
| Frontend production build | passed |
| Python and Node | passed |
| Pandoc | passed |
| Playwright Chromium | passed |
| Public PDF download and local hashing | passed for both holdouts |
| SQLite isolation and API regression | passed by the baseline suite; repeated in P5 deterministic CI |
| Model credential | **not configured in shell, launch environment, project env files, or the checked application keychain label** |
| Small API capability probe | `blocked_missing_credential`; no full-paper call was used as a probe |

The credential result is an execution-environment limitation, not a simulated model failure. P5-C fresh runs cannot be claimed complete until a credential is configured outside Git and the frontend.

## Budget basis and frozen per-paper ceiling

Historical final successful-path records show approximately 11–13 P2-A calls, 6 P2-B calls, 4–5 P2-C calls, and 23–32 P3 calls per paper. Their combined known input usage ranges from roughly 640k to 740k tokens before final P4 writing. Development-wide P4 call totals and failed retries are explicitly excluded from the production estimate.

Each P5 paper therefore receives the same conservative ceiling:

| Budget | Ceiling |
|---|---:|
| Model calls | 90 |
| Known input tokens | 1,200,000 |
| Known output tokens | 220,000 |
| Wall-clock time | 10,800 seconds |
| Explicit retries | 2 |
| USD cost | `unknown` until a reliable price source is configured |

Stage limits remain at or below the frozen application limits. The runner checks the aggregate paper budget before starting the next stage and preserves partial work when a limit is reached. P4 model calls are included in the ledger. The runner first reads the `model_calls` returned by each draft API response. An explicitly supplied and validated SQLite path is a fallback for an older or interrupted API response; absence of both sources is recorded as `usage_incomplete`, never as zero calls. Failed calls and calls with missing Token fields remain in the ledger, and a call with missing usage consumes a call slot.

The complete P4 acceptance path creates internal and author drafts in both Chinese and
English. Author drafts are required to remain pending, every automatically selected issue
must map to an eligible P3 adjudication and report-plan group, and a final export request is
expected to return HTTP 409 before human approval. The runner never creates reviewer
decisions or approvals.

## Blind evaluation order

1. Freeze paper title, version, URL, PDF hash, and test group.
2. Read each holdout without viewing EtPaper output and freeze source-grounded reference issues.
3. Commit the manifest and references.
4. Run EtPaper P1–P4 without prompt or rule changes.
5. Match scientific questions semantically, inspect source evidence, and classify each output A/B/C/D/E.
6. Trace misses and conservative outcomes to the responsible stage.
7. Execute product, approval, and export checks separately from scientific scoring.

Codex-authored reference issues are labelled `non_expert_source_grounded_reference`; they are not an expert gold standard. Any later expert or ChatGPT reference must remain independent and versioned rather than silently replacing this set.

## Data protection and publication policy

Ignored `data/p5-evaluation/` holds PDFs, databases, full structured responses, rendered pages, and operational logs. Public `docs/examples/p5-*` contains only sanitized summaries, issue-level evidence excerpts that are necessary for audit, stable identifiers, and aggregate usage. It excludes API keys, endpoint hosts, access tokens, raw full requests/responses, private papers, and private databases.

The runner never serializes environment variables or authorization headers. Public error fields contain an exception class or normalized category, not provider payloads that might include sensitive material.

## Stop and invalidation rules

- A prompt or core decision-rule change after holdout results are visible creates a new evaluation version and invalidates affected blind-run claims.
- Cross-document evidence, nonexistent source references, false completed states, approval bypass, stale approval after editing, or sensitive-data disclosure is blocking.
- A limited stage remains limited downstream; P4 cannot upgrade an unverified external, table, formula, or model interpretation into an established fact.
- A passing lineage audit is recorded independently and cannot upgrade any P2-B, P2-C, P3,
  or P4 `limited` stage to `completed`.
- P5 stops after opening its PR. It does not merge `main` or deploy.
