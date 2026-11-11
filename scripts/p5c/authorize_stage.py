"""Offline, one-stage C1 authorization. Never constructs a model client."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.p5c_runtime import C1Runtime
from backend.p4_budget import require_p4_budget_configuration
from scripts.p5c.stage_control import C1Budget, C1ControlError


def _check_p4_budget(budget: C1Budget, environment_file: Path) -> None:
    actual = require_p4_budget_configuration(environment_file)
    expected = (
        budget.max_calls, budget.max_input_tokens, budget.max_output_tokens,
        budget.max_output_tokens_per_request, budget.max_duration_ms,
        budget.max_planned_cost_usd, budget.input_usd_per_million,
        budget.output_usd_per_million, budget.pricing_status,
    )
    configured = (
        actual.max_provider_requests, actual.max_input_tokens, actual.max_output_tokens,
        actual.max_output_tokens_per_request, int(actual.max_duration_seconds * 1000),
        str(actual.max_planned_cost_usd), str(actual.input_usd_per_million),
        str(actual.output_usd_per_million), actual.pricing_status,
    )
    from decimal import Decimal
    if expected[:5] != configured[:5] or any(
        Decimal(left) != Decimal(right) for left, right in
        zip(expected[5:8], configured[5:8])
    ) or expected[8] != configured[8]:
        raise C1ControlError("p4_budget_configuration_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize exactly one P5-C1 paid stage")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stage", choices=("p2a", "p2b", "p2c", "p3", "p4"), required=True)
    parser.add_argument("--upstream-run-id", required=True)
    parser.add_argument("--budget-json", type=Path, required=True)
    parser.add_argument("--acknowledge", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        budget = C1Budget.model_validate_json(args.budget_json.read_bytes())
        if args.stage == "p4":
            _check_p4_budget(budget, args.root / ".env.local")
        runtime = C1Runtime(args.data_dir, root=args.root,
                            environment_file=args.root / ".env.local",
                            recover_on_start=False)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        run_id = None
        try:
            token, run_id = runtime.authorize(args.stage, args.upstream_run_id, budget,
                                              args.acknowledge)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"authorization_token": token, "run_id": run_id,
                           "stage": args.stage, "upstream_run_id": args.upstream_run_id}, output)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            os.close(descriptor) if run_id is None else None
            if run_id is not None:
                runtime.store.cancel_unclaimed_authorization(run_id)
            args.output.unlink(missing_ok=True)
            raise
        print(json.dumps({"status": "authorized", "run_id": run_id,
                          "model_calls": 0, "token_written_to_private_file": True}))
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, C1ControlError) else "authorization_configuration_invalid"
        print(json.dumps({"status": "blocked", "code": code, "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
