# EtPaper AI P5 end-to-end acceptance index

P5 evaluates the frozen professional-review chain merged at `c4b613f486b4db6f118dc0ef99b7c0c103676843`. It is separate from the older `phase5-acceptance.md` document-delivery milestone.

Final decision: **FAIL**. The evaluation produced useful engineering and product evidence but could not execute a fresh model-backed P2-A–P4 chain. See the [formal assessment](p5-final-assessment.md).

## Deliverables

- [Frozen protocol, environment and budgets](p5-evaluation-protocol.md)
- [Paper manifest and SHA-256 values](p5-evaluation-manifest.md)
- [Machine-readable frozen manifest](examples/p5-regression/evaluation-manifest.json)
- [Fresh-run status and measured P1 results](p5-fresh-run-status.md)
- [Scientific-quality method and current evidence](p5-scientific-quality-evaluation.md)
- [Product, reliability and export acceptance](p5-product-usability.md)
- [ChatGPT benchmark protocol and pending status](p5-chatgpt-benchmark.md)
- [Final four-dimensional decision](p5-final-assessment.md)

## Sanitized examples

- [Historical regression checks](examples/p5-regression/)
- [SARDet-100K holdout](examples/p5-holdout-1/)
- [SAM holdout](examples/p5-holdout-2/)

Full PDFs, Source Bundles, SQLite databases, model diagnostics and rendered export pages are excluded from Git under `data/p5-evaluation/`.

## Execution tooling

- `scripts/p5/build_manifest.py` creates the versioned public manifest.
- `scripts/p5/run_evaluation.py` drives P1–P4 through existing APIs with a per-paper budget and partial-result ledger.
- `scripts/p5/audit_lineage.py` validates source identity, Source Refs, candidate lineage and P3 task disposition.
- `scripts/p5/scientific_quality.py` computes A/B/C/D/E metrics without treating unresolved issues as false positives.
- `scripts/p5/run_product_acceptance.py` executes deterministic edit/approval/export checks and renders PDF pages for manual inspection.

No P1–P4 production Prompt or scientific admission rule was changed in P5. Real model calls remain opt-in and are never run by CI.
