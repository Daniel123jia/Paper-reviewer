# P2-B Stage Requirements Isolation Design

## Goal

Prevent P2-A execution constraints from entering P2-B prompts while preserving
auditable, optional P2-B-specific review requirements.

## Root cause and scope

`run_professional_review()` currently passes the parent P2-A row's
`special_requirements` to `run_p2b()`. The Shaw run stored the P2-A-only
instruction “仅完成N1-N3论文理解与证据映射，不生成审稿问题。”, so several P2-B
dimensions obeyed the wrong stage constraint and returned no candidates.

The fix is limited to the P2-B request contract, the model input envelope,
SQLite run record, and API tests. It does not change P2-A outputs, P2-B system
prompt templates, result schemas, scientific rules, retry behavior, or
historical rows.

## Contract and data flow

- Add `ProfessionalReviewRunRequest` with optional `review_requirements`, at
  most 2,000 characters.
- Add nullable `review_requirements` to `professional_review_runs`; existing
  databases receive the column through the normal startup migration.
- `POST /api/understanding-runs/{run_id}/professional-review-runs` accepts an
  optional body. The server persists only its P2-B-specific value.
- `run_professional_review()` passes the persisted P2-B value to `run_p2b()`.
  It continues to pass the P2-A `target_journal` as paper context, but never
  passes the P2-A `special_requirements`.
- The P2-B model input names the field `review_requirements`; the old
  `special_requirements` key is removed from P2-B inputs so the stage boundary
  is explicit. Versioned system prompt files and output schemas are unchanged.
- A missing request body or null field produces the existing default six-
  dimension review behavior.
- `GET /api/professional-review-runs/{run_id}` exposes the persisted nullable
  field through the existing run row response, providing source auditability.

## Compatibility and failure behavior

Existing P2-B rows read with `review_requirements = NULL`. Existing P2-A rows
and the historical partial-failed Shaw P2-B run remain unchanged. P2-C keeps
accepting only P2-B states `completed` or `limited` with a complete result.
Unknown usage continues to stop the whole P2-B run immediately.

The frozen Shaw ReviewPlan contains a natural-language P2-A exclusion for
review findings. Current applicability logic only excludes an exact dimension
ID or name, and the projected P2-B plan does not transmit
`excluded_dimensions`; it did not skip D1-D6. The frozen plan is therefore
reported as a historical stage-scoping inconsistency but is not rewritten.

## Budget recommendation for the next authorized run

The failed Shaw run used 161,174 known input tokens for D1-D5. Reconstructing
the corrected D6 request gives a local UTF-8/character estimate of 17,817
tokens. For D1-D5, supplier usage was consistently 1.825–1.840 times the local
estimate, implying roughly 32.5k–32.8k supplier input tokens for D6 and about
193.7k–194.0k for a normal six-call run.

Recommended approval envelope:

- `P2B_MAX_CALLS=8` (six normal calls plus at most two schema-repair calls)
- `P2B_MAX_INPUT_TOKENS=240000`
- `P2B_MAX_OUTPUT_TOKENS=50000`
- `P2B_MAX_DURATION_MS=1800000`

This is a local stop policy, not a supplier account hard limit. The pre-request
character estimator is not an exact tokenizer and can under-estimate supplier
usage. Unknown usage remains unknown and may still be billed.
