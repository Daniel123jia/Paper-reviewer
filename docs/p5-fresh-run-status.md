# P5 fresh-run status

Status date: 2026-11-06 (Asia/Shanghai)  
Execution mode: `fresh_model_run`  
Result: **P1 completed for both holdouts; P2-A–P4 blocked before model execution**

## What was actually executed

Both frozen holdout PDFs were uploaded through the production API into an isolated P5 data directory. The production P1 parser created new document snapshots, Source Bundles, reading units, parse-quality reports, and input-readiness decisions. These were new executions, not fixture replays.

| Paper | P1 duration | Pages | Reading units | Unverified-order pages | Section summary | Formula / table / figure checks |
|---|---:|---:|---:|---:|---|---|
| SARDet-100K | 8.581 s | 32 | 669 | 8 | `needs_review` | `blocked` / `blocked` / `blocked` |
| SAM | 6.255 s | 20 | 638 | 9 | `needs_review` | `blocked` / `blocked` / `blocked` |

The P2-A creation request then returned the application's normal missing-credential response. The evaluation runner recorded a separate `blocked_missing_credential` P2-A stage for each paper. Neither run made a provider call, so the measured usage is zero calls and zero tokens—not an unknown-usage call and not a model failure.

## What this does not establish

- No fresh P2-A, P2-B, P2-C, P3, or P4 model result exists for either holdout.
- No holdout issue-detection, false-positive, missed-issue, evidence-quality, or report-quality score can be computed yet.
- The parser inventory does not prove that formulas, tables, figures, or reading order are correct.
- Historical saved results may support regression checks only; they cannot substitute for the required fresh P5 run.
- The ChatGPT webpage benchmark remains `pending` because no independent reference result was supplied.

The complete PDFs, P1 artifacts, local SQLite database, and normalized operational errors remain under ignored `data/p5-evaluation/`. Public summaries contain only hashes, counts, state transitions, aggregate usage, and limitations.
