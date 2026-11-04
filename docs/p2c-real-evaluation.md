# P2-C real-paper consolidation evaluation (2026-11-05)

## Material and source boundary

The three inputs are the accepted P2-B **actual** outputs for [Shaw 2018](examples/p2b-shaw2018/), [DistilBERT 2019](examples/p2b-distilbert2019/), and the [fastText 2016 holdout](examples/p2b-fasttext2016/). Their public PDF links, SHA-256 hashes, P2-A/P2-B call costs and original 36 candidate records are in the [P2-B evaluation](p2b-real-evaluation.md). P2-C read the final P2-A/P2-B rows and the **complete frozen Source Bundle** from the prior private SQLite run databases. It did not rerun P2-A or the six P2-B dimensions and did not reconstruct a Source Bundle from the shortened public Evidence Mapping export.

The final P2-C structured results were revalidated against each frozen bundle, P2-A ClaimEvidenceMap and P2-B candidate list before export. Public output directories are [Shaw](examples/p2c-shaw2018/), [DistilBERT](examples/p2c-distilbert2019/) and [fastText](examples/p2c-fasttext2016/). Each has the full `candidate_consolidation_result.json`, original inventory, all pair relations, consolidated issues, complete candidate lineage, coverage audit, P3 task list and measured run summary. Original Evidence IDs, pages and coordinates for cited Source IDs remain in the corresponding P2-B `evidence_mapping.json` files. The CI test of these public exports checks schema, lineage and public mapping consistency; it cannot repeat full frozen-bundle validation because the complete bundle is not committed.

## Execution and cost

All three P2-C runs used actual `gpt-6.1-sol` responses through the existing OpenAI-compatible interface. Every pairwise batch and global reconciliation call completed with known Token usage; no retries were needed. The run state is `limited` because the accepted P2-B inputs and some PDF objects remain source-limited, **not** because the grouping calls failed.

| Paper | Original candidates → consolidated issues | Pair relation counts (exact / semantic / related distinct / distinct) | Calls | Input / output Tokens | Wall time | Coverage gaps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Shaw 2018 | 11 → 6 | 2 / 6 / 11 / 36 | 4 | 49,344 / 6,047 | 135.8 s | 37 |
| DistilBERT 2019 | 13 → 9 | 1 / 3 / 28 / 46 | 5 | 70,882 / 8,354 | 181.6 s | 41 |
| fastText 2016 | 12 → 9 | 0 / 4 / 20 / 42 | 5 | 65,344 / 7,220 | 160.0 s | 35 |

Total P2-C.1 model usage: **14 calls, 185,570 input Tokens, 21,621 output Tokens, 477.4 s summed wall time**. All final calls reported Tokens; unknown-token calls and retries were zero. These counts are accounting and audit data, not evidence that a particular number of issues is scientifically correct. Raw requests/responses are confined to ignored private files with restrictive permissions; no API key or raw call payload is exported to GitHub. The run uses P2-C schema `1.1`, pair Prompt `candidate-semantic-grouping/v1` and reconciliation Prompt `candidate-group-reconciliation/v2`.

Compared with the accepted P2-C commit, the rerun kept the exact same candidate membership in every group and the same positive/counterevidence reference sets. The new group-level central questions express the shared doubt and verification need, while every original verification question remains in its issue and P3 task. This check is about preservation and wording; it does not establish that the scientific concerns are true.

## Manual review of grouping decisions

This appraisal compares original candidate text, relation judgments, consolidated lineage and selected original reading units. It is **not** P3 verification of equations, tables, code, outside literature or candidate validity. All issues and tasks remain `unverified`.

### Shaw 2018

- [Unseen-length generalization](examples/p2c-shaw2018/consolidated_issues.json) (`consolidated_e3eac49834cebb136759`) combines four D1–D4 candidates that question the same scope of evidence. Every original ID and verification question remains in the lineage and issue record.
- Baseline comparability (`consolidated_9825c343099d3753e676`) and measured efficiency (`consolidated_105a91210515bf8f5b82`) each combine D2/D3 variants. Storage-complexity accounting (`consolidated_fe5b94a19e9e06327e40`) stays separate from measured runtime: the PDF's page 3 source unit reports the representation-storage bound, while another reports a 7% decrease in steps per second. These need different checks.
- BLEU evaluation protocol and the combined-position no-benefit claim remain independent singletons. Table 3 Key/Value ablation is not recast as missing. D6 has no candidates but an inspection explanation and **no `dimension_unfinished` gap**.
- Targeted check found no confirmed false merge, evidence loss or fabricated new observed fact. Baseline fairness and BLEU-protocol questions are related, but a matched comparison and an exact scorer/pipeline are different verification needs. Source table/formula status still limits scientific judgment.

### DistilBERT 2019

- Triple-loss specification (`consolidated_c9750ce2c32914386e93`) joins D2/D5; component-ablation attribution (`consolidated_8e8e2b5dcfc41219ae35`) joins D2/D3. Pretraining-resource comparison (`consolidated_74d31eae282a7e352cfb`) is kept separate from those method questions and from CPU/mobile inference timing.
- The SQuAD source difference (`consolidated_2fc7365d65828d021acd`) joins D3/D4 and retains both prose and Table 2 source roles: the prose cites 70.4 EM / 79.8 F1, while the unverified Table 2 extraction lists DistilBERT (D) as 79.1 / 86.9. The original reading-unit mapping points to both PDF regions. This is a **reconciliation task**, not a confirmed author error; the settings still need independent checking.
- The D1 candidate about pretraining-distillation advantage overlaps with both the ablation and task-specific-distillation groups. Its original text contains more than one verification angle. The current one-owner lineage keeps it as a separate issue and preserves all its questions, rather than attaching it to either group by transitivity. **Potential missed merge / mixed-candidate risk:** P3 may split those checks into separate steps or revise the grouping after source inspection.
- Targeted check found no confirmed false merge, lost source role or invented experiment. The PDF table and formula transcriptions remain unverified.

### fastText 2016 holdout

- D3/D4 runtime and time-to-quality variants combine (`consolidated_8103558cbdda88bc3c6c`). The Huffman training-complexity assumption (`consolidated_64b696e6ae9642e79b3d`) and prediction/top-T complexity (`consolidated_7065266f221bc62de86a`) stay separate from runtime measurement and each other. D1's attribution of algorithmic versus implementation speed and D5's reproducible timing boundaries also remain independent checks.
- D1/D4/D6 word2vec-positioning concerns combine (`consolidated_1a30bff6ccc7941967da`). The original D1/D4/D6 external-verification flags disagree; the issue and task conservatively require external verification and expose that disagreement. No prior-paper result is invented.
- YFCC filtering/split reconstruction (`consolidated_456a6e99f6fb075f3912`) stays separate from the D3 conditional concern about filtering before splitting (`consolidated_23e6a7c956e176a37f5c`); the former asks how to reproduce the split, the latter whether held-out frequency could influence it. Tag-set to single-label handling remains a third task. None is treated as established leakage or author error.
- Targeted check found no confirmed false merge or evidence loss. External scripts and datasets may resolve these concerns; P2-C did not inspect them.

## Coverage and handoff limits

CoverageAudit exposes all six dimension records and all P2-A contributions/Claims for each paper. Its inspection evidence level is `model_report_only`. A focus is `reported_partial` only when the P2-B free text explicitly names that focus and the dimension has partial source coverage. Otherwise the free text cannot establish focus-specific inspection, so the focus is `mapping_uncertain` with a gap. Shaw has 1 reported partial and 17 uncertain foci; DistilBERT has 18 uncertain; fastText has 2 reported partial and 16 uncertain. None is called fully verified. Gap totals above break down as: Shaw 17 focus-mapping, 12 source-quality, 6 Claim-not-reported-inspected, 1 required-material, 1 external-material; DistilBERT 18/17/4/1/1; fastText 16/15/2/1/1. These are **review/source tasks, not author defects**. The full [CoverageGap lists](examples/p2c-shaw2018/coverage_audit.json) and their paper-specific counterparts identify affected IDs, causes and next actions.

Every consolidated issue has one [P3 VerificationTask](examples/p2c-shaw2018/verification_tasks.json) with its frozen snapshot, original candidates, original positive/counterevidence roles, all preserved questions and limitations. The [lineage files](examples/p2c-shaw2018/candidate_lineage.json) show a single counted owner for every original candidate; the complete per-paper files are linked above. P3 may refine or reject concerns after independent PDF, mathematical, code and external checks. P2-C has not done those checks.

## Remaining risks and benchmark status

1. Pairwise model classifications and reconciliation can still produce missed or false merges despite server-wide clique checks. DistilBERT's mixed D1 candidate is a concrete review item. Human academic adjudication remains necessary.
2. P2-A/P2-B source limits, unverified tables/formulas and outside materials survive into CoverageGap and VerificationTask. Public exports cannot replace the private frozen Source Bundle for a new full-source validation.
3. Pairwise comparison scales quadratically for large candidate sets. The budget can stop later batches; the engine records every remaining pair as uncertain and keeps every original candidate. Runtime and Token optimization are deferred.
4. Production retention and access control for unpublished PDFs and private diagnostics remain a deployment gate. The evaluation used only public papers, and raw requests/responses were kept outside Git.
5. **ChatGPT web deep-review comparison: `pending`.** Reference reviews have not been supplied; no parity or superiority claim is made.

## Regression boundary

Local final-tree verification passed the complete P1/P2-A/P2-B/P2-C backend suite (**145 passed, 2 skipped**), the unchanged frontend build, and **5/5** existing Playwright PDF-reader/review regressions. The P2-C CI workflow repeats these checks on GitHub; the run link is reported in PR #4 after completion.
