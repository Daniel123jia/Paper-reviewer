# P5-R3B Estimated Pricing Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, auditably unverified estimated-pricing option to the existing one-call P5-R2 probe without weakening its strict default or its separate paid-call authorization gate.

**Architecture:** Extend only `scripts/p5r2/capability_probe.py`: thread one boolean pricing-policy argument from the CLI through configuration validation and probe execution, and keep all production P1–P4 loaders untouched. Resolve the optional environment file at lower precedence than the current process and attach redacted provenance metadata to reports. Version new reports as Schema 1.3 while retaining the existing fields needed by 1.2 readers.

**Tech Stack:** Python 3.11, argparse, python-dotenv, Decimal, pytest, OpenAI SDK mock clients, GitHub Actions.

---

### Task 1: Specify strict and estimated pricing behavior with failing tests

**Files:**
- Modify: `backend/tests/test_p5r2_capability_probe.py`
- Modify: `scripts/p5r2/capability_probe.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests that call the report builder with `$0.80/$4.00` and no provider attestation:

```python
report = build_configuration_report(
    configured_environment(
        OPENAI_INPUT_USD_PER_MILLION="0.80",
        OPENAI_OUTPUT_USD_PER_MILLION="4.00",
        OPENAI_PRICING_SOURCE_URL="",
        OPENAI_PRICING_VERIFIED_AT="",
        OPENAI_PRICING_VERIFICATION="",
    ),
    FROZEN_MODEL,
    allow_estimated_pricing=True,
)
assert report["probe_schema_version"] == "1.3"
assert report["configuration_ready"] is True
assert report["pricing"]["status"] == "estimated_unverified"
assert report["pricing"]["estimated_probe_cost_usd"] == 0.0003584
assert report["pricing"]["source_evidence_status"] == "not_provided_or_not_used"
assert report["pricing"]["independently_verified"] is False
assert report["pricing"]["account_hard_limit"] is False
assert report["pricing"]["scope"] == "p5r3b_single_nonpaper_capability_probe"
```

Also assert the same environment remains blocked without the argument, and that missing or invalid prices remain blocked even when the argument is true.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_p5r2_capability_probe.py -k "estimated_pricing or configuration_report_is_redacted" -q
```

Expected: failure because the new keyword argument and Schema 1.3 do not exist.

- [ ] **Step 3: Implement the minimal pricing policy**

In `scripts/p5r2/capability_probe.py`:

```python
PROBE_SCHEMA_VERSION = "1.3"
ESTIMATED_PRICING_SCOPE = "p5r3b_single_nonpaper_capability_probe"

def _pricing(environment, *, allow_estimated_pricing=False, today=None):
    # Existing finite/non-negative rate parsing remains shared.
    if allow_estimated_pricing:
        estimate = (
            Decimal(PROBE_INPUT_TOKEN_ESTIMATE) * input_rate
            + Decimal(PROBE_OUTPUT_TOKEN_CEILING) * output_rate
        ) / Decimal(1_000_000)
        return ({
            "status": "estimated_unverified",
            "estimated_probe_cost_usd": float(
                estimate.quantize(Decimal("0.00000001"))
            ),
            "basis": "operator-supplied unverified planning rates; input usage remains an estimate",
            "source_evidence_status": "not_provided_or_not_used",
            "scope": ESTIMATED_PRICING_SCOPE,
            "independently_verified": False,
            "account_hard_limit": False,
        }, [])
    # Preserve the existing strict attestation/date branch unchanged.
```

Add the same keyword-only argument to `build_configuration_report()` and pass it to `_pricing()`.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit the pricing configuration behavior**

```bash
git add scripts/p5r2/capability_probe.py backend/tests/test_p5r2_capability_probe.py
git commit -m "feat: add explicit estimated pricing probe mode"
```

### Task 2: Preserve the authorization gate and distinguish calculated cost

**Files:**
- Modify: `backend/tests/test_p5r2_capability_probe.py`
- Modify: `scripts/p5r2/capability_probe.py`

- [ ] **Step 1: Write failing CLI and execution tests**

Extend the CLI helper with `estimated=False` and append `--allow-estimated-pricing` only when true. Add tests proving:

```python
# Estimated pricing does not authorize a request.
exit_code = main(
    cli_args(
        environment_file,
        manifest,
        output,
        execute=True,
        estimated=True,
    ),
    client_factory=forbidden_client,
)
assert exit_code == 2
assert called is False

# A mock authorized request preserves one call, zero retries, fixed model,
# max_tokens=64, stream=True and include_usage=True.
```

For an authorized mock request in estimated mode, assert the result carries `pricing.status=estimated_unverified` and:

```python
assert result["cost"]["status"] == "estimated_unverified_rate_calculation"
assert result["cost"]["actual_cost_usd"] == 0.0000584
assert result["cost"]["provider_billed_cost_usd"] is None
assert result["cost"]["is_provider_billed_cost"] is False
```

Keep a strict-mode assertion for `operator_attested_rate_calculation`.

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_p5r2_capability_probe.py -k "estimated_pricing and (authorization or bounded or cost or cli)" -q
```

Expected: failures because CLI and execution do not accept the mode.

- [ ] **Step 3: Thread the explicit mode through execution**

Add `--allow-estimated-pricing` to argparse. Add a keyword-only `allow_estimated_pricing=False` argument to `run_capability_probe()`. Pass it into `build_configuration_report()`, include the redacted pricing object in the execution result, and choose cost status from the validated pricing policy. Keep `authorization_ack` validation before configuration and client construction.

Add compatibility fields to every cost object:

```python
"provider_billed_cost_usd": None,
"is_provider_billed_cost": False,
```

Retain `actual_cost_usd` as the Schema 1.2-compatible local calculation field and document that it is not a provider invoice value.

- [ ] **Step 4: Run the new tests and confirm GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit authorization and cost reporting**

```bash
git add scripts/p5r2/capability_probe.py backend/tests/test_p5r2_capability_probe.py
git commit -m "test: preserve probe authorization and cost semantics"
```

### Task 3: Make environment precedence explicit and redacted

**Files:**
- Modify: `backend/tests/test_p5r2_capability_probe.py`
- Modify: `scripts/p5r2/capability_probe.py`

- [ ] **Step 1: Write failing environment-resolution tests**

Use `monkeypatch` and a temporary `.env.local` containing conflicting endpoint, model, pricing and key values. Assert `_effective_environment()` returns process values and metadata shaped like:

```python
assert resolution == {
    "precedence": "process_environment_over_environment_file",
    "conflicting_variable_names": [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_INPUT_USD_PER_MILLION",
        "OPENAI_MODEL",
        "OPENAI_OUTPUT_USD_PER_MILLION",
    ],
}
```

Assert serialized metadata contains variable names but none of the file/process values. Add a CLI configuration test showing the report uses the process model/rates and records the same redacted resolution. Assert production environment helpers outside this script are unchanged by checking the Git diff scope.

- [ ] **Step 2: Run the resolution tests and confirm RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_p5r2_capability_probe.py -k "environment_resolution or process_environment" -q
```

Expected: failure because the file currently overrides the process and no metadata exists.

- [ ] **Step 3: Implement lower-precedence environment-file loading**

Change only the probe-local helper:

```python
def _effective_environment(path: Path) -> tuple[dict[str, str], dict]:
    file_values = (
        {key: value or "" for key, value in dotenv_values(path).items()}
        if path.is_file()
        else {}
    )
    process_values = dict(os.environ)
    conflicts = sorted(
        key for key in file_values.keys() & process_values.keys()
        if file_values[key] and process_values[key]
        and file_values[key] != process_values[key]
    )
    values = {**file_values, **process_values}
    return values, {
        "precedence": "process_environment_over_environment_file",
        "conflicting_variable_names": conflicts,
    }
```

Attach only this metadata to configuration, blocked and execution reports in `main()`. Never attach values, file paths or endpoint hostnames.

- [ ] **Step 4: Run the resolution tests and confirm GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Commit environment provenance handling**

```bash
git add scripts/p5r2/capability_probe.py backend/tests/test_p5r2_capability_probe.py
git commit -m "fix: make probe environment precedence auditable"
```

### Task 4: Update operator documentation and compatibility assertions

**Files:**
- Modify: `docs/p5r2-model-capability-probe.md`
- Modify: `backend/tests/test_p5r2_capability_probe.py`

- [ ] **Step 1: Add documentation assertions**

Add a small test that reads the guide and asserts it names `--allow-estimated-pricing`, `estimated_unverified`, the P5-R3B-only scope and the separate authorization requirement. Assert the frozen Manifest still requests `gpt-6.1-sol` and has no diff.

- [ ] **Step 2: Run the documentation test and confirm RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_p5r2_capability_probe.py -k "documentation or frozen_manifest" -q
```

Expected: documentation test fails before the guide is updated.

- [ ] **Step 3: Document the two modes and safe key reuse**

Update the guide with:

```text
Strict mode remains the default.
--allow-estimated-pricing accepts operator estimates only for the one-call,
non-paper P5-R3B probe and reports estimated_unverified.
It never replaces --execute plus P5R2_MINIMAL_PROBE_AUTHORIZED.
```

Show the zero-network configuration command using process variables for endpoint/model/rates and `--environment-file /existing/path/.env.local`, without any key value. Explain process-over-file precedence, redacted conflict names, `$0.0003584` as a planning estimate rather than a hard ceiling, and Schema 1.3 compatibility fields.

- [ ] **Step 4: Run the documentation test and the complete probe suite**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_p5r2_capability_probe.py -q
```

Expected: all P5-R2/P5-R3B probe tests pass with no network client other than mocks.

- [ ] **Step 5: Commit documentation and contract tests**

```bash
git add docs/p5r2-model-capability-probe.md backend/tests/test_p5r2_capability_probe.py
git commit -m "docs: describe P5-R3B estimated pricing gate"
```

### Task 5: Run zero-network regression and prepare the PR

**Files:**
- Verify only; modify no frozen assets.

- [ ] **Step 1: Verify the diff and frozen assets**

Run:

```bash
git diff origin/main --check
git diff --name-only origin/main
git diff --exit-code origin/main -- docs/examples/p5-regression/evaluation-manifest.json
git grep -nE 'sk-[A-Za-z0-9_-]{16,}|OPENAI_API_KEY=[^<]' -- ':!backend/tests/test_p5r2_capability_probe.py'
```

Expected: only the probe script, tests, guide, design and plan differ; Manifest diff is empty; no credential is found.

- [ ] **Step 2: Run the complete backend regression**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

Expected: all existing backend tests pass, with the repository's existing environment-dependent skips only.

- [ ] **Step 3: Run deterministic inherited checks**

Run the shared frontend production build used by inherited workflows:

```bash
cd frontend && npm run build
```

Then return to the repository root and run the deterministic P5 tests directly covered by the backend suite:

```bash
cd ..
.venv/bin/python -m pytest \
  backend/tests/test_p5_acceptance.py \
  backend/tests/test_p5r_preflight.py \
  backend/tests/test_p5r2_capability_probe.py -q
```

Expected: the production build and all selected deterministic tests pass. P2-A, P2-B, P2-C and P3 use the same complete `backend/tests` command already run in Step 2; their GitHub workflows must also pass on the PR because the backend test file changed. No command uses `--execute` or a real environment file.

- [ ] **Step 4: Push and create the PR**

```bash
git push origin feature/p5r3b-estimated-pricing-probe
gh pr create \
  --base main \
  --head feature/p5r3b-estimated-pricing-probe \
  --title "P5-R3B: add explicit estimated pricing probe mode" \
  --body-file /tmp/p5r3b-pr-body.md
```

Expected: a new open PR targeting `main`; do not merge it.

- [ ] **Step 5: Wait for and verify GitHub CI**

Run `gh pr checks <number> --watch` and record every check URL and result. Any failed, missing or still-running required check remains non-passing. Do not run `--execute` and do not access a real environment file.
