# P5 End-to-End Acceptance Implementation Plan

> Execution mode: inline on `feature/p5-end-to-end-acceptance`. P1–P4 production prompts and scientific decision rules remain frozen.

**Goal:** Evaluate the frozen P1–P4 chain on historical and unseen public papers, preserve an auditable run ledger, measure scientific and product quality, and publish a candid four-part acceptance decision.

**Architecture:** Add an evaluation-only Python package under `scripts/p5/`. It drives the existing HTTP API, validates source/run lineage, summarizes model calls without storing prompts or raw responses, and writes sanitized artifacts. Deterministic tests exercise the ledger, budgets, failure states, and lineage checks; real model calls remain opt-in and never run in CI.

**Technology:** Python 3.11, httpx, Pydantic, SQLite audit reads, FastAPI API, pytest, Playwright, Pandoc.

---

## Stage P5-A — Freeze and protocol

1. Add `scripts/p5/contracts.py` with versioned EvaluationManifest, PaperBudget, StageRun, and RunLedger contracts.
2. Add `scripts/p5/environment.py` for non-secret environment checks and prompt/schema inventory.
3. Add `docs/p5-evaluation-protocol.md`, initial `docs/p5-evaluation-manifest.md`, and execution directories.
4. Add deterministic contract/environment tests in `backend/tests/test_p5_acceptance.py`.
5. Verify focused tests and commit `test(p5): freeze evaluation protocol and environment`.

## Stage P5-B — Freeze paper and reference sets

1. Freeze Shaw 2018, DistilBERT 2019, fastText 2016, SARDet-100K, and SAM with official source URLs and PDF SHA-256 values.
2. Add blind-reference templates and source-grounded, pre-EtPaper reference issues for both holdouts. Label Codex-authored judgments `non_expert_source_grounded_reference`; never call them expert gold standards.
3. Add `docs/p5-chatgpt-benchmark.md` with status `pending` and contamination controls.
4. Add manifest validation tests for immutable paper IDs, unique hashes, and reference provenance.
5. Commit `docs(p5): freeze holdout papers and blind references` before any real EtPaper output is inspected.

## Stage P5-C — Real P1–P4 execution

1. Add `scripts/p5/run_evaluation.py`: upload one PDF, poll P2-A/B/C/P3, create P4 project/drafts, and save sanitized JSON summaries.
2. Enforce per-paper call/token/time/retry budgets and retain partial artifacts on failure.
3. Add `scripts/p5/audit_lineage.py` to check document SHA, source bundle, run IDs, evidence IDs, candidate lineage, P3 task disposition, and P4 admission.
4. Probe the configured model endpoint with a tiny request. If no credential is configured, record `blocked_missing_credential` while completing all deterministic work.
5. Run at least one fresh historical paper and both holdouts when credentials are available; require at least one holdout through all executable P1–P4 stages.
6. Store full private results under ignored `data/p5-evaluation/`; publish only sanitized summaries under `docs/examples/p5-*`.
7. Add deterministic fake-server runner tests and commit `test(p5): add budgeted end-to-end evaluation runner`.

## Stage P5-D — Scientific quality evaluation

1. Compare P2-B/P2-C/P3/P4 outputs against the frozen references at scientific-question level.
2. Classify each issue A/B/C/D/E with page/source evidence and a reason; report numerators, denominators, and undecided counts.
3. Trace misses to P1, P2-A, P2-B, P2-C, P3, or P4.
4. Diagnose `inconclusive` and `requires_human_review` outcomes without lowering P3 rules.
5. Add `docs/p5-scientific-quality-evaluation.md` and machine-readable sanitized evaluation files.
6. Keep ChatGPT comparison `pending` unless independent webpage results are supplied.
7. Commit `docs(p5): evaluate scientific review quality`.

## Stage P5-E — Product, reliability, and exports

1. Run API-chain acceptance and separately inventory what the web UI can and cannot perform.
2. Exercise edit/save/reload/history/approval invalidation/final-export blocking/reapproval.
3. Export Markdown, DOCX, and PDF; inspect DOCX structure and render PDF pages for visual checks.
4. Run deterministic injected-failure coverage for invalid PDF, parse limitation, timeout, invalid JSON, unknown usage, intermediate failure, budget exhaustion, duplicate starts, restart/readback, source isolation, missing source, and unapproved export.
5. Add `docs/p5-product-usability.md` and sanitized export checks.
6. Commit `test(p5): assess product workflow reliability and exports`.

## Stage P5-F — Final assessment and PR

1. Add `docs/p5-end-to-end-acceptance.md` and `docs/p5-final-assessment.md` with separate Engineering, Scientific, Product, and Benchmark decisions.
2. List blocking/non-blocking defects, evidence gaps, and next-version priorities. Do not infer broad-domain performance from five papers.
3. Add `.github/workflows/p5-end-to-end-acceptance.yml` for deterministic tests/build/browser regression only.
4. Run the full backend suite, frontend production build, and browser regressions; inspect `git diff` and secret scans.
5. Commit `docs(p5): publish end-to-end acceptance assessment`, push the branch, and open a PR to `main`.
6. Stop without merging or deploying.
