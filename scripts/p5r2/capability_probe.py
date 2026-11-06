from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values
from openai import OpenAI
from pydantic import BaseModel, ConfigDict


FROZEN_MODEL = "gpt-6.1-sol"
AUTHORIZATION_ACK = "P5R2_MINIMAL_PROBE_AUTHORIZED"
PROBE_SCHEMA_VERSION = "1.3"
PROBE_MAX_CALLS = 1
PROBE_INPUT_TOKEN_ESTIMATE = 128
PROBE_OUTPUT_TOKEN_CEILING = 64
PROBE_TIMEOUT_SECONDS = 180
PRICING_VERIFICATION_MAX_AGE_DAYS = 30
ESTIMATED_PRICING_SCOPE = "p5r3b_single_nonpaper_capability_probe"
PROBE_SYSTEM = "Return exactly one compact JSON object and no Markdown."
PROBE_USER = 'Return {"ok":true,"probe":"p5r2"}.'


class ProbeAuthorizationError(ValueError):
    """Raised before client construction when paid execution is not authorized."""


class ProbeConfigurationError(ValueError):
    """Raised before client construction when the frozen environment is incomplete."""


class ProbeManifestError(ValueError):
    """Raised before client construction when the frozen manifest is unusable."""

    def __init__(self, blocker: str, error_type: str):
        super().__init__(blocker)
        self.blocker = blocker
        self.error_type = error_type


class ProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    probe: str


def _placeholder(value: str, *, endpoint: bool = False) -> bool:
    lowered = value.strip().casefold()
    markers = ("replace-with", "your-", "your_", "<", ">")
    if any(marker in lowered for marker in markers):
        return True
    return endpoint and lowered in {
        "https://api.example.com/v1",
        "https://example.com/v1",
    }


def _valid_endpoint(value: str) -> bool:
    parsed = urlparse(value)
    return (parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment)


def _pricing(
    environment: Mapping[str, str], *, allow_estimated_pricing: bool = False,
    today: date | None = None,
) -> tuple[dict, list[str]]:
    raw_input = environment.get("OPENAI_INPUT_USD_PER_MILLION", "").strip()
    raw_output = environment.get("OPENAI_OUTPUT_USD_PER_MILLION", "").strip()
    if not raw_input or not raw_output:
        return ({
            "status": "unknown",
            "estimated_probe_cost_usd": None,
            "basis": "operator rates are not configured",
            "source_evidence_status": "missing",
            "independently_verified": False,
            "account_hard_limit": False,
        }, ["pricing_not_configured"])
    try:
        input_rate = Decimal(raw_input)
        output_rate = Decimal(raw_output)
        if (not input_rate.is_finite() or not output_rate.is_finite()
                or input_rate < 0 or output_rate < 0):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return ({
            "status": "unknown",
            "estimated_probe_cost_usd": None,
            "basis": "operator rates are invalid",
            "source_evidence_status": "missing",
            "independently_verified": False,
            "account_hard_limit": False,
        }, ["pricing_invalid"])
    estimate = (
        Decimal(PROBE_INPUT_TOKEN_ESTIMATE) * input_rate
        + Decimal(PROBE_OUTPUT_TOKEN_CEILING) * output_rate
    ) / Decimal(1_000_000)
    if allow_estimated_pricing:
        return ({
            "status": "estimated_unverified",
            "estimated_probe_cost_usd": float(
                estimate.quantize(Decimal("0.00000001"))
            ),
            "basis": ("operator-supplied unverified planning rates; "
                      "input usage remains an estimate"),
            "source_evidence_status": "not_provided_or_not_used",
            "scope": ESTIMATED_PRICING_SCOPE,
            "independently_verified": False,
            "account_hard_limit": False,
        }, [])
    source_url = environment.get("OPENAI_PRICING_SOURCE_URL", "").strip()
    verification = environment.get("OPENAI_PRICING_VERIFICATION", "").strip()
    verified_at = environment.get("OPENAI_PRICING_VERIFIED_AT", "").strip()
    current_date = today or date.today()
    try:
        verified_date = date.fromisoformat(verified_at)
        date_valid = True
    except ValueError:
        verified_date = None
        date_valid = False
    date_status = "missing_or_invalid"
    date_blockers: list[str] = []
    if date_valid and verified_date is not None:
        age_days = (current_date - verified_date).days
        if age_days < 0:
            date_status = "future_dated"
            date_blockers.append("provider_pricing_verification_future_dated")
        elif age_days > PRICING_VERIFICATION_MAX_AGE_DAYS:
            date_status = "expired"
            date_blockers.append("provider_pricing_verification_expired")
        else:
            date_status = "current"
    source_attested = (
        _valid_endpoint(source_url)
        and not _placeholder(source_url, endpoint=True)
        and verification == "provider_source_checked"
        and date_status == "current"
    )
    evidence_status = (
        "operator_attested" if source_attested
        else date_status if date_status in {"future_dated", "expired"}
        else "missing_or_invalid"
    )
    if not source_attested and not date_blockers:
        date_blockers.append("provider_pricing_not_verified")
    return ({
        "status": ("operator_attested_provider_pricing" if source_attested
                   else "operator_configured_unverified"),
        "estimated_probe_cost_usd": float(
            estimate.quantize(Decimal("0.00000001"))
        ),
        "basis": ("operator-supplied rates with an attested provider source; "
                  "input usage remains an estimate" if source_attested else
                  "operator-supplied rates without valid provider-source attestation"),
        "source_evidence_status": evidence_status,
        "verification_max_age_days": PRICING_VERIFICATION_MAX_AGE_DAYS,
        "independently_verified": False,
        "account_hard_limit": False,
    }, [] if source_attested else date_blockers)


def build_configuration_report(
    environment: Mapping[str, str], manifest_requested_model: str,
    *, allow_estimated_pricing: bool = False, today: date | None = None,
) -> dict:
    blockers: list[str] = []
    key = environment.get("OPENAI_API_KEY", "").strip()
    if not key:
        credential_status = "not_configured"
        blockers.append("model_credential_not_configured")
    elif _placeholder(key):
        credential_status = "placeholder_invalid"
        blockers.append("model_credential_is_placeholder")
    else:
        credential_status = "configured"

    endpoint = environment.get("OPENAI_BASE_URL", "").strip()
    if not endpoint:
        endpoint_status = "not_explicitly_configured"
        blockers.append("model_endpoint_not_explicitly_configured")
    elif _placeholder(endpoint, endpoint=True):
        endpoint_status = "placeholder_invalid"
        blockers.append("model_endpoint_is_placeholder")
    elif not _valid_endpoint(endpoint):
        endpoint_status = "invalid"
        blockers.append("model_endpoint_invalid")
    else:
        endpoint_status = "configured_valid"

    configured_model = environment.get("OPENAI_MODEL", "").strip()
    requested_model = configured_model or manifest_requested_model
    if not configured_model:
        blockers.append("requested_model_not_explicitly_configured")
    elif requested_model != manifest_requested_model:
        blockers.append("requested_model_mismatch")

    pricing, pricing_blockers = _pricing(
        environment,
        allow_estimated_pricing=allow_estimated_pricing,
        today=today,
    )
    blockers.extend(pricing_blockers)
    return {
        "probe_schema_version": PROBE_SCHEMA_VERSION,
        "execution_mode": "configuration_check_only",
        "configuration_ready": not blockers,
        "credential_status": credential_status,
        "endpoint_status": endpoint_status,
        "endpoint_label": "configured_custom_endpoint" if endpoint else "not_configured",
        "provider_identity": "operator_confirmation_required",
        "api_protocol": "openai_compatible_chat_completions",
        "requested_model": requested_model,
        "manifest_requested_model": manifest_requested_model,
        "model_matches_manifest": requested_model == manifest_requested_model,
        "pricing": pricing,
        "limits": {
            "max_calls": PROBE_MAX_CALLS,
            "input_tokens": {
                "kind": "planning_estimate",
                "estimated_tokens": PROBE_INPUT_TOKEN_ESTIMATE,
                "enforced": False,
            },
            "output_tokens": {
                "requested_tokens": PROBE_OUTPUT_TOKEN_CEILING,
                "request_parameter": "max_tokens",
                "provider_enforcement": "pending_real_probe",
            },
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
            "sdk_max_retries": 0,
        },
        "probe": {
            "status": "not_run_pending_authorization",
            "network_attempted": False,
            "model_calls": 0,
        },
        "blockers": blockers,
        "locally_verified": [
            "environment_shape",
            "frozen_model_match",
            "single_call_and_token_gates",
            "public_report_redaction",
        ],
        "requires_real_request": [
            "endpoint_connectivity",
            "authentication",
            "requested_vs_actual_model",
            "streaming_chat_completions",
            "structured_json_parse",
            "usage_reporting",
            "actual_latency",
        ],
        "not_covered_by_minimal_probe": [
            "p4_non_streaming_chat_completion",
            "long_context_capacity",
            "paper_scale_output_limits",
            "multi_call_reliability",
            "scientific_review_quality",
        ],
    }


def _usage_value(usage, name: str, fallback: str | None = None):
    value = getattr(usage, name, None) if usage is not None else None
    if value is None and fallback:
        value = getattr(usage, fallback, None) if usage is not None else None
    return value


def _validated_usage(usage) -> dict:
    values = {
        "input_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"),
        "output_tokens": _usage_value(usage, "completion_tokens", "output_tokens"),
        "total_tokens": _usage_value(usage, "total_tokens"),
    }
    normalized: dict[str, int | None] = {}
    missing: list[str] = []
    errors: list[str] = []
    for name, value in values.items():
        if value is None:
            normalized[name] = None
            missing.append(f"{name}_missing")
        elif type(value) is not int:
            normalized[name] = None
            errors.append(f"{name}_not_integer")
        else:
            normalized[name] = value
            if value < 0:
                errors.append(f"{name}_negative")
    if not errors and not missing:
        if normalized["total_tokens"] != (
            normalized["input_tokens"] + normalized["output_tokens"]
        ):
            errors.append("total_tokens_inconsistent")
    status = "invalid" if errors else "incomplete" if missing else "complete"
    report: dict = {"status": status, **normalized}
    if errors:
        report["validation_errors"] = errors
    elif missing:
        report["missing_fields"] = missing
    return report


def run_capability_probe(
    environment: Mapping[str, str],
    manifest_requested_model: str,
    authorization_ack: str,
    client_factory: Callable[..., object] = OpenAI,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    if authorization_ack != AUTHORIZATION_ACK:
        raise ProbeAuthorizationError(
            "paid probe requires explicit authorization acknowledgement"
        )
    configuration = build_configuration_report(environment, manifest_requested_model)
    if not configuration["configuration_ready"]:
        raise ProbeConfigurationError(
            "paid probe configuration is blocked: " + ",".join(configuration["blockers"])
        )

    started = clock()
    result = {
        "probe_schema_version": PROBE_SCHEMA_VERSION,
        "status": "failed",
        "network_attempted": False,
        "model_calls": 0,
        "requested_model": manifest_requested_model,
        "actual_model": None,
        "structured_json": None,
        "usage": {
            "status": "incomplete",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        "cost": {
            "status": "unknown",
            "actual_cost_usd": None,
            "account_hard_limit": False,
        },
        "budget_observation": {
            "input_tokens": {
                "estimate": PROBE_INPUT_TOKEN_ESTIMATE,
                "actual": None,
                "status": "usage_unavailable",
            },
            "output_tokens": {
                "requested_limit": PROBE_OUTPUT_TOKEN_CEILING,
                "actual": None,
                "status": "usage_unavailable",
            },
        },
        "output_limit_compatibility": {
            "parameter": "max_tokens",
            "status": "not_confirmed",
        },
        "duration_ms": None,
        "blockers": [],
        "error": None,
    }
    try:
        client = client_factory(
            api_key=environment["OPENAI_API_KEY"],
            base_url=environment["OPENAI_BASE_URL"],
            timeout=PROBE_TIMEOUT_SECONDS,
            max_retries=0,
        )
        result["network_attempted"] = True
        result["model_calls"] = 1
        stream = client.chat.completions.create(
            model=manifest_requested_model,
            messages=[
                {"role": "system", "content": PROBE_SYSTEM},
                {"role": "user", "content": PROBE_USER},
            ],
            stream=True,
            stream_options={"include_usage": True},
            max_tokens=PROBE_OUTPUT_TOKEN_CEILING,
        )
        pieces = []
        usage = None
        for chunk in stream:
            result["actual_model"] = getattr(chunk, "model", None) or result["actual_model"]
            usage = getattr(chunk, "usage", None) or usage
            choices = getattr(chunk, "choices", None) or []
            if choices:
                content = getattr(choices[0].delta, "content", None)
                if content:
                    pieces.append(content)

        result["usage"] = _validated_usage(usage)
        usage_complete = result["usage"]["status"] == "complete"
        if usage_complete:
            input_tokens = result["usage"]["input_tokens"]
            output_tokens = result["usage"]["output_tokens"]
            actual_cost = (
                Decimal(input_tokens)
                * Decimal(environment["OPENAI_INPUT_USD_PER_MILLION"])
                + Decimal(output_tokens)
                * Decimal(environment["OPENAI_OUTPUT_USD_PER_MILLION"])
            ) / Decimal(1_000_000)
            result["cost"] = {
                "status": "operator_attested_rate_calculation",
                "actual_cost_usd": float(
                    actual_cost.quantize(Decimal("0.00000001"))
                ),
                "account_hard_limit": False,
            }
            input_exceeded = input_tokens > PROBE_INPUT_TOKEN_ESTIMATE
            output_exceeded = output_tokens > PROBE_OUTPUT_TOKEN_CEILING
            result["budget_observation"] = {
                "input_tokens": {
                    "estimate": PROBE_INPUT_TOKEN_ESTIMATE,
                    "actual": input_tokens,
                    "status": ("estimate_exceeded" if input_exceeded
                               else "within_estimate"),
                },
                "output_tokens": {
                    "requested_limit": PROBE_OUTPUT_TOKEN_CEILING,
                    "actual": output_tokens,
                    "status": ("requested_limit_exceeded" if output_exceeded
                               else "within_requested_limit"),
                },
            }
            result["output_limit_compatibility"] = {
                "parameter": "max_tokens",
                "status": ("accepted_but_reported_usage_exceeded" if output_exceeded
                           else "accepted_within_observed_limit"),
            }
            if input_exceeded:
                result["blockers"].append("input_token_estimate_exceeded")
            if output_exceeded:
                result["blockers"].append("output_token_limit_exceeded")
        if result["actual_model"] != manifest_requested_model:
            result["blockers"].append("actual_model_mismatch")
        if result["usage"]["status"] == "invalid":
            result["blockers"].append("usage_reporting_invalid")
            result["output_limit_compatibility"] = {
                "parameter": "max_tokens",
                "status": "accepted_but_usage_invalid",
            }
        elif not usage_complete:
            result["blockers"].append("usage_reporting_incomplete")
            result["output_limit_compatibility"] = {
                "parameter": "max_tokens",
                "status": "accepted_but_usage_unavailable",
            }
        try:
            payload = ProbePayload.model_validate_json("".join(pieces))
            if payload != ProbePayload(ok=True, probe="p5r2"):
                raise ValueError("unexpected probe payload")
            result["structured_json"] = payload.model_dump()
        except (ValueError, TypeError):
            result["blockers"].append("structured_json_invalid")
        result["status"] = "passed" if not result["blockers"] else "failed"
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        result["error"] = {
            "type": type(exc).__name__,
            "status_code": status_code if isinstance(status_code, int) else None,
        }
        result["blockers"].append("provider_request_failed")
    finally:
        result["duration_ms"] = round((clock() - started) * 1000)
    return result


def _effective_environment(path: Path) -> dict[str, str]:
    values = {key: value for key, value in os.environ.items()}
    if path.is_file():
        values.update({key: value or "" for key, value in dotenv_values(path).items()})
    return values


def _manifest_model(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProbeManifestError(
            "manifest_file_not_found", "ManifestFileNotFound"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ProbeManifestError("manifest_read_failed", "ManifestReadError") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeManifestError("manifest_json_invalid", "ManifestJsonInvalid") from exc
    if not isinstance(payload, dict):
        raise ProbeManifestError("manifest_json_invalid", "ManifestJsonInvalid")
    model = payload.get("requested_model")
    if not isinstance(model, str) or not model.strip():
        raise ProbeManifestError(
            "manifest_requested_model_invalid", "ManifestModelInvalid"
        )
    if model != FROZEN_MODEL:
        raise ProbeManifestError(
            "manifest_requested_model_mismatch", "ManifestModelMismatch"
        )
    return model


def _blocked_probe_result(blockers: list[str], error_type: str) -> dict:
    return {
        "probe_schema_version": PROBE_SCHEMA_VERSION,
        "status": "failed",
        "network_attempted": False,
        "model_calls": 0,
        "requested_model": FROZEN_MODEL,
        "actual_model": None,
        "structured_json": None,
        "usage": {
            "status": "not_applicable",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        "cost": {
            "status": "not_incurred",
            "actual_cost_usd": 0,
            "account_hard_limit": False,
        },
        "duration_ms": 0,
        "blockers": blockers,
        "error": {"type": error_type, "status_code": None},
    }


def _write_report(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _print_summary(output: str | None, execute: bool, result: dict) -> None:
    print(json.dumps({
        "output": output,
        "execution_mode": "paid_probe" if execute else "configuration_check_only",
        "model_calls": result.get("model_calls", result.get("probe", {}).get("model_calls", 0)),
        "network_attempted": result.get(
            "network_attempted", result.get("probe", {}).get("network_attempted", False)
        ),
        "status": result.get("status", "configuration_checked"),
    }, ensure_ascii=False))


def main(argv: list[str] | None = None, *, client_factory: Callable[..., object] = OpenAI) -> int:
    parser = argparse.ArgumentParser(
        description="Check P5-R2 model configuration; never calls the API unless explicitly authorized"
    )
    parser.add_argument("--environment-file", type=Path, default=Path(".env.local"))
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("docs/examples/p5-regression/evaluation-manifest.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-ack", default="")
    args = parser.parse_args(argv)

    pending = _blocked_probe_result(["probe_not_started"], "ProbeNotStarted")
    try:
        _write_report(args.output, pending)
    except OSError:
        unavailable = _blocked_probe_result(
            ["report_output_unavailable"], "ReportOutputUnavailable"
        )
        _print_summary(None, args.execute, unavailable)
        return 2

    try:
        manifest_model = _manifest_model(args.manifest)
    except ProbeManifestError as exc:
        result = _blocked_probe_result([exc.blocker], exc.error_type)
        _write_report(args.output, result)
        _print_summary(str(args.output), args.execute, result)
        return 2

    environment = _effective_environment(args.environment_file)
    exit_code = 0
    if args.execute:
        try:
            result = run_capability_probe(
                environment,
                manifest_model,
                args.authorization_ack,
                client_factory,
            )
            exit_code = 0 if result["status"] == "passed" else 1
        except ProbeAuthorizationError:
            result = _blocked_probe_result(
                ["probe_authorization_missing"], "ProbeAuthorizationError"
            )
            exit_code = 2
        except ProbeConfigurationError:
            configuration = build_configuration_report(environment, manifest_model)
            result = _blocked_probe_result(
                configuration["blockers"], "ProbeConfigurationError"
            )
            exit_code = 2
    else:
        result = build_configuration_report(environment, manifest_model)
    _write_report(args.output, result)
    _print_summary(str(args.output), args.execute, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
