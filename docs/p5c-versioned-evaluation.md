# P5-C versioned evaluation candidate (C0.1)

This candidate is **not** permission to call a model or a completed P5 acceptance. It prepares an independently reviewable asset version for the two holdout papers. The historic P5 manifest and `scripts/p5r/preflight.py` remain unchanged.

## Identity and freeze method

- Manifest schema `2.0`, evaluation `p5-c/v2.0`, asset version `p5-c/v2.0-assets.1`.
- Scientific baseline: main commit `69752745fe7c4a0a0289c5da75379c79e99e1cf5`, tree `da319d0d0b02e7b592955a7c89cafd6a4ebfcea1`.
- The candidate manifest records all 30 Prompt file paths, versions, and SHA-256 digests, the schema contract, the frozen model `gpt-6.1-sol`, the H1/H2 PDF identities and SHA-256 digests, and blind reference *identity* and whole-file digests. It contains no blind question text, API key, private PDF path, provider endpoint, actual execution commit, or final cost.
- The final execution commit cannot be embedded in a file inside that same commit without a circular identity. After the final code is merged and reviewed, produce an **external** execution lock in a private path. The lock binds the immutable manifest byte SHA-256 to the actual execution commit and tree. Review the lock and provide its exact SHA-256 to preflight. A PR-head lock is not the final main lock after Squash Merge.
- Preflight must see a clean working tree, the scientific baseline as an ancestor of HEAD, the exact locked commit/tree, every Prompt digest, both PDF digests, both reference whole-file digests and frozen metadata, matching schema and model, and no configuration-source conflicts. Any failure blocks execution. An external lock records code identity; it does not authorize a paid call.

The builder is deterministic:

```bash
python -m scripts.p5c.build_manifest --root . \
  --output docs/examples/p5-c-v2/evaluation-manifest.json
```

After an accepted final main merge, the operator can create an external lock in a private directory. Both H1 and H2 frozen PDFs are required for the full asset preflight, even if the later authorized C1 trial runs only one paper:

```bash
python -m scripts.p5c.preflight --root . \
  --manifest docs/examples/p5-c-v2/evaluation-manifest.json \
  --create-lock /PRIVATE/PATH/p5c-execution-lock.json
python -m scripts.p5c.preflight --root . \
  --manifest docs/examples/p5-c-v2/evaluation-manifest.json \
  --lock /PRIVATE/PATH/p5c-execution-lock.json \
  --expected-lock-sha256 REVIEWED_LOCK_SHA256 \
  --paper sardet100k-neurips2024=/PRIVATE/PATH/H1.pdf \
  --paper sam-iclr2021=/PRIVATE/PATH/H2.pdf \
  --output /PRIVATE/PATH/p5c-preflight.json
```

The CLI has no `--execute` flag and does not construct a model client. The public response includes only hashes, IDs, pass/fail state, and `model_calls: 0`; no blind issue body. Do not copy the blind reference files into model input. Seal run results before reference scoring. PDF paths and execution-lock paths stay outside Git. A lock must not be committed to the public repository.

## Separate scopes and budget meaning

| Scope | Paper | Report output | Authorization |
| --- | --- | --- | --- |
| C1 trial | One holdout paper | One Chinese internal draft | Each paid stage separately approved |
| Full P5 | One holdout paper | Chinese/English internal and author drafts | Separate full-run approval |

The old `scripts/p5/run_evaluation.py` still attempts four drafts. C0.1 does not invoke it or silently reinterpret its acceptance criteria. The historical 90-call, 1.2-million-input-token, 220,000-output-token, 10,800-second per-paper planning ceiling is recorded only for full P5. C1 requires separate explicit stage budgets. Neither a local ceiling nor an estimated price is a supplier-account hard limit. Provider rate, route, and billed amount remain unverified.

## C0.2 controlled execution contract (design only)

1. Select one sealed paper identity and one `internal:zh` output. Lock the final main commit, tree, manifest digest, Prompt inventory and input PDF before any run.
2. Require a separate human authorization for **each** of P2-A, P2-B, P2-C, P3 and P4. P1 stays offline. A stage token must bind paper, parent Run ID, scope, model, endpoint identity, calls, tokens, time and planned-cost ceiling. C0.1 issues no such token.
3. Create each stage through a persistent idempotency key and a database uniqueness constraint. A lost HTTP response or double click must return the same Run ID; it must never create a second paid stage. Do not auto-enter the next stage.
4. Persist Run ID, status, supplier call attempts, reliable Usage, unknown Usage and cumulative local budget after each stage. Unknown Usage stops later requests. Preserve failed and paid runs; no automatic full restart or replay.
5. After P3, inspect the number of P4 internal themes and verify that explicit P4 request, output, input, duration and planned-cost budgets accommodate the normal path before offering P4 authorization.
6. The four-draft full-P5 mode remains separate. C1 trial success cannot be reported as full-P5 or product-release acceptance.
7. Supplier account debit and hard spending limit require independent operator evidence; local estimates do not prove them.

No controller, paid authorization, model invocation, reference scoring or holdout run is implemented in C0.1.
