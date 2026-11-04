# P1 / P1.1 Source Bundle v1.0

P1 adds a read-only, versioned view of each uploaded PDF's existing Evidence snapshot. It does not change upload parsing, Evidence IDs, review sampling, prompts, model calls, or exports. Both new endpoints use the existing `/api/` access middleware:

- `GET /api/documents/{document_id}/source-bundle`
- `GET /api/documents/{document_id}/parse-quality`

No database migration or new runtime dependency is required. Both views are built on demand from the private PDF and SQLite rows. Full paper text remains in the existing private data directory and API; neither response is written into the public repository. As with the existing Evidence API, a deployment must configure the access token and HTTPS before exposing these endpoints outside localhost.

## Source Bundle shape

| Field | Meaning and provenance |
|---|---|
| `schema_version`, `bundle_id` | `1.0` contract; ID derives from document ID and the original Evidence `snapshot_id`. |
| `document` | Existing ID, filename, SHA-256, snapshot ID, page count, and nonempty PDF metadata. |
| `parser` | Name and version of the PyMuPDF parser used for this bundle. |
| `pages[]` | Zero-based page, size, rotation, existing Evidence IDs, and text/OCR state. |
| `sections[]` | Existing TOC or heading candidates with page, level, and Evidence ID when present. Section body boundaries remain unverified. |
| `body[]` | Every original Evidence text block, original ID, excerpt, page, bounding box, and precision. Array order is the legacy extraction order; it is **not** certified reading order. |
| `reading_units[]` | Candidate model reading sequence. Each unit has a stable `ru_` ID, text, zero-based page, PDF box, `section_id`, zero-based `section_order`, one or more original Evidence IDs, `reading_order_status`, `evidence_mapping_status`, and parser name/version. Table data can yield several units from the same Evidence block. |
| `formulas[]` | Numbered formula candidates, raw extracted text, original Evidence ID, page and box. Formula transcription and context remain unverified. |
| `tables[]` | PyMuPDF table candidates with raw `cells`, candidate `structured_rows`, shape, box and intersecting Evidence IDs. Captions are linked by page/position as candidates. A caption with no detected table is retained as `missing_structure`. `structure_status` and `usability_status` expose split rows, incomplete structure and possible false positives. |
| `figures[]` | Caption candidates and Evidence ID. Raster image boxes are linked by proximity when available. Vector diagrams and visual meaning are not parsed. |
| `references[]` | Candidate text blocks following a detected References heading. Completeness is unverified, especially in multi-column layouts. |
| `limitations[]` | Explicit conditions that block treating this bundle as verified full-text input. |

`body[]` is complete only relative to the existing text-block extraction, not to the visible PDF. A `candidate` object is a detected possibility, not a verified academic structure. `missing_structure`, `needs_ocr`, `transcription_unverified`, and `visual_unreviewed` must not be treated as successful review coverage.

P1.1 chooses **PyMuPDF geometric column ordering** for the candidate text stream, retaining the existing PyMuPDF Evidence layer. Wide blocks divide page bands; within a two-column band, left blocks precede right blocks. Narrow blocks crossing the gutter make that page `unverified`. The rule uses PDF geometry, not a title or discipline-specific string. Sections from the TOC match a block only on the same page with an exact canonical heading; duplicate or unmatched headings stay unverified. `sections[].reading_unit_ids` records the mapped units and `reading_order` records the reconstructed heading position. The original `body[]` order remains intact.

Normal units point to one exact original Evidence block and keep its box (`exact_block`). A reconstructed table row can point to multiple intersecting Evidence blocks, and one block can source multiple rows. Its mapping is `source_block_partial` with the table's PDF region; a consumer must not treat that region as an exact row highlight. No fuzzy paragraph-to-Evidence matching is used. Table columns with equal nonempty line counts can be split into candidate rows; otherwise `structure_incomplete` remains. A single-row uncaptioned “table” is flagged `possible_false_positive`. PyMuPDF4LLM and Docling stay in the offline comparison because their output currently has no validated mapping to the legacy Evidence IDs and has higher resource cost.

Object `usability_status` is `detected_unverified`, `structure_incomplete`, or `visual_review_required` in the unreviewed API response. `ready` requires an explicit human PDF reference in `assess_input_readiness`; a parser never grants that status on its own. A missing vector figure box is `visual_review_required`, and an isolated equation number without an expression is `structure_incomplete`.

## Parse quality report

The report names the source bundle and records per-page, per-section, per-table, per-formula and per-figure states. It flags observable problems such as `reading_order_unverified`, `section_order_unverified`, `section_body_boundaries_unverified`, `table_structure_missing`, `row_alignment_unverified`, `text_missing_or_image_only`, `expression_and_context_unverified`, and `visual_region_unlocated`.

`accuracy.status` is always `not_measured` unless a separately reviewed reference is supplied in a future version. `inventory_completeness` is `unverified`: an undetected section, formula, table or figure cannot be inferred absent from this report. The human landmark reference in `tests/fixtures/p1_parse_reference.json` is used only by the offline comparison script, not to make claims about arbitrary uploads.

`input_readiness` distinguishes `section_summary`, `formula_check`, `table_review`, and `figure_review`. `blocked` means the required text/object is missing or unreadable, or a required technical object lacks structure or human object/inventory reference. `needs_review` means the text exists but reading order, source mapping, section assignment, page inventory or human reading reference has an unresolved condition. `ready` requires a separately supplied `human_pdf_review` reference with a `reference_id`, verified unit IDs, and (for a section summary) verified pages or (for an object task) verified object IDs. Whole-scope object checks also require an inventory attestation. `scope` may select pages, sections or object IDs. The production API supplies no human reference, so it cannot certify a paper as ready by itself.

## P2 dependency

P2 must consume a verified reading order and section/body map, or explicitly carry these P1 limitations into its review scope. The current bundle gives all nodes the same traceable source contract, but does not authorize describing its text as a reliable complete reading of the paper.
