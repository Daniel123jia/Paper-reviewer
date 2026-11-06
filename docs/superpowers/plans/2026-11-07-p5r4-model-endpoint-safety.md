# P5-R4 Model Endpoint Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every production model entry point fail closed before sending content unless an explicit, conflict-free API key, HTTPS endpoint, and model are available from distinguishable process and `.env.local` sources.

**Architecture:** Add a pure configuration resolver plus one SDK session factory in `backend/model_runtime.py`. Keep model variables out of `os.environ` when reading `.env.local`, preflight every model API route, revalidate background work, and make every request use the model stored beside its configured client. P4 receives that same validated session through its existing injection boundary.

**Tech Stack:** Python 3.11, FastAPI, python-dotenv, OpenAI Python SDK, pytest, SQLite test fixtures, existing frontend/Vitest/Playwright regressions.

---

## File map

- Create `backend/model_runtime.py`: source isolation contract, endpoint validation, safe errors, immutable runtime configuration/session, centralized SDK construction.
- Create `backend/tests/test_model_runtime.py`: pure resolver, source conflicts, endpoint edge cases, factory argument and redaction tests.
- Modify `backend/app.py`: isolate `.env.local` model keys, add route/background preflight, use validated sessions/models, remove fallback endpoint and direct SDK construction.
- Modify `backend/p4_drafts_api.py`: consume validated P4 session/model; preflight only model-generating draft creation.
- Modify `backend/p4_api.py`: pass safe preflight/session dependencies into draft routes.
- Modify `backend/tests/test_local_environment.py`: prove model variables are not imported into process state and repeated/file-updated loads stay isolated.
- Modify `backend/tests/test_usage.py`: remove fallback gateway expectation and retain cost-only tests.
- Modify model-path API tests: provide explicit fake key/HTTPS endpoint/model and assert model identity reaches fake SDK calls.
- Create `backend/tests/test_model_entrypoint_safety.py`: entry-point 503, background revalidation, SSE preflight, P4 local-route availability, and static centralization coverage.
- Modify `.env.example`: document explicit required runtime configuration and conflict behavior.
- Create `docs/p5r4-model-endpoint-safety.md`: operational migration, safe error contract, coverage matrix, and remaining risks.

### Task 1: Pure configuration resolver and SDK session factory

**Files:**
- Create: `backend/model_runtime.py`
- Create: `backend/tests/test_model_runtime.py`

- [ ] **Step 1: Write failing resolver acceptance and conflict tests**

Cover process-only, file-only, identical dual-source values, and one conflict per required variable:

```python
def valid(**overrides):
    values = {"OPENAI_API_KEY": "test-key",
              "OPENAI_BASE_URL": "https://provider.example/v1",
              "OPENAI_MODEL": "gpt-6.1-sol"}
    values.update(overrides)
    return values

def test_resolver_accepts_identical_sources():
    config = resolve_model_runtime_config(valid(), valid())
    assert config.model == "gpt-6.1-sol"

@pytest.mark.parametrize("name", REQUIRED_MODEL_ENV_KEYS)
def test_resolver_fails_closed_for_source_conflict(name):
    process = valid()
    file_values = valid(**{name: "different-nonempty-value"})
    with pytest.raises(ModelConfigurationError) as caught:
        resolve_model_runtime_config(process, file_values)
    assert caught.value.code == "model_configuration_invalid"
    assert caught.value.conflicting_variables == (name,)
    assert "different-nonempty-value" not in str(caught.value.safe_detail())
```

- [ ] **Step 2: Run the resolver tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_model_runtime.py -q`
Expected: collection/import failure because `backend.model_runtime` does not exist.

- [ ] **Step 3: Add endpoint and required-value tests**

Parameterize missing/blank/placeholder key and model plus endpoint cases: HTTP, whitespace, credentials, query, fragment, invalid/non-numeric/out-of-range port, empty/malformed hostname, leading/trailing-hyphen DNS labels, and placeholder host. Assert only blocker codes and variable names appear in `safe_detail()`.

- [ ] **Step 4: Implement the minimal pure resolver**

Define:

```python
REQUIRED_MODEL_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")

@dataclass(frozen=True)
class ModelRuntimeConfiguration:
    api_key: str = field(repr=False)
    base_url: str
    model: str

class ModelConfigurationError(ValueError):
    def __init__(self, blockers: Iterable[str], variables: Iterable[str] = ()):
        self.code = "model_configuration_invalid"
        self.blockers = tuple(dict.fromkeys(blockers))
        self.conflicting_variables = tuple(dict.fromkeys(variables))
        super().__init__(self.code)

    def safe_detail(self) -> dict:
        return {"code": self.code, "blockers": list(self.blockers),
                "variables": list(self.conflicting_variables)}
```

`resolve_model_runtime_config(process_environment, environment_file_values)` must compare non-empty raw source values before selecting, validate all three values, and return the frozen object without logging.

- [ ] **Step 5: Add failing SDK factory test**

Use a recording factory and assert exact `api_key`, `base_url`, `timeout`, and `max_retries`; assert `session.configuration.model` is the validated model and the key is absent from `repr(session.configuration)`.

- [ ] **Step 6: Implement the minimal session factory**

```python
@dataclass(frozen=True)
class ModelRuntimeSession:
    configuration: ModelRuntimeConfiguration
    client: object

def create_model_session(configuration, *, timeout, max_retries,
                         client_factory=OpenAI):
    client = client_factory(api_key=configuration.api_key,
                            base_url=configuration.base_url,
                            timeout=timeout, max_retries=max_retries)
    return ModelRuntimeSession(configuration=configuration, client=client)
```

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest backend/tests/test_model_runtime.py -q`
Expected: all tests pass and no network fixture exists.

- [ ] **Step 8: Commit**

```bash
git add backend/model_runtime.py backend/tests/test_model_runtime.py
git commit -m "feat: add fail-closed model runtime configuration"
```

### Task 2: Keep process and `.env.local` model sources isolated

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/tests/test_local_environment.py`

- [ ] **Step 1: Replace the old override expectation with failing isolation tests**

Tests must prove:

```python
def test_local_environment_does_not_copy_model_values_into_process(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("OPENAI_API_KEY=file-key\nNON_MODEL_SETTING=file-value\n")
    load_local_environment(path)
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["NON_MODEL_SETTING"] == "file-value"
```

Also test: an existing process model key is unchanged; repeat loads do not pollute; changing the file changes the file mapping seen by `require_model_runtime_config`; two different paths remain independent.

- [ ] **Step 2: Run the isolation tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_local_environment.py -q`
Expected: old `load_dotenv(..., override=True)` copies or overwrites the model key.

- [ ] **Step 3: Implement isolated file loading**

Replace `load_dotenv` use in `backend/app.py` with `dotenv_values`. `load_local_environment(path)` writes only non-model values to `os.environ`, preserving the existing override behavior for those unrelated settings. Add `MODEL_ENVIRONMENT_FILE` and:

```python
def runtime_model_configuration():
    return require_model_runtime_config(MODEL_ENVIRONMENT_FILE)
```

`require_model_runtime_config(path)` freshly reads `dotenv_values(path)` and compares it with live `os.environ` on every call.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest backend/tests/test_local_environment.py backend/tests/test_model_runtime.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/model_runtime.py backend/tests/test_local_environment.py backend/tests/test_model_runtime.py
git commit -m "fix: isolate process and file model configuration"
```

### Task 3: Centralize P2-A through P3 and legacy production requests

**Files:**
- Modify: `backend/app.py`
- Create: `backend/tests/test_model_entrypoint_safety.py`
- Modify: `backend/tests/test_p2a_api.py`
- Modify: `backend/tests/test_p2b_api.py`
- Modify: `backend/tests/test_p2c_api.py`
- Modify: `backend/tests/test_p3_api.py`
- Modify: `backend/tests/test_review.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_drafts.py`

- [ ] **Step 1: Add a shared fake explicit model environment fixture**

In the new safety test module, set only fake values:

```python
@pytest.fixture
def explicit_model_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
```

Never read `.env.local` or use a real key.

- [ ] **Step 2: Write failing API preflight tests**

For P2-A, P2-B, P2-C, P3, legacy analyze, deep verification, SSE chat, and English translation, remove the endpoint or introduce a file/process conflict and assert:

- HTTP 503 before a background task is queued or a `StreamingResponse` is returned;
- safe detail contains stable code/blockers/variable names only;
- a recording `OpenAI` factory has zero calls.

Use the smallest valid database fixture required to reach each route; do not execute scientific engines.

- [ ] **Step 3: Run the entry-point tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_model_entrypoint_safety.py -q`
Expected: current routes accept key-only configuration or create a streaming response.

- [ ] **Step 4: Introduce safe API preflight and session helpers in `create_app`**

```python
def model_configuration_or_503():
    try:
        return runtime_model_configuration()
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=503, detail=exc.safe_detail()) from None

def model_session(*, timeout, max_retries):
    config = runtime_model_configuration()
    return create_model_session(config, timeout=timeout,
                                max_retries=max_retries,
                                client_factory=OpenAI)
```

Call `model_configuration_or_503()` in every model route before accepting/enqueuing work. For SSE chat, call it before defining/returning `StreamingResponse`.

- [ ] **Step 5: Replace every direct production client/model lookup**

Each execution path obtains a fresh session immediately before its first possible request:

```python
session = model_session(timeout=180, max_retries=0)
client = session.client
model = session.configuration.model
```

All `chat.completions.create` calls use `model=model`. Remove `model_base_url()`, direct production `OpenAI(...)`, and every production `os.environ.get("OPENAI_MODEL", ...)`.

- [ ] **Step 6: Add background revalidation test**

Capture the queued background callback after successful route preflight, mutate/remove the explicit endpoint, execute the callback, and assert the run becomes failed with a safe configuration error and the fake SDK factory remains uncalled.

- [ ] **Step 7: Update existing model-path tests with explicit fake configuration**

Set fake HTTPS endpoint and model wherever existing tests already set a fake key. Extend fake SDK assertions so every observed request model equals the explicitly configured fake model.

- [ ] **Step 8: Run focused API regressions and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest \
  backend/tests/test_model_entrypoint_safety.py \
  backend/tests/test_p2a_api.py backend/tests/test_p2b_api.py \
  backend/tests/test_p2c_api.py backend/tests/test_p3_api.py \
  backend/tests/test_review.py backend/tests/test_api.py \
  backend/tests/test_drafts.py -q
```

- [ ] **Step 9: Commit**

```bash
git add backend/app.py backend/tests
git commit -m "fix: fail closed at every production model entry point"
```

### Task 4: Make P4 client and request model one validated session

**Files:**
- Modify: `backend/p4_api.py`
- Modify: `backend/p4_drafts_api.py`
- Modify: `backend/app.py`
- Modify: `backend/tests/test_p4_drafts_api.py`
- Modify: `backend/tests/test_model_entrypoint_safety.py`

- [ ] **Step 1: Write failing P4 generation/session test**

Inject a recording `ModelRuntimeSession` whose configuration model is `validated-model`. Create a model-requiring draft and assert every fake `chat.completions.create` receives `model="validated-model"`, while the fake client constructor receives the validated fake key and endpoint.

- [ ] **Step 2: Write failing P4 failure/local-operation tests**

Assert model-requiring draft creation returns safe 503 for missing/conflicting configuration and makes zero client calls. Create/read/edit/approve/export an already stored eligible local draft with no model configuration and assert these routes are not blocked by the model gate.

- [ ] **Step 3: Run P4 focused tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_p4_drafts_api.py backend/tests/test_model_entrypoint_safety.py -q`

- [ ] **Step 4: Change the P4 injection contract minimally**

Pass two dependencies through `register_p4_routes`/`register_p4_draft_routes`:

- a safe API preflight callable for model-generating draft creation;
- a session factory returning `ModelRuntimeSession`.

Inside `call_model`, initialize one session lazily and derive both client and `requested` model from it. Remove all P4 direct `os.environ` model/key reads. Leave retry loops and prompts unchanged for PR 2.

- [ ] **Step 5: Run P4 tests and verify GREEN**

Run: `.venv/bin/python -m pytest backend/tests/test_p4_api.py backend/tests/test_p4_drafts_api.py backend/tests/test_p4_planner.py backend/tests/test_p4_writer.py backend/tests/test_p4_validator.py -q`

- [ ] **Step 6: Commit**

```bash
git add backend/app.py backend/p4_api.py backend/p4_drafts_api.py backend/tests/test_p4_drafts_api.py backend/tests/test_model_entrypoint_safety.py
git commit -m "fix: bind P4 requests to validated model sessions"
```

### Task 5: Static coverage guard and operator documentation

**Files:**
- Modify: `backend/tests/test_model_entrypoint_safety.py`
- Modify: `backend/tests/test_usage.py`
- Modify: `.env.example`
- Create: `docs/p5r4-model-endpoint-safety.md`

- [ ] **Step 1: Write failing static centralization tests**

Parse production Python sources and assert:

- `www.yaoxing.best` does not occur under `backend/`;
- `OpenAI(` occurs only in `backend/model_runtime.py` (tests and `scripts/p5r2` are outside this assertion);
- `OPENAI_MODEL` is not read in `backend/app.py` or `backend/p4_drafts_api.py`;
- no production request contains a literal/default model.

- [ ] **Step 2: Run static tests and verify RED, then remove obsolete fallback tests/code**

Delete `test_gateway_url_is_configurable` and the removed `model_base_url` import from `backend/tests/test_usage.py`. Do not alter cost calculation behavior.

- [ ] **Step 3: Document explicit production configuration**

Update `.env.example` comments and create `docs/p5r4-model-endpoint-safety.md` with:

- required variables and HTTPS rules;
- process/file conflict behavior and safe error example;
- migration steps for deployments previously relying on the implicit relay;
- complete entry-point coverage matrix;
- statement that no key, endpoint, model value, or raw provider body is logged;
- unchanged retries and deferred P4 budget work;
- unchanged P5-R3B FAIL and no real calls in this PR.

- [ ] **Step 4: Run static and documentation-adjacent tests**

Run: `.venv/bin/python -m pytest backend/tests/test_model_entrypoint_safety.py backend/tests/test_usage.py backend/tests/test_p5r2_capability_probe.py -q`

- [ ] **Step 5: Commit**

```bash
git add .env.example backend/tests/test_model_entrypoint_safety.py backend/tests/test_usage.py docs/p5r4-model-endpoint-safety.md
git commit -m "docs: define explicit production model configuration"
```

### Task 6: Full zero-network verification and PR delivery

**Files:**
- Review all branch changes; no new feature files expected.

- [ ] **Step 1: Confirm frozen assets and scope**

Run:

```bash
git diff --name-only origin/main...HEAD
git diff --exit-code origin/main...HEAD -- backend/prompts docs/examples/p5-regression/evaluation-manifest.json
rg -n 'www\.yaoxing\.best|OpenAI\(' backend --glob '*.py'
git diff --check origin/main...HEAD
```

Expected: no frozen Prompt/Manifest changes; only the centralized factory contains production `OpenAI(`.

- [ ] **Step 2: Run complete backend regression**

Run: `.venv/bin/python -m pytest backend/tests -q`
Expected: exit 0, with only the existing intentional skips.

- [ ] **Step 3: Run inherited P2/P3 and P5 deterministic checks**

The P2-A, P2-B, P2-C, P3, P4, and P5 workflows all use the full backend command below; run it once with the fixed local public-PDF paths when available, then run the P5 deterministic product harness:

```bash
P11_SHAW_PDF="$P11_SHAW_PDF" P11_JMLR_PDF="$P11_JMLR_PDF" \
  .venv/bin/python -m pytest backend/tests -q
.venv/bin/python scripts/p5/run_product_acceptance.py --output /tmp/p5r4-pr1-deterministic
```

Confirm `.github/workflows/p5-end-to-end-acceptance.yml` still contains `fresh-model-run: if: ${{ false }}` and that neither command creates an external model client.

- [ ] **Step 4: Build frontend**

Run: `npm run build` in `frontend/`.
Expected: exit 0.

- [ ] **Step 5: Run existing browser regressions**

Run the exact Playwright commands from CI for PDF/legacy review and P4 edit/evidence/export. Fixtures must use fake model configuration and make no provider request.

- [ ] **Step 6: Inspect for secrets and unintended behavior**

Run tracked-file secret pattern scans, verify `.env.local` remains ignored/untracked, and inspect `git diff origin/main...HEAD`. Never print environment values.

- [ ] **Step 7: Push branch and create PR**

```bash
git push
gh pr create --base main --head feature/p5r4-model-endpoint-safety \
  --title "P5-R4: fail closed on implicit model endpoints" \
  --body-file /tmp/p5r4-pr1-body.md
```

The PR body must state: zero real model calls; P5-R3B remains FAIL; P5 remains FAIL; ChatGPT comparison remains pending; P4 retry/budget controls are deferred to PR 2.

- [ ] **Step 8: Wait for and report every required CI check**

Use `gh pr checks --watch`. Missing, skipped unexpectedly, pending, or failed checks are not reported as success. Do not merge.

## Compatibility risks to verify

- Deployments relying on the implicit Yaoxing endpoint will now receive 503 until `OPENAI_BASE_URL` is explicit.
- Deployments with a real process value and a different `.env.local` value will fail closed; operators must remove one conflict rather than rely on precedence.
- Tests that previously set only `OPENAI_API_KEY` must set all three fake values.
- Non-model `.env.local` behavior remains as before; model values are intentionally no longer written into `os.environ`.
- Existing retries remain unchanged in PR 1 and are still a production cost risk until PR 2.
- Explicit configuration validates the requested identifier and transport shape; it does not independently prove provider identity, underlying model identity, pricing, or billing.
