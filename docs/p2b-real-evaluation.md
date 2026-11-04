# P2-B real-model evaluation (2026-11-05)

## Scope and provenance

The final runs use the accepted P2-A code from main and this branch's P2-B engine, with fresh calls to the OpenAI-compatible `gpt-6.1-sol` model. Each paper was uploaded through the existing API, parsed into a frozen Source Bundle, processed by N1–N3, and then passed to all six P2-B dimensions. The final examples are **structured outputs, not verified review findings**. They are suitable for source-by-source independent inspection; the original request and response logs stay in ignored, access-restricted local storage.

| Paper | Public PDF | PDF SHA-256 | Selection |
| --- | --- | --- | --- |
| Shaw et al. (2018), *Self-Attention with Relative Position Representations* | [arXiv PDF](https://arxiv.org/pdf/1803.02155) | `8086e2e5e139fd576631ce0ec4560c5ee66225bec260d631a3e8fe7f1f13d50b` | Fixed regression paper; Key/Value ablation and formula-status checks. |
| Sanh et al. (2019), *DistilBERT* | [arXiv PDF](https://arxiv.org/pdf/1910.01108) | `a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6` | Fixed regression paper; three-objective training and cross-section SQuAD figures. |
| Joulin et al. (2016), *Bag of Tricks for Efficient Text Classification* | [arXiv PDF](https://arxiv.org/pdf/1607.01759) | `6cbcf620c2537ed54dfb9ed0d843a83689b82dc41dcd305bfb2e82425d4d7906` | Holdout paper, not used to tune production prompts or expected answers; different efficiency, data-split and baseline questions. |

The public output directories are [Shaw](examples/p2b-shaw2018/), [DistilBERT](examples/p2b-distilbert2019/), and [fastText](examples/p2b-fasttext2016/). Each contains `review_plan.json`, `paper_profile.json`, `claim_evidence_map.json`, all six `dimension_reviews.json`, the complete raw `issue_candidates.json`, `evidence_mapping.json` with original Evidence IDs and PDF coordinates, and `run_summary.json`. These are actual outputs from the final runs, not hand-authored expected answers. Source refs and P2-A/P2-B schemas were revalidated against their private frozen Source Bundles before export. The exported `evidence_mapping.json` contains only objects actually cited by candidates; it is not a substitute for the full PDF.

## Final run coverage and cost

| Paper | P2-A calls, input/output tokens | P2-B calls, input/output tokens | P2-B wall time | Raw candidates D1/D2/D3/D4/D5/D6 | Final run state |
| --- | ---: | ---: | ---: | --- | --- |
| Shaw | 13; 126,575 / 19,845 | 6; 165,597 / 13,738 | 400.8 s | 11 = 1/3/4/1/2/0 | `limited` |
| DistilBERT | 11; 103,274 / 20,087 | 6; 143,600 / 14,963 | 323.1 s | 13 = 1/2/3/3/3/1 | `limited` |
| fastText | 11; 105,190 / 18,324 | 6; 148,320 / 13,811 | 322.1 s | 12 = 2/2/2/2/3/1 | `limited` |

All 18 final P2-B model calls completed with known usage; final P2-B totals are **457,517 input tokens, 42,512 output tokens, and 1,045.6 s summed model-call duration**. P2-A plus P2-B final paths total **53 calls, 792,556 input tokens, 100,768 output tokens**. The six dimensions in each run report `partial` coverage. `limited` reflects P1 source/visual verification limitations; it does not mean that a model call failed. The actual context selections, omitted reading-unit IDs, table/formula/figure status, and dimension limitations are in the published dimension JSON. No dimension is represented as having checked every PDF object.

The final-path numbers exclude exploratory and aborted runs. Local metadata also records **23 additional successful calls** across the same three papers: a failed-after-calls DistilBERT P2-A attempt (11), Shaw P2-B context/budget pilots (8), and fastText P2-B context pilots (4). Those add **497,670 input / 43,634 output tokens** and are real evaluation cost, even though their incomplete outputs were not published as final results. The first DistilBERT attempt ended in `contribution-normalizer: invalid_global_support_source` after its 11 calls; a fresh rerun completed. The first Shaw P2-B pilot hit `budget_exceeded` before D6 with the full P2-A structures repeated in every call. A source-local fact projection reduced input while retaining selected original reading units, coverage limits and omitted-unit inventory; the final Shaw run completed D1–D6 within the configured 180,000 input-token budget. An exploratory Layer Normalization PDF fragmented into 601 reading units and was stopped after two P2-A calls (24,300 input / 2,254 output tokens); it was not used as the holdout. No final-result token accounting treats failed usage as free.

## Manual candidate appraisal

This is a targeted check of the candidate text against the cited Source Bundle units, not independent mathematical, experimental, code, or literature verification. Candidate validity remains `unverified`. The public candidate IDs below can be resolved through each paper's `evidence_mapping.json` to original Evidence IDs, page indices and coordinates.

### Shaw 2018

- The model described relative Key and Value representations and did **not** claim that the paper omitted the existing Table 3 Key/Value ablation. The D3 context included Table 3. The D6 empty result includes an inspection explanation.
- `candidate_633aeca7f9bd479d` asks whether the observed long-sequence behavior is supported beyond the tested structural clipping setting; it cites reading units `ru_ev_886cadb1a708_1_16`, `_2_18`, `_3_6`. This is a scope question, not a claim that the method fails.
- `candidate_4e6dea2fd90f07da` concerns the claimed storage/efficiency bound and the scope of observed implementation measurements. `candidate_ea97b96233a7aa53` asks what supports the stated lack of gain from combining sinusoidal and relative positions. Both cite paper units, but need original table/formula inspection before becoming findings.
- The 11 raw candidates cluster into roughly **six distinct issue themes**. The unseen-length question appears in D1/D2/D3/D4; efficiency and baseline-comparability questions each appear twice. These cross-dimension repetitions are deliberately preserved for P2-C, not silently counted as 11 independent defects.
- Possible false-positive risk: BLEU evaluation details and baseline settings might be recoverable from the cited Tensor2Tensor implementation or supplementary material; visual table and equation transcription is not fully verified. No confirmed author error was established.

### DistilBERT 2019

- The PaperProfile and candidates distinguish pretraining distillation from downstream task distillation and identify distillation cross-entropy, MLM and cosine objectives. Existing component/loss ablations, including Table 4, were considered rather than described as wholly absent.
- D3/D4 preserve the SQuAD difference as a **source conflict needing verification**: the prose has 70.4 EM / 79.8 F1, while Table 2's DistilBERT (D) row has 79.1 EM / 86.9 F1. `candidate_8cc2ac38dbe85ec5` cites units `ru_ev_6724ca5dce54_3_3`, `_2_4`, `_2_8`. It does not diagnose an author mistake or assume the two settings are identical.
- `candidate_b43a63c1120cd273` examines what the existing ablations isolate; `candidate_2349c5cdd44540ee` examines how the three objectives are combined. Their original evidence is mapped in the public output.
- The 13 raw candidates cluster into roughly **eight distinct themes**. Pretraining/task distinction, objective configuration, ablation attribution, cost, and SQuAD figures recur across dimensions. Duplicates and possible weaker variants are visible to the independent reviewer.
- Possible false-positive risk: objective weights or experiment settings may be specified in released code or unparsed portions; tables and equations remain source-limited. The numeric discrepancy is not a confirmed scientific error.

### fastText 2016 holdout

- The model produced specific checks about hierarchical-softmax/Huffman complexity (`candidate_45ae0b8b2e4c35bd`), epoch-versus-total runtime and hardware/thread conditions (`candidate_f4b82c009ae294f8`), and the timing of YFCC100M filtering relative to the data split (`candidate_a399dcbbab67a609`). The split question is explicitly conditional; the output does **not** assert known leakage.
- Other candidates address tag-set/single-label alignment and the scope of the word2vec comparison. One D1 word2vec-positioning candidate sets `external_verification_required=false` while the analogous D6 candidate sets it true. This inconsistent classification is an unresolved quality issue.
- The 12 raw candidates cluster into roughly **six distinct themes**. Efficiency appears in four dimensions, word2vec positioning in three, and YFCC filtering in two. This shows substantial repetition, not stronger evidence.
- Possible false-positive risk: the authors' released scripts may resolve data splitting, tag labels and timing details. No code or external dataset audit was performed.

Across all papers, cited source IDs exist in the corresponding frozen Source Bundle, but **source existence is not scientific correctness**. The spot check found no confirmed invented experiment or unsupported accusation among the cited examples. It was not an exhaustive judgment of every candidate. The main open quality problems are repeated cross-dimension themes, limited visual/formula confidence, conditional questions that may be resolved by external artifacts, and one inconsistent external-verification flag. P2-C and P3 are outside this branch.

## ChatGPT deep-review comparison

**Status: `pending`.** The user has not yet supplied ChatGPT web deep-review reference results, their review instructions, or model conditions. We have not fabricated them, computed a match rate, or claimed parity with ChatGPT. Once references are supplied, compare the same three papers under comparable material and requirements; assess matching or equally valuable issues, missed theory/method/experiment defects, genuinely new supported issues, false positives, repetitions, depth, specificity and original evidence. Resolve disagreements against the PDF and human judgment rather than treating the ChatGPT result as ground truth.

## Remaining risks

1. `limited` input and `partial` coverage leave some formula, table, figure and omitted-reading-unit questions for visual or source-level review. A partial pack cannot establish whole-paper absence.
2. P2-A's invalid global support-reference failure on one real attempt can consume model calls before a run fails; it is recorded here and is not changed by P2-B.
3. Cross-dimension duplicates remain in the raw output by design. They must not be presented to users as separate verified findings or as an inflated quality metric.
4. The local raw diagnostics and public PDFs stay outside Git. Production retention and access-control settings for private model logs and unpublished PDFs remain a deployment gate.
5. The ChatGPT web benchmark and independent academic judgment are pending. These results establish real execution and inspectable candidate evidence, not professional-review equivalence.

## Regression evidence

On the final source tree, `pytest backend/tests -q` with the Shaw and JMLR public-PDF fixtures passed **110 tests, with 2 skips**. `npm run build` passed. The existing Playwright PDF reader, cross-formula and review-mode suite passed **5/5** against the running backend and frontend using the public Shaw PDF. The GitHub workflow `p2b-professional-review.yml` repeats these checks on the pushed branch and PR; its run URL belongs in the PR once GitHub completes it. No frontend source or legacy review prompt was changed.
