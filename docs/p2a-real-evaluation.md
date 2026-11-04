# P2-A real-model evaluation

This is an engineering and manual academic check of N1–N3. It does not certify scientific-review quality. All runs use public PDFs and the existing model interface. The three prompt templates contain no paper-specific expected answers. DistilBERT was held out from prompt adjustment.

## Reproducibility and privacy

| Paper | Public source | PDF SHA-256 | Role |
| --- | --- | --- | --- |
| Shaw et al., *Self-Attention with Relative Position Representations* (2018) | [arXiv PDF](https://arxiv.org/pdf/1803.02155) | `8086e2e5e139fd576631ce0ec4560c5ee66225bec260d631a3e8fe7f1f13d50b` | Fixed acceptance paper |
| Sanh et al., *DistilBERT, a distilled version of BERT* (2019) | [arXiv PDF](https://arxiv.org/pdf/1910.01108) | `a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6` | Held-out public paper |

The exact actual request and raw response for each model call are retained only under the gitignored local `data/p2a-evaluation/runs/` directory (directory mode 0700, file mode 0600). The evaluation runner redacts the configured API key and Bearer credentials before writing. The local PDFs, SQLite data, request/response diagnostics, and credential file are excluded from the repository. The committed [ReviewPlan](examples/p2a-shaw2018/review_plan.json), [PaperProfile](examples/p2a-shaw2018/paper_profile.json), and [ClaimEvidenceMap](examples/p2a-shaw2018/claim_evidence_map.json) are structured public-paper examples; all their source IDs were resolved against the actual local Source Bundle snapshot. The service persists call metadata, not raw requests and responses. Automatic production retention and access control beyond the existing authenticated API require deployment policy before use with private papers.

## Shaw 2018: measured run

The end-to-end run `2fdc6dd37e054b9d84a848246e9986a0` completed with requested and returned model `gpt-6.1-sol`. N1 read 34 of 167 ordered units plus the full section inventory for planning; N2 and N3 each used 167 of 167 units. All three results have `execution_status=limited` because P1 reports `needs_review`; full unit coverage is not proof of correct reading order or transcription.

| Measure | Actual result |
| --- | ---: |
| Model call attempts | 20 |
| Completed calls | 15 |
| Failed calls | 5, connection failures retried or handled by bounded splitting |
| Input tokens reported by completed calls | 131,279 |
| Output tokens reported by completed calls | 26,529 |
| Total call elapsed time, including failed attempts | 935,170 ms (15 min 35 s) |
| Failed-call token usage | Unknown; the provider returned no usage |

The profile contains 12 method components, 9 experimental-setup facts, 10 main-result facts, and 5 author-claimed contribution facts. The map contains 15 contribution objects, 47 claims, 85 support relations and 41 gaps. These are observed outputs, not target counts. Several contribution objects restate the same core contribution and some claims are overfragmented; human consolidation is still needed before downstream issue generation.

### Manual academic check against the public PDF

| Check | Observation | Acceptance status |
| --- | --- | --- |
| Paper type | N1 identifies a neural architecture/method paper evaluated on machine translation. | Pass |
| Research problem | Profile identifies relative position information inside self-attention, compared with input-level absolute encoding. | Pass |
| Key and Value roles | Map states value-side relations enter attention-weighted output, and key-side relations enter compatibility. Source IDs lead to page 2 formulas and surrounding text. | Pass for conceptual reading; formula transcription remains unverified |
| Complexity and implementation | Profile includes clipped relative distances, shared representations, a quadratic pairwise representation issue, and reshaped matrix multiplication to avoid broadcasting. | Pass for high-level understanding; no independent implementation validation |
| Translation setup | Profile distinguishes WMT 2014 English–German and English–French, base/big Transformer configurations, BLEU comparison, and training-speed observation. | Pass for stated setup; extracted numbers remain unverified in P1 |
| Tables 1, 2 and 3 | Table 1 is treated as translation comparison, Table 2 as clipping-distance variation, and Table 3 as key/value ablation. | Pass for comparison scope; table structures not fully verified by Source Bundle |
| Table 3 ablation | The result list reports both sides, key only, value only, and neither. Manual PDF reading confirms the displayed BLEU sequence 25.8, 25.8, 25.3, 12.5. | Pass for recognizing an existing ablation; runtime table status remains unverified |
| Contribution evidence | Main contribution claims have source unit IDs that resolve to original Evidence IDs and the same PDF snapshot. | Pass for traceability; duplicate contribution objects require consolidation |
| Unverified materials | Profile gaps name the incomplete Table 1, unverified Tables 2/3 and equations (1)–(5), and Figure 1 visual review. | Pass |
| Invented contributions or experiments | Spot-check found no clear fabricated research contribution or experimental setting. Some broad statements are model interpretation and several contribution objects repeat source claims. | Partial; not an exhaustive factual audit |

The P1 parser's Table 1 structure is incomplete. The other detected tables and formulas are candidate transcriptions without independent validation. Figure 1 requires visual review. Some reading units have ambiguous order or source mapping. The model therefore cannot certify numerical or mathematical conclusions from these objects, even when its narrative happens to agree with a human glance at the PDF. P2-A preserves these limitations in the output instead of treating missing or uncertain parsed content as proof of an omission in the paper.

## Held-out DistilBERT 2019: measured run and spot-check

The completed end-to-end run `a32350b251db48188dae021284873667` used requested and returned model `gpt-6.1-sol`. N1 read 24 of 107 units plus the full section inventory; N2 and N3 each used 107 of 107 units. All three outputs are `limited` because the input Source Bundle needs review. The run made 16 call attempts: 12 completed and four connection failures. Completed calls reported 99,587 input tokens and 27,406 output tokens; failed calls did not return usage. Total call time, including failures, was 1,110,419 ms (18 min 30 s). A preceding attempt failed in N2 after four calls (two completed, two connection failures; reported usage 16,897 input and 3,984 output tokens; 242,602 ms). The failed attempt remains in private diagnostics; it is not represented as a completed paper result.

N1 correctly identifies model compression through pre-training distillation. N2 describes a six-layer student initialized from alternating BERT teacher layers and the three-part distillation, masked-language-modeling, and cosine objectives. Its profile names the Wikipedia and Toronto Book Corpus training data, GLUE, IMDb, SQuAD 1.1, and the reported speed/size comparisons. Source IDs resolve to the same PDF snapshot. N3 produced 25 contribution objects and 62 claims, with similar duplication and overfragmentation to Shaw. The model states that table-derived numbers are unverified; a manual PDF spot-check found the key method statements and author-reported headline numbers in the source.

One discrepancy deserves explicit follow-up: the paper's page-3 Table 2 gives the two-step distilled SQuAD result as 79.1 EM / 86.9 F1, while page-4 prose gives 70.4 EM / 79.8 F1. The profile reports the prose numbers with a source reference but does not surface this table/prose conflict. This is a known limitation of P2-A understanding, compounded by P1's unverified table structure. It is a manual observation here, not a generated review finding or a reason to silently alter the model output. The PDF's Table 3 caption also calls STS-B “sentiment analysis”; the profile attributes that wording to the caption rather than presenting it as an independently verified task definition.

## Remaining risks for ChatGPT review

1. N3 produces duplicate or overly granular contribution/claim objects; P2-B should not treat raw object count as scientific quality.
2. N2 did not flag the held-out paper's Table 2/prose SQuAD discrepancy. Downstream evidence verification must compare structured tables with nearby narrative, after the table structure is checked against the PDF.
3. P1 has no verified global reading reference for these PDFs, so `limited` is the correct execution status even with every reading unit included.
4. A relay connection failure can cause retry, bounded input splitting, or a failed run. Usage from unsuccessful calls may be unavailable, so reported tokens are a lower bound.
5. The local diagnostic directory has restricted permissions, but retention expiry is operational rather than automated. Do not expose private-paper diagnostics without an access and deletion policy.
