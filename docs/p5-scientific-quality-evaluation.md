# P5 scientific review quality evaluation

Status: **incomplete — fresh holdout model outputs unavailable**  
Evaluation schema: `1.0`  
Evaluator boundary: Codex non-expert, source-grounded checks; not an expert gold standard

## Result boundary

The required scientific-quality conclusion cannot yet be issued. Both holdouts completed a fresh P1 run, but the environment stopped at the P2-A credential gate before any candidate issue was generated. Consequently there is no honest basis for measuring holdout issue validity, blind-reference coverage, important misses, P3 conservatism, P4 writing quality, or ChatGPT competitiveness.

The three historical papers have complete saved P2–P4 outputs, but those outputs participated in prior development. Their P3.1 results are `recorded_model_replay`, not fresh P5 calls. They are used here only to validate the evaluation method and to identify an operational bottleneck; they do not satisfy P5-C.

## Evaluation method

Each issue judgment uses the task-book categories:

- **A** — independently auditable technical or factual concern.
- **B** — source-grounded scientific clarification worth an author response.
- **C** — false or sufficiently refuted concern.
- **D** — unresolved because critical evidence is unavailable or unverified.
- **E** — plausible wording with low academic value.

Class D is reported separately and never counted as a false positive. The effective-issue ratio is `(A + B) / (A + B + C + E)` and is always published with its numerator, denominator, and D count. A PDF coordinate match alone is not treated as semantic or independent factual verification.

The versioned `ScientificEvaluation` contract requires every assessed issue to carry source-result execution mode, Source Ref, PDF page, rationale, evidence-support level, scientific value, and actionability. `scripts.p5.scientific_quality.compute_quality_metrics()` implements the denominator rule.

## Historical sentinel checks

Only three sentinel issues received a new P5 manual classification. Full records are in [Shaw](examples/p5-regression/shaw2018-scientific-evaluation.json), [DistilBERT](examples/p5-regression/distilbert2019-scientific-evaluation.json), and [fastText](examples/p5-regression/fasttext2016-scientific-evaluation.json).

| Paper / issue | P3 state | P5 class | Reason |
|---|---|---|---|
| Shaw unseen-length generalization | narrowed; qualified | B | The source supports architectural applicability; requesting the boundary of any performance interpretation is specific without alleging an error. |
| DistilBERT prose/Table 2 scores | narrowed; human review | D | The numerical difference is material, but the table's model–metric row/column relation still lacks auditable visual confirmation. |
| fastText single-epoch versus complete-training speed | narrowed; qualified | B | The paper labels the table as one epoch and states five fastText epochs; a scope clarification is useful without declaring the timings wrong. |

For this deliberately small sentinel set: A=0, B=2, C=0, D=1, E=0. The effective-issue result is **2/2**, with **one unresolved item**. This is a contract check and qualitative spot check, not a system precision estimate: 21 other historical P3 issues and every holdout issue remain without a new P5 independent judgment.

## P3 conservatism diagnostic from historical replays

The saved P3.1 records contain 24 consolidated issues: 20 `inconclusive` and four `concern_narrowed`. Two were `eligible_with_qualification`; 22 require human review. P4 retained 1/0/1 default author-facing issues for Shaw/DistilBERT/fastText.

A preliminary primary-blocker inventory, derived from outstanding checks and source-quality states rather than a new scientific ruling, is:

| Primary unresolved dependency | Count | Interpretation |
|---|---:|---|
| Unverified table or formula relationship | 14 | The system can locate text but cannot close a claim that depends on cell alignment, exact transcription, or arithmetic. |
| External code, literature, hardware, or data unavailable | 7 | P3 correctly avoids presenting an external assumption as verified, but the workflow does not obtain the needed material. |
| Support extraction incomplete or claim scope unresolved | 2 | Located text narrows the question without independently proving the remaining interpretation. |
| Verification capability missing | 1 | A dataset reconstruction question requires identifiers or scripts not available to the recorded run. |

This distribution does **not** show that 22 concerns are invalid. It shows that the current chain often ends at “potentially useful but not independently closed.” There is no evidence in this small replay that simply lowering a server threshold is the right fix: most blockers require visual source confirmation or external material. At the same time, allowing only two of 24 issues into default author drafts creates a substantial reviewer workload and may suppress useful questions if no practical human-review UI exists.

The P3 diagnosis is therefore: **conservative behavior is safety-preserving but operationally incomplete**. Whether it is scientifically over-conservative must be decided from fresh holdout issues and independent source review, which are not available in this environment.

## Blind-reference coverage and misses

The two holdout reference sets were frozen before EtPaper output and each contains six enumerated, source-grounded challenge questions. They are not complete defect inventories. Because EtPaper produced no P2-B candidates for either holdout:

- semantic matching is `not_assessable`, not 0% coverage;
- important-issue omission rate is `not_assessable`;
- no miss can be attributed to P2-A, P2-B, P2-C, P3, or P4;
- no false-positive count exists for the holdouts.

Calling these values zero would conflate an unexecuted model chain with a completed review that found nothing.

## ChatGPT benchmark

Status remains `pending`. No independent ChatGPT webpage output, model/mode record, or unpolluted prompt was supplied. No parity or inferiority conclusion is made.

## Evidence still required for a scientific verdict

1. Fresh P2-A through P4 outputs for both frozen holdouts and at least one historical paper.
2. Issue-level source checks for every author-facing issue and a representative sample of blocked internal issues.
3. Semantic matching to the frozen challenge sets, with partial matches and disagreements adjudicated from the PDF.
4. Auditable visual confirmation for critical table/formula claims and explicit external-material status.
5. Independent domain-expert review if the result is to be described as expert-level.
6. Independent ChatGPT webpage reference output for benchmark comparison.
