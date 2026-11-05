# P5 product usability and export acceptance

Status: **professional-review web workflow fails end-to-end closure; P4 subflow is usable with limits**  
Execution date: 2026-11-06  
Fresh API chain: P1 completed, P2-A blocked before model execution

## API and website are different chains

The backend exposes the new P1 → P2-A → P2-B → P2-C → P3 → P4 API sequence. The P5 runner successfully exercised new P1 uploads for both holdouts and received the expected credential blocker at P2-A. It did not misreport either chain as completed.

The website does not expose that sequence. The `审稿分析` tab still creates the legacy `review-case` workflow and explicitly describes sampling at most 30 passages. It cannot create or display P2-A understanding, six P2-B dimensions, P2-C consolidation, or P3 verification. The P4 page can only begin after a P3 run already exists, which currently requires an API or developer script.

| User action | Website | API | P5 result |
|---|---|---|---|
| Upload and read PDF | Yes | Yes | Passed; PDF canvas, search, Evidence jump and highlight exist. |
| Start P2-A professional understanding | No | Yes | Product gap. |
| View P2-A/P2-B/P2-C/P3 progress and outputs | No | Yes | Product gap. |
| Start P4 from an existing P3 run | Yes | Yes | Passed on deterministic fixture. |
| View P3 status, source refs and outstanding checks in P4 | Yes | Yes | Passed on fixture. |
| Record table/formula visual review | Partial | Yes | P4 page provides a structured visual-review form and PDF jump. |
| Record external literature/code/data verification | No | Yes | Product gap; reviewer approval cannot substitute for the API record. |
| Add/exclude/approve a reviewer-authored issue | Yes | Yes | Covered by existing API tests; P4 page has controls. |
| Edit, version, reopen and approve a draft | Yes | Yes | Passed in browser and API regression. |
| Export Markdown/DOCX/PDF | Yes | Yes | Passed functionally; one PDF layout defect found below. |

Product Usability is therefore **FAIL for the complete professional-review journey**. This does not negate the usable P4 review-and-document subflow.

## Human review and approval behavior

The P4 page clearly presents P3 verdict, report eligibility, human-review state, processing state, report decision, Source Refs, counterevidence, and outstanding checks. Evidence buttons load the corresponding PDF page and highlight the original Evidence box. Visual checks record rows/columns, values/labels, metric order, unresolved configurations, and reviewer notes.

The UI text correctly distinguishes accepting an issue from independently confirming its science. Final draft approval requires an explicit checkbox for the exact revision. A source issue must be separately approved before the draft can be approved. Deterministic API tests also show that a critical external factual dependency cannot be approved without a separate external-evidence record.

However, the P4.1 external-evidence record has no equivalent frontend form. A normal user cannot complete critical literature, code, or data verification without calling the API. This is an explicit workflow break, not a documentation problem.

## Actual edit, approval and export check

The P5 product script created a synthetic source-grounded P4 project, generated an English author draft, edited and saved revision 2, attempted final export before approval, approved the source issue and exact draft revision, exported all formats, edited the approved body, and attempted final export again.

Results:

- Final export before approval: HTTP 409.
- Approved revision: 2.
- Markdown: 423 bytes; no Draft marker in the approved export.
- DOCX: 23,796 bytes; editable paragraphs; the Quick Look rendering was visually legible.
- PDF: 90,321 bytes; one A4 page (595.92 × 842.88 points), correct title, page number, no clipping and no internal `ru_ev_` identifier.
- Editing after approval changed the state to `pending`; stale final export returned HTTP 409.
- The browser test opened the saved draft, jumped to original PDF evidence, edited, saved, refreshed, reopened revision 2, and downloaded Draft-marked Markdown and PDF: 1/1 passed.

The actual files and rendered pages are in ignored local P5 storage. The sanitized measurements are in [product-check-summary.json](examples/p5-regression/product-check-summary.json).

## DOCX/PDF visual and structural inspection

An additional Chinese internal draft contained Chinese headings/body, an inline mathematical display, and a Markdown table. The DOCX retained an editable table and Office math object. PDF text extraction and page rendering retained Chinese, the equation, table labels/value, page numbers, and a visible Draft banner.

One real formatting defect was found: the small table was moved by itself to PDF page 2, leaving most of that page blank. The table was not clipped or corrupted, but the pagination is not publication-quality. This check therefore passes content preservation and fails the “no unreasonable blank page” criterion for that fixture. P5 records the defect; it does not change the frozen P4 formatter.

Pandoc is another deployment dependency. This workstation has no global `pandoc` command; P5 used the virtual environment's bundled Pandoc 3.9 by exposing its directory on PATH. Without a configured Pandoc executable, production DOCX/PDF export returns a controlled 500 while the saved draft remains intact. CI installs system Pandoc. Deployment documentation must make this dependency explicit.

## Deterministic reliability coverage

| Scenario | Evidence | Result |
|---|---|---|
| Invalid/missing PDF and parse limitations | P1 API/parser tests and both holdout P1 reports | Passed state/limitation handling. |
| Model timeout/connection failure | P2–P4 injected client failures | Error class recorded; unknown Tokens remain unknown. |
| Invalid model JSON | P2-A/P2-B/P2-C/P3 contract tests | Rejected or conservatively degraded. |
| Budget exceeded | Stage budget tests and P5 aggregate runner | Partial work preserved; next stage not started. |
| Duplicate run/project creation | API conflict tests | HTTP 409 rather than silent overwrite. |
| Service restart and SQLite readback | P2/P3/P4 persistence tests | Saved results remain readable. |
| Cross-document Source Ref | P2/P3/P5 lineage validation | Rejected/blocking. |
| Missing external material | P3/P4 eligibility tests | Cannot become verified through approval alone. |
| Unapproved or stale-approved final export | P4 API and P5 product script | HTTP 409. |
| Export conversion failure | P4 injected exporter failure | HTTP 500; saved draft preserved. |
| Browser refresh | P4 Playwright regression | Saved revision reopened. |
| Browser-side download interruption | No deterministic browser injection exists | Not tested. |

The full fresh-model failure/recovery path remains untested because no model call could start. The browser cannot yet recover or resume a P2-A–P3 run because it has no UI for those stages.

## Product blockers and non-blocking defects

Blockers:

1. No website path exists for P2-A through P3, so a normal user cannot complete professional review from upload to report.
2. External evidence verification is API-only.
3. The required fresh model chain has not executed in this environment.

Non-blocking but material defects:

1. A small PDF table can be orphaned onto a mostly empty second page.
2. Pandoc must be installed or explicitly placed on PATH; the application does not discover the bundled `pypandoc` executable by itself.
3. Browser download interruption is not covered by deterministic E2E injection.
