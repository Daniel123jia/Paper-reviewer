# EtPaper AI P5 final assessment

Assessment date: 2026-11-06  
Frozen P1–P4 baseline: `c4b613f486b4db6f118dc0ef99b7c0c103676843`  
P5 branch: `feature/p5-end-to-end-acceptance`  
Overall P5 decision: **FAIL**

This result does not revoke the accepted P1–P4 stage implementations. It means the evidence produced in this environment does not satisfy the stricter P5 claim that a new paper can complete a stable, scientifically evaluated, user-operable professional-review chain.

## Four independent decisions

| Dimension | Decision | Evidence and boundary |
|---|---|---|
| Engineering Readiness | **FAIL (not demonstrated end to end)** | Both holdouts completed fresh P1, but P2-A was blocked before provider execution because no model credential was configured. No historical paper or holdout completed a fresh P1–P4 P5 chain. Deterministic lineage, budget, failure, persistence and approval tests pass, but they cannot replace a production-path run. |
| Scientific Review Quality | **FAIL / not assessable for holdouts** | No holdout candidate issue exists. Historical replays show two useful B-class sentinel clarifications and one unresolved D-class table issue, but those development papers are not blind and the three checks are not a precision/recall estimate. |
| Product Usability | **FAIL for the complete workflow** | The web UI still sends “审稿分析” to the legacy sampled `review-case` path. It cannot start or inspect P2-A–P3. P4 review/edit/export is usable, but external-evidence verification is API-only. |
| Benchmark Competitiveness | **pending** | No independent ChatGPT webpage output was supplied. No comparison or parity claim is made. |

Overall status is FAIL because the task book identifies “a new paper cannot complete any real professional-review chain” as blocking. The credential is an environment prerequisite rather than a hidden model failure, but acceptance cannot infer successful behavior beyond the point actually executed.

## What passed

1. **Frozen protocol and test set.** Five exact paper versions and hashes are recorded. The two holdout challenge sets were committed before EtPaper output was viewed and are explicitly non-expert references.
2. **Fresh P1 on both holdouts.** SARDet-100K and SAM produced new document snapshots, Source Bundles and parse-quality reports from the production API.
3. **Honest state accounting.** P2-A is recorded as `blocked_missing_credential`, downstream stages as not started, and model usage as zero calls/zero Tokens. No replay is labelled fresh.
4. **Budgeted runner and lineage audit.** The runner preserves partial artifacts, enforces inter-stage call/Token/time ceilings, separates unknown usage, validates source identity and candidate/task lineage, and emits versioned ledgers.
5. **Safety regressions.** Cross-document source use, stale approval, unapproved final export, external-fact approval without evidence, invalid model structures and unknown-usage accounting are covered deterministically.
6. **P4 subflow.** Browser editing, save/reload, PDF evidence jump, history, Draft export, exact-version approval and approval invalidation passed. Approved Markdown, DOCX and PDF were generated and inspected.
7. **Sensitive-data boundary.** Public files contain hashes, counts, normalized states and selected public-paper audit material only. PDFs, SQLite files, full P1 bundles, rendered pages and operational errors stay in ignored local storage.

## Blocking defects and missing evidence

### B1 — No fresh P2-A–P4 execution

Severity: blocking. Both holdout runs stopped at the model credential gate. The minimum requirement of one fresh historical chain, two fresh holdout chains and one completed holdout is unmet. Actual returned model ID, model Tokens, retries, P2-A–P4 wall time and single-production-run cost remain unavailable.

### B2 — New professional-review web chain is not exposed

Severity: blocking for Product Usability. Upload and P4 exist in the website, but P2-A, P2-B, P2-C and P3 require direct API or a developer script. The visible review tab remains the legacy 30-passage sampling flow, so a normal user cannot perform the promised workflow.

### B3 — External verification is API-only

Severity: blocking for evidence-review closure. P4.1 correctly separates reviewer approval from external verification in the backend, but the browser has no form for literature, code or data verification records.

### B4 — Scientific quality cannot be estimated

Severity: blocking. Without holdout candidates, A/B/C/D/E classification, challenge-set coverage, important misses, false positives, evidence accuracy, P3 conservatism and author-report usefulness cannot be evaluated on unseen papers.

## Non-blocking but material defects

1. The Chinese formula/table PDF retained all content, but placed the small table alone on a mostly empty second page. The “no unreasonable blank page” criterion failed for that fixture.
2. DOCX/PDF export requires Pandoc on PATH. CI installs it, while this workstation needed the virtual environment's bundled Pandoc 3.9 to be exposed explicitly. Missing Pandoc returns a controlled export failure and preserves the draft, but deployment setup is fragile.
3. SARDet-100K has eight pages and SAM nine pages with unverified reading order. Formula, table and figure review readiness is blocked for both. A later model run must preserve these limitations rather than treating the inventory as verified content.
4. Historical P3.1 replays leave 22/24 issues at `requires_human_review`; most depend on table/formula visual checks or external material. Safety is good, but the current UI cannot efficiently close all such checks.
5. Browser download interruption has not been deterministically injected. Conversion failures are covered at the API layer.

## P3 conservatism conclusion

The saved historical results do not justify lowering P3 thresholds. Fourteen of 24 issues primarily depend on unverified table/formula relations, seven on unavailable external material, two on incomplete support extraction or claim-scope resolution, and one on missing reconstruction capability. These are mostly missing verification inputs, not evidence that a simple rule threshold is suppressing otherwise proven findings.

Operationally, however, only two issues are automatically eligible with qualification and the website lacks a complete route for resolving the rest. P3 is therefore safety-preserving but incomplete as a reviewer workflow. A fresh holdout run is required before judging whether it is scientifically over-conservative.

## Next-version priorities

1. **P0 — Execute the frozen evaluation, not new feature work.** Configure a credential outside Git/frontend, run a tiny capability probe, then run one historical paper and both frozen holdouts with the committed budgeted runner. Do not alter core prompts after seeing outputs.
2. **P0 — Complete issue-level scientific adjudication.** Match holdout outputs against the six frozen challenge issues per paper, inspect all author-facing evidence, sample blocked internal issues, and report A/B/C/D/E counts with denominators.
3. **P0 — Close the website workflow.** Add a minimal P2-A→P3 run/progress/result path and external-evidence review form under a separately approved implementation task. Do not route it through the legacy baseline.
4. **P1 — Make visual source checks auditable.** Support table/formula region review and preserve object labels, page coordinates, reviewer identity, remaining uncertainty and decision linkage.
5. **P1 — Fix export pagination and dependency discovery.** Prevent orphan tables/mostly blank pages and document or reliably discover Pandoc.
6. **P1 — Obtain an independent benchmark.** Freeze an uncontaminated ChatGPT webpage review and, preferably, domain-expert assessment before making competitiveness claims.
7. **P2 — Establish performance/cost baseline.** Only after successful fresh chains, report per-stage wall time, provider-call time, Token use, retries, unknown usage, human time and reliable price source if available.

## Re-entry criteria for P5

P5 may be rerun against the same frozen holdouts without changing prompts if:

- a model credential is configured through a non-repository secret mechanism;
- the exact PDF hashes remain unchanged;
- no P1–P4 prompt or scientific gate changes after holdout results are opened;
- all full responses remain private while sanitized ledgers retain actual model IDs and usage;
- at least one holdout completes all executable P1–P4 stages;
- issue-level independent judgments and product actions are actually performed.

If core prompts or admission rules change, the affected result is a new evaluation version and cannot be combined with this blind-run record.

## Final local verification

- Full backend: **250 passed, 2 skipped**.
- P5-focused contracts, runner, metrics, manifests and product exports: **23 passed**.
- Frontend production build: **passed** (bundle-size warning only).
- Isolated real-PDF and retained-baseline browser regressions: **6/6 passed**.
- Isolated P4 evidence/edit/reload/download browser regression: **1/1 passed**.
- Approved English PDF and DOCX: visually legible; one A4 page; no internal Evidence ID in PDF.
- Chinese/math/table draft: content preserved, but visual pagination failed because the table was orphaned onto a mostly empty second page.

These deterministic results support the specific engineering controls described above. They do not change the P5 FAIL decision because no fresh model-backed P2-A–P4 chain completed.
