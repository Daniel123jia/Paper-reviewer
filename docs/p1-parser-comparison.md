# P1 parser comparison and human reference (2026-11-04)

## Scope and method

The production Evidence layer is unchanged. The offline runner `scripts/compare_parsers.py` compares three parser candidates in isolated processes and emits only counts, landmark observations, time, peak process RSS, versions and coordinate capability. It never emits the paper text or uploads private PDFs. The CI run downloads the two public test PDFs into temporary runner storage, checks their SHA-256 values, and retains only the JSON summary for seven days.

The human reference contains selected visible landmarks, **not** a complete ground-truth transcription or an overall accuracy percentage. I visually checked Shaw PDF pages 2–5 and JMLR pages 2–3 against rendered PDF pages. The synthetic two-page, one-column PDF is generated in memory by the test script. Its purpose is to exercise a different and fully known layout; the real JMLR paper supplies an additional independent one-column layout.

- Shaw et al., 2018: [public arXiv PDF](https://arxiv.org/pdf/1803.02155), SHA-256 `8086e2e5…66225bec260d631a3e8fe7f1f13d50b`, 5 pages, two columns.
- Watson et al., 2024, *KerasCV and KerasNLP*: [JMLR PDF](https://www.jmlr.org/papers/volume25/24-0404/24-0404.pdf), SHA-256 `4ddf0066…6ccc50840ff6c7dea`, 10 pages, one column with a wide multi-level table.

The reference checks heading presence/order, visible Table 3 rows on Shaw, two JMLR table rows, formula number markers and Figure 1 caption. “Token sequence found” means only that text appeared in order; “structured row found” requires a parser table row or a Markdown table row. Neither check proves the math, table semantics or source-to-caption link is correct.

## Local Intel Mac results

Python 3.11, PyMuPDF 1.28.2 and PyMuPDF4LLM 1.28.2. Each parser ran in a fresh child process. Time and peak RSS are single-run observations, not capacity estimates.

| Material | Parser | Headings found / order | Structured reference rows | Formula markers | Time | Peak RSS |
|---|---|---|---:|---:|---:|---:|
| Shaw | PyMuPDF blocks | 7/7, wrong order | 0/4 | 5/5 | 0.86 s | 65.9 MB |
| Shaw | PyMuPDF4LLM | 7/7, wrong order | 4/4 | 4/5 | 2.69 s | 309.7 MB |
| JMLR | PyMuPDF blocks | 9/9, correct order | 2/2 | no reference formulas | 1.05 s | 61.4 MB |
| JMLR | PyMuPDF4LLM | 9/9, correct order | 2/2 | no reference formulas | 3.20 s | 313.5 MB |
| Synthetic single-column | PyMuPDF blocks | 4/4, correct order | 2/2 | 1/1 | 0.03 s | 47.5 MB |
| Synthetic single-column | PyMuPDF4LLM | 4/4, correct order | 2/2 | 1/1 | 1.34 s | 311.4 MB |

PyMuPDF4LLM's page layout boxes do not carry the existing Evidence IDs. Docling's document provenance likewise needs an explicit anchor mapping before either candidate could replace the source used by current PDF highlighting.

Docling 2.146.0 could not be installed in the local Intel Mac comparison environment with binary-only packages: its required `docling-parse>=7.22.2` wheel was unavailable for that platform. The branch's Linux comparison workflow therefore runs Docling separately. That result should be interpreted on the same runner and PDFs, not compared numerically with the Mac times above.

## Linux CI results

The [completed isolated workflow](https://github.com/Daniel123jia/Paper-reviewer/actions/runs/41774864405) ran all three parsers on Ubuntu with the same public PDF hashes. These are one-run observations; the JSON artifact contains the per-parser detail and is retained for seven days.

| Material | Parser | Headings found / order | Structured reference rows | Formula markers | Time | Peak RSS |
|---|---|---|---:|---:|---:|---:|
| Shaw | PyMuPDF blocks | 7/7, wrong order | 0/4 | 5/5 | 0.49 s | 87.4 MB |
| Shaw | PyMuPDF4LLM | 7/7, wrong order | 4/4 | 4/5 | 1.84 s | 395.8 MB |
| Shaw | Docling | 7/7, correct order | 4/4 | 4/5 | 15.85 s | 2399.6 MB |
| JMLR | PyMuPDF blocks | 9/9, correct order | 2/2 | no reference formulas | 0.61 s | 79.9 MB |
| JMLR | PyMuPDF4LLM | 9/9, correct order | 2/2 | no reference formulas | 2.35 s | 395.7 MB |
| JMLR | Docling | 9/9, correct order | 2/2 | no reference formulas | 20.59 s | 2593.6 MB |
| Synthetic single-column | PyMuPDF blocks | 4/4, correct order | 2/2 | 1/1 | 0.02 s | 70.9 MB |
| Synthetic single-column | PyMuPDF4LLM | 4/4, correct order | 2/2 | 1/1 | 0.75 s | 352.9 MB |
| Synthetic single-column | Docling | 4/4, correct order | 2/2 | 1/1 | 61.06 s | 2258.5 MB |

Docling detected two tables in the synthetic document although the reference contains one table and a separate vector figure. This is a false positive candidate, not evidence of extra source content. The sampled landmark checks do not establish completeness for any of the three parsers.

## Shaw source and quality findings

The fixed visible PDF has headings 3.2 and 3.3 on page 3, 4.1 beginning on page 3, 4.2 and 4.3 on page 4, Figure 1 on page 3, equations (1)–(5) on pages 2–3, and Table 3 on page 5 with four visible rows: Yes/Yes 25.8, No/Yes 25.8, Yes/No 25.3, No/No 12.5. This is a selected human reference, not a statement that all academic content was checked.

The existing snapshot contains 99 Evidence blocks. The new Source Bundle returns those IDs unchanged, plus 15 section candidates, five numbered formula candidates, one Figure 1 caption candidate and four table objects under PyMuPDF 1.28.2. The section list puts 2.2 after 3.1 and 4.2 after 4.3. Table 3's data values are visible in text, but PyMuPDF's detected table collapses the four data rows into multiline cells; Table 1 has a caption without detected structure. Another detected “table” on page 3 is actually part of Figure 1/formula-like content. The Figure 1 vector drawing has no raster image box. The References candidate list is incomplete because the legacy block order crosses columns.

The quality endpoint flags reading order on all five Shaw pages, both section-order regressions, the missing Table 1 structure, collapsed rows and the unlocated Figure 1 visual region. It does **not** mark those parts as reliably parsed. The 99 blocks still include text missing from the legacy review's sampled prompt; the new bundle does not change that prompt in P1.

## Decision and limits

No parser is promoted into the production Evidence path in P1. PyMuPDF4LLM improves Table 3 row layout on this paper but does not repair all reading-order errors and loses one equation marker in this run. The Source Bundle remains a conservative, traceable candidate structure. P2 requires a verified structural input policy and an Evidence-ID mapping for any chosen higher-level parser.

P1.1 adds a PyMuPDF geometry-based candidate order and table-row split without promoting a comparison parser. See `docs/p1.1-reading-acceptance.md` for the new reading units, legacy snapshot compatibility and input gate.

The comparison environment has a material maintenance and licensing cost: PyMuPDF4LLM adds layout/ONNX dependencies; Docling adds local layout/table models and substantially higher memory use on the Linux run. [PyMuPDF offers AGPL or commercial licensing](https://pymupdf.readthedocs.io/en/latest/about.html); [Docling code is MIT licensed](https://github.com/docling-project/docling). The product's eventual commercial deployment needs a separate license review, including parser dependencies and model weights.

## Reproduce

On a machine with the comparison dependencies installed:

```bash
python scripts/compare_parsers.py --shaw /private/path/shaw2018.pdf --jmlr /private/path/jmlr2024.pdf > /private/path/p1-summary.json
```

The SHA-256 checks reject a different edition of either paper. The local PDFs, generated PDF and full parser outputs are not committed. CI workflow and a metadata-only JSON summary provide a repeatable Linux comparison.
