# P5-R4 Model Endpoint Safety Design

Date: 2026-11-07  
Scope: PR 1 only — unified production model configuration and fail-closed endpoint safety.

## Objective

Prevent every Paper-reviewer production model entry point from sending paper or user content unless the API key, HTTPS endpoint, and model name are explicitly and consistently configured. Preserve all P1–P4 prompts, algorithms, evidence rules, scientific decisions, frozen P5 assets, and the accepted P5-R3B probe behavior.

## Confirmed requirements

1. `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` are required for every production model call.
2. `OPENAI_BASE_URL` must be an explicit HTTPS URL. It may not contain user information, a query string, or a fragment.
3. Process environment normally has deployment precedence over `.env.local`, but different non-empty values for any required model variable are a configuration conflict. A conflict fails closed instead of silently selecting either source.
4. Errors expose only blocker names and conflicting variable names. They never expose keys, endpoint values, model values, environment-file contents, or raw provider errors.
5. Configuration is resolved before a model client is constructed and again in background execution paths before a model request. Invalid configuration cannot create a client or send a request.
6. The frozen model remains `gpt-6.1-sol`; this PR does not alter the P5 Manifest or make the production runtime depend on the Manifest.
7. P5-R2/P5-R3B capability-probe configuration resolution remains unchanged.

## Current risks

- `backend/app.py:model_base_url()` silently falls back to `https://www.yaoxing.best/v1`.
- most call sites silently default `OPENAI_MODEL` to `gpt-6.1-sol` instead of requiring an explicit production choice;
- application startup loads `.env.local` with `override=True`, so file values can silently replace process configuration;
- routes usually check only `OPENAI_API_KEY`, not endpoint/model validity or cross-source conflicts;
- P2-A, P2-B, P2-C, P3, legacy review, deep verification, paper chat, legacy translation, and P4 construct clients independently.

## Approaches considered

### A. Central immutable resolver and client factory — selected

Add a focused `backend/model_runtime.py` module that reads the three required keys from an injected process mapping and an injected/file mapping, detects conflicts, validates the effective values, and returns an immutable runtime configuration. A client factory consumes only that object. API routes call the same resolver as execution functions.

Advantages: one contract, deterministic unit tests, no hidden endpoint, no secret-bearing errors, and surgical call-site changes. It also leaves scientific engines independent of SDK configuration.

### B. Add validation separately at every call site — rejected

This minimizes new abstraction but duplicates security rules across more than ten paths. The rules would drift and future endpoints could easily restore an implicit fallback.

### C. Wrap the complete OpenAI SDK and all model calls — deferred

A comprehensive request/audit wrapper could also unify Usage and budgets, but it would mix PR 1 with P4 budgeting and require broader behavior changes. PR 2 will add P4 budget accounting without turning this security fix into a full client rewrite.

## Architecture

### `backend/model_runtime.py`

The module owns only production configuration and client construction:

- `ModelRuntimeConfiguration`: frozen dataclass containing `api_key`, `base_url`, and `model`.
- `ModelConfigurationError`: safe exception containing stable blocker codes and conflicting variable names only.
- `resolve_model_runtime_config(process_environment, environment_file_values)`: validates sources and returns a configuration.
- `require_model_runtime_config(environment_file_path)`: production adapter that reads `os.environ` plus `.env.local` values without logging either.
- `create_model_client(configuration, *, timeout, max_retries, client_factory=OpenAI)`: passes all three values explicitly to the SDK.

The resolver is pure and receives mappings in tests. It does not read the P5 Manifest and does not import the probe implementation.

### Source precedence and conflicts

Application startup changes `.env.local` loading from `override=True` to `override=False`. This preserves process-environment precedence for non-model application settings.

For each required model variable:

1. normalize surrounding whitespace only for presence/validation;
2. if process and file sources both contain different non-empty values, return `model_configuration_source_conflict` plus the variable name;
3. otherwise use the process value when non-empty, then the file value;
4. reject missing/blank and known placeholder values;
5. never include either value in an exception or API response.

An identical non-empty value in both sources is allowed. A blank value in one source does not conflict with a valid value in the other.

### Endpoint validation

The endpoint must:

- use the `https` scheme;
- include a hostname;
- contain no username/password;
- contain no query string or fragment;
- not be a documented placeholder host.

The resolver does not restrict the hostname to one provider. Provider selection remains an explicit operator decision, not a hard-coded endpoint.

### Entry-point integration

All production OpenAI client construction in `backend/app.py` uses the central factory. Every route that can enqueue or immediately run a model task performs a configuration preflight before accepting work. Background functions resolve again before client construction so queued work fails closed if configuration changes.

Covered paths:

- P2-A paper understanding;
- P2-B professional review;
- P2-C candidate consolidation;
- P3 evidence verification;
- legacy review generation and screening;
- legacy finding deep verification;
- document chat;
- legacy English-draft translation;
- P4 report writing through the existing injected `model_factory`.

The P4 draft module and P4 prompts remain unchanged in this PR; its injected client factory becomes safe because it resolves the shared configuration before construction.

### Failure behavior

Synchronous/API preflight failures return HTTP 503 with a stable generic message and safe blocker names. Background failures retain their existing failed-task status and store only an exception class or blocker code, never secret values.

No provider request is retried or newly introduced by this change. Existing retry behavior is unchanged until the separate P4 budget PR.

## Test design

Tests are zero-network and use injected mappings/fake clients.

1. Resolver accepts process-only, file-only, and identical dual-source configurations.
2. Resolver rejects missing key/endpoint/model, placeholders, HTTP endpoints, malformed endpoints, user information, query strings, and fragments.
3. Resolver rejects each differing non-empty dual-source variable and exposes only the conflicting variable name.
4. Client factory receives the exact resolved key/endpoint/model and configured timeout/retry count without returning or logging secrets.
5. Representative API entry points fail before fake client construction when endpoint or model configuration is absent/conflicting.
6. Existing API tests that intentionally exercise model paths set an explicit fake HTTPS endpoint and model.
7. A static regression test ensures production code contains no Yaoxing fallback URL and all `OpenAI(...)` construction is centralized; the accepted probe is excluded from this production assertion.
8. P2-A/P2-B/P2-C/P3/P4, full backend, P5 deterministic, frontend build, and browser regression remain required before PR recommendation.

## Compatibility and migration

There is no database or API schema migration. Deployments must explicitly configure the three model variables. If `.env.local` and the process environment currently disagree, startup can continue for non-model functions, but every model action fails closed until the conflict is removed.

`.env.example` and the operations document will describe the contract without real credentials or provider-specific defaults.

## Out of scope

- P4 request/Token/time/cost budgets (PR 2 after PR 1 is reviewed and merged);
- changing SDK retries, model prompts, algorithms, evidence handling, or scientific decisions;
- changing P5 frozen assets or the P5-R3B 128-Token result;
- provider identity proof, pricing verification, or any real model request;
- endpoint allowlisting or hard-coding Yaoxing as the only valid provider.

## Acceptance criteria

- no production model request can be made without an explicit, conflict-free API key, HTTPS endpoint, and model;
- configuration errors occur before client construction and contain no secret values;
- all listed production entry points use the shared configuration contract;
- P5-R2/P5-R3B probe tests and behavior are unchanged;
- all required zero-network regressions and CI checks pass.
