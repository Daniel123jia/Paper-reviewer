# P5-C0.1 Versioned Manifest and Preflight Implementation Plan

> Scope: evaluation assets and offline validation only. No model client or paid workflow is added.

## Contract and file ownership

- `scripts/p5c/contracts.py`: strict `p5-c/v2.0` manifest and external execution-lock schemas. The committed manifest contains the accepted P1–P4 scientific baseline, its Tree, paper identities, reference hashes, all prompt hashes, schema versions, and separate C1/full report scopes. It deliberately contains no self-referential execution commit.
- `scripts/p5c/build_manifest.py`: deterministic builder from the immutable old manifest and current prompt directory. It verifies the old manifest's accepted SHA before using H1/H2 metadata. It never reads blind question text.
- `scripts/p5c/preflight.py`: offline lock creation and validation. A lock is written only after a clean commit and kept outside Git. It contains the actual execution commit/tree and the committed manifest's byte SHA. Its SHA is confirmed separately by the reviewer and supplied back to preflight. Preflight checks live Git, ancestry, file hashes, schema, mode separation, model identity, and sanitized configuration conflicts. It never imports an OpenAI client.
- `docs/examples/p5-c-v2/evaluation-manifest.json`: candidate immutable asset. Execution lock is intentionally absent from the repository and is not created during this PR.
- `backend/tests/test_p5c_preflight.py`: synthetic PDF/reference fixtures and Fake Client/no-network guards.
- `docs/p5c-versioned-evaluation.md`: operator steps and C0.2 controller contract. The old manifest and old preflight remain untouched.

## Sequence and checks

1. Write tests for the exact old manifest/reference SHA values, schema and report scopes, then run them red before adding the new package.
2. Add strict manifest schema and deterministic builder; make those tests green. Assert committed candidate bytes are stable and contain no private paths, endpoints, keys, or blind questions.
3. Write preflight tests using a temporary Git repository: valid clean commit/tree and approved lock SHA pass; changed commit/tree, dirty worktree, changed Prompt/PDF/reference, altered schema, old-manifest mix, model mismatch, or source conflict fail before a client can be constructed. Run red.
4. Add the smallest lock/preflight implementation. `create_execution_lock()` and `validate_preflight()` accept injected source mappings and test Git facts; CLI reads actual Git and local paths. Lock creation checks a clean tree and conflicting config but never grants model authorization.
5. Add C0.2 design contract documenting one paper/one Chinese internal draft, per-stage manual authorization, idempotent creation, audit and budget accounting, restart policy, and separation from four-draft formal P5. Do not implement it.
6. Run focused tests, full backend, P5 deterministic regression, frontend build, and relevant browser tests with Fake Model. Compare old assets' hashes and Git diff; push one branch and create one PR. Wait for every related CI result. Do not merge.

## Compatibility limits

- The old P5-R preflight must continue to reject the current 30-Prompt tree. The new preflight cannot rewrite its 28-Prompt manifest or hardcoded baseline.
- External execution-lock SHA confirms reviewed identity, not paid-call authorization or provider pricing. The lock generated from a PR head is not the final main lock after squash merge; final execution identity must be captured after an approved merge.
- C1 is a scoped trial, not full P5 acceptance. The existing four-draft runner is unchanged in C0.1.
