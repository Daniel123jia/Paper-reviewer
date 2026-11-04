# P1 Source Bundle v1.0

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
| `formulas[]` | Numbered formula candidates, raw extracted text, original Evidence ID, page and box. Formula transcription and context remain unverified. |
| `tables[]` | PyMuPDF table candidates with cell matrix, shape, box and intersecting Evidence IDs. Captions are linked by page/position as candidates. A caption with no detected table is retained as `missing_structure`. |
| `figures[]` | Caption candidates and Evidence ID. Raster image boxes are linked by proximity when available. Vector diagrams and visual meaning are not parsed. |
| `references[]` | Candidate text blocks following a detected References heading. Completeness is unverified, especially in multi-column layouts. |
| `limitations[]` | Explicit conditions that block treating this bundle as verified full-text input. |

`body[]` is complete only relative to the existing text-block extraction, not to the visible PDF. A `candidate` object is a detected possibility, not a verified academic structure. `missing_structure`, `needs_ocr`, `transcription_unverified`, and `visual_unreviewed` must not be treated as successful review coverage.

## Parse quality report

The report names the source bundle and records per-page, per-section, per-table, per-formula and per-figure states. It flags observable problems such as `reading_order_unverified`, `section_order_unverified`, `section_body_boundaries_unverified`, `table_structure_missing`, `row_alignment_unverified`, `text_missing_or_image_only`, `expression_and_context_unverified`, and `visual_region_unlocated`.

`accuracy.status` is always `not_measured` unless a separately reviewed reference is supplied in a future version. `inventory_completeness` is `unverified`: an undetected section, formula, table or figure cannot be inferred absent from this report. The human landmark reference in `tests/fixtures/p1_parse_reference.json` is used only by the offline comparison script, not to make claims about arbitrary uploads.

## P2 dependency

P2 must consume a verified reading order and section/body map, or explicitly carry these P1 limitations into its review scope. The current bundle gives all nodes the same traceable source contract, but does not authorize describing its text as a reliable complete reading of the paper.
