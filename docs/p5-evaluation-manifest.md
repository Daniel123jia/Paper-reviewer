# P5 evaluation manifest

Manifest schema: `1.0`  
Test-set freeze date: 2026-11-06 (Asia/Shanghai)  
P1–P4 production baseline: `c4b613f486b4db6f118dc0ef99b7c0c103676843`  
P5 protocol commit: `a59c178`  
Requested model: `gpt-6.1-sol`  
ChatGPT webpage benchmark: `pending`

The two holdouts were selected and their reference sets were frozen before any EtPaper output for either PDF was created or viewed. Source PDFs are stored only in ignored local evaluation storage. The hashes below identify the exact bytes used.

## Frozen papers

| ID | Group | Paper and version | Official PDF | SHA-256 | Material profile | Reference |
|---|---|---|---|---|---|---|
| `shaw2018` | Historical regression | Shaw et al., *Self-Attention with Relative Position Representations*, arXiv `1803.02155` / NAACL 2018 | `https://arxiv.org/pdf/1803.02155` | `8086e2e5e139fd576631ce0ec4560c5ee66225bec260d631a3e8fe7f1f13d50b` | 2-column; formulas; three key tables and figure | Existing P1–P4 audited examples; not blind |
| `distilbert2019` | Historical regression | Sanh et al., *DistilBERT, a distilled version of BERT*, arXiv `1910.01108` | `https://arxiv.org/pdf/1910.01108` | `a70c78e2b4db85bfee7696e19829571a3b05f69c616db5c039f6f4eb33b1a0b6` | Short 2-column paper; formulas; result and ablation tables | Existing P1–P4 audited examples; not blind |
| `fasttext2016` | Historical regression | Joulin et al., *Bag of Tricks for Efficient Text Classification*, arXiv `1607.01759` | `https://arxiv.org/pdf/1607.01759` | `6cbcf620c2537ed54dfb9ed0d843a83689b82dc41dcd305bfb2e82425d4d7906` | Short NLP paper; equation; efficiency/result tables | Existing P1–P4 audited examples; not blind |
| `sardet100k-neurips2024` | Holdout H1 | Li et al., *SARDet-100K: Towards Open-Source Benchmark and ToolKit for Large-Scale SAR Object Detection*, NeurIPS 2024 final proceedings PDF, DOI `10.52202/079017-4079` | `https://proceedings.neurips.cc/paper_files/paper/2024/file/e7eb8128eb26eafbe901348df1dbacdc-Paper-Conference.pdf` | `3f4e6cddbf32524853f3493a5e7975490b8ffeae274cf139acf2b576736998b9` | 32 pages including appendix/checklist; SAR detection; equations; many tables/figures; mixed/overlaid layout in some pages; public code | `docs/examples/p5-holdout-1/blind-reference.json` |
| `sam-iclr2021` | Holdout H2 | Foret et al., *Sharpness-Aware Minimization for Efficiently Improving Generalization*, arXiv `2010.01412v3`, ICLR 2021 | `https://arxiv.org/pdf/2010.01412` | `73ace22a61c52152fc84a94239cb3ea5f9fa9e753aa8ef5995f277365e25b764` | 20 pages; optimization/theory; equations and theorem; multi-dataset experiments; appendices and ablations; public code | `docs/examples/p5-holdout-2/blind-reference.json` |

## Holdout selection rationale

H1 moves outside the development set’s NLP focus into multi-source SAR object detection. It stresses source provenance, dataset composition, sensor/domain heterogeneity, dense tables, figures, appendices, and an unusually difficult PDF text layer. It was selected because these properties create real review questions, not because success was expected.

H2 is an optimization and generalization paper rather than a dataset/detection or language-model paper. Its central claims require following a PAC-Bayesian motivation, approximations to a min–max objective, hardware-dependent batching behavior, and experiments across several regimes. This gives a materially different scientific and document structure from H1 and the historical group.

Neither holdout participated in P1–P4 prompt/rule changes. After this manifest is committed, a parsing or model failure does not justify replacing either paper.

## Versions, appendices, and public materials

- H1 uses the official NeurIPS proceedings PDF, not the earlier arXiv/ECCV-submission PDF. The exact official file includes the appendix and NeurIPS checklist. The official code/dataset repository named by the paper is `https://github.com/zcablii/SARDet_100K`.
- H2 uses arXiv v3, whose response identifies the file as `2010.01412v3.pdf`; it includes appendices A–C. The paper’s official implementation is `https://github.com/google-research/sam`.
- External code, reviews, and later papers may be used only in an explicitly labelled external-verification step. They are not silently treated as PDF evidence.

## Frozen budget

Every paper uses the P5 protocol ceiling: 90 calls, 1,200,000 known input tokens, 220,000 known output tokens, 10,800 seconds wall time, and two explicit retries. Cost remains `unknown` until a reliable price source is configured.

## Reference-set boundary

The holdout references were produced by Codex from the frozen PDFs without viewing EtPaper output. They are non-expert, source-grounded challenge sets, not complete defect inventories. Therefore P5 may report coverage of these enumerated issues, but it may not call the result whole-paper defect recall or expert-level quality.
