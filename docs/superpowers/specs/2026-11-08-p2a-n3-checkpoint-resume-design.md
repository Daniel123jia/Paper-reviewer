# P2-A N3 Checkpoint Resume Design

## Goal

Allow an operator-authorized P2-A continuation to reuse a validated ReviewPlan and PaperProfile from a failed parent run, persist each newly validated N3 partition, and later resume only missing N3 work. Historical failed runs remain immutable. A continuation becomes `completed` only after every N3 partition, contribution normalization, final ClaimEvidenceMap validation, and the existing P2-B snapshot validation succeed.

## Scope

This change is limited to P2-A N3 checkpointing, continuation lineage, its API, database migration, and deterministic tests. It does not introduce a general scheduler, alter any prompt, change scientific validation, modify frozen evaluation assets, recover partial SSE responses, or call a real supplier.

The two existing Shaw runs remain unchanged. In particular, run `74bbc32a016342f59cf4cbeea89d9124` has three successful N3 call traces but no persisted structured partition outputs. Those traces are not checkpoints, so the first continuation must execute all five N3 partitions and the final contribution-normalization call.

## Verified failure evidence

- Run `e5ec7f7b82ea46e0825faaf646d220ce` failed during the first attempt for the third N2 `paper-understanding` partition after 71.176 seconds. Old retry behavior then continued the run.
- Run `74bbc32a016342f59cf4cbeea89d9124` failed during N3 partition 4/5, covering `ru_table_p3_0_row_0` through `ru_ev_636c4e081dbd_4_19`, after 61.188 seconds.
- Both errors occurred before the 180-second SDK timeout. Existing records cannot distinguish connection setup failure from an interrupted SSE stream, do not contain a supplier request ID, and do not prove supplier fault, output overflow, or proxy timeout.

## Data model

### `paper_understanding_run_lineage`

One row exists for each N3 continuation:

- `run_id`: the new paper-understanding run.
- `parent_run_id`: the failed run explicitly selected by the operator.
- `plan_source_run_id` and `profile_source_run_id`: the runs whose validated N1 and N2 results were inherited.
- `mode`: fixed to `n3_resume`.
- `document_id`, `source_bundle_id`, `snapshot_id`, and `pdf_sha256`.
- `plan_prompt_version`, `profile_prompt_version`, and `schema_version`.
- `created_at`.

This table records provenance independently from model-call rows. Inherited N1/N2 results create no supplier-call record and consume no new usage.

### `paper_understanding_n3_checkpoints`

Each row is one validated contribution-mapping partition associated with the run that generated it:

- Stable checkpoint ID and generating `run_id`.
- `partition_id`, zero-based index, partition count, and ordered Reading Unit IDs.
- Source Bundle, document snapshot, PDF SHA-256, prompt version, schema version, and exact input fingerprint.
- Validated structured `result_json`, its SHA-256, and `validation_status=validated`.
- Optional `origin_checkpoint_id` and `origin_run_id` when a continuation reuses a checkpoint.
- `generation_mode`: `generated` or `inherited`.
- Creation timestamp.

The primary uniqueness boundary is `(run_id, partition_id)`. A continuation transaction copies each reusable checkpoint into the new run with a new checkpoint ID and explicit origin fields. No model-call row is created for an inherited checkpoint.

## Stable partition and input identity

N3 partitioning continues to use the existing ordered Source Bundle reading units and the existing 9,000-character chunk limit. A partition descriptor is derived from:

- ordered Reading Unit IDs and their exact text;
- the Source Bundle ID, document ID, snapshot ID, and PDF SHA-256;
- `contribution-evidence-mapper/v2`;
- P2-A schema version;
- inherited PaperProfile content excluding `review_run_id`;
- target journal and special requirements;
- the exact model system and user messages, with the run-specific ID excluded from fingerprint material.

The descriptor produces a stable `partition_id` and `input_fingerprint`. Legitimate continuations therefore match even though their run IDs differ. Any source text, ordering, profile content, prompt version, schema, journal requirement, snapshot, or PDF change causes a mismatch and prevents reuse before a supplier request.

## Inherited result handling

The continuation endpoint accepts only a failed parent with:

- a frozen Source Bundle;
- a validated ReviewPlan and PaperProfile;
- no completed ClaimEvidenceMap;
- an exact match to the current document ID, snapshot ID, and PDF SHA-256;
- supported P2-A schemas and the current N1/N2 prompt versions.

ReviewPlan and PaperProfile are copied into the new run with `review_run_id` rebound to the new run ID so the final snapshot can satisfy the existing P2-B validator. The lineage table is the audit proof that these results were inherited rather than regenerated. Their original model identifiers, prompt versions, evidence references, and content remain unchanged.

## Execution flow

1. `POST /api/understanding-runs/{parent_run_id}/n3-resume-runs` performs model configuration preflight and opens an immediate database transaction.
2. It validates the parent, current document, frozen bundle, inherited results, prompt/schema versions, and all ancestor checkpoints.
3. It rejects a concurrent active P2-A run for the document. A partial unique index provides a database-level second guard.
4. It creates one queued run, its lineage row, and inherited checkpoint rows in the same transaction.
5. The background task revalidates all inherited state before model-session creation.
6. It computes the current five N3 descriptors and loads only exact, validated checkpoint matches.
7. For every missing partition, it makes one budgeted supplier request. After Schema, Source Bundle, Evidence, partition coverage, and fingerprint checks pass, it commits the checkpoint before starting another request.
8. Unknown Usage or connection/timeout failure persists one redacted call trace, marks the continuation failed, preserves checkpoints, and stops. It never retries, continues, or creates another continuation.
9. Once all partitions are available, the existing contribution normalization and merge execute under the same budget. The final ClaimEvidenceMap is validated and persisted.
10. The run becomes `completed` only after `validate_understanding_snapshot` accepts the new run ID and full snapshot. Otherwise it remains failed.

## Recovery and immutability

Application startup continues to mark queued/running P2-A runs failed as `interrupted_by_restart`; it does not replay them. Validated checkpoints already committed remain available to a later operator-authorized continuation. Unvalidated or partially written rows are never reusable. Historical run status, call rows, ReviewPlan, PaperProfile, and missing ClaimEvidenceMap are not updated.

Corrupt checkpoints, invalid Evidence references, hash mismatches, unsupported versions, or ambiguous lineage fail closed before any supplier request. They are not silently ignored or regenerated.

## API responses and audit

`GET /api/understanding-runs/{run_id}` adds:

- `lineage`, or `null` for historical/full runs;
- `n3_checkpoints`, containing IDs, partition ranges, hashes, validation state, generation mode, and origins, but not raw prompts;
- counts for inherited, generated, and missing partitions.

The API never exposes credentials, full model input, raw supplier exceptions, or private response bodies. Supplier call usage remains solely in `paper_understanding_model_calls`; inherited checkpoints do not affect token or cost totals.

## Testing

All tests use Fake Model clients and isolated SQLite databases. Required coverage includes:

- three N3 checkpoints persisted before a fourth-partition connection failure;
- an operator-authorized continuation calls only missing partitions and normalization;
- no N1/N2 supplier calls in a continuation;
- final P2-A output passes the existing P2-B input validator;
- snapshot, PDF, prompt, schema, input-fingerprint, result-hash, and Evidence failures block before dispatch;
- duplicate/concurrent creation is rejected;
- checkpoint transaction failure prevents the next request;
- unknown Usage latches the budget and preserves prior checkpoints;
- restart never replays a failed run;
- inherited calls do not appear in model-call usage;
- errors and audit responses contain no credential or raw provider detail.

## Operational path after merge

The first authorized continuation from run `74bbc32a016342f59cf4cbeea89d9124` must pay for all five N3 partitions plus contribution normalization because no earlier structured checkpoints exist. If it later fails after validated checkpoints, a separately authorized continuation can reuse them and pay only for missing partitions plus any unfinished normalization. No continuation is created automatically.
