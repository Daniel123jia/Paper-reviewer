from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from scripts.p5.contracts import BlindReferenceSet, EvaluationManifest, PaperBudget
from scripts.p5.environment import public_environment_snapshot


P5R_BASELINE = "09c0ef8e0210d5973d4d9d5e82a4ff90a560831c"
P5R_PAPER_IDS = ("shaw2018", "sardet100k-neurips2024", "sam-iclr2021")
P5R_HOLDOUT_IDS = ("sardet100k-neurips2024", "sam-iclr2021")


class PreflightError(ValueError):
    """Raised when a frozen P5-R input fails closed validation."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _budget_dict(budget: PaperBudget) -> dict[str, int]:
    return {
        "max_calls": budget.max_calls,
        "max_input_tokens": budget.max_input_tokens,
        "max_output_tokens": budget.max_output_tokens,
        "max_duration_seconds": budget.max_duration_seconds,
        "max_retries": budget.max_retries,
    }


def _pricing_estimate(environment: Mapping[str, str], aggregate: dict[str, int]) -> dict:
    input_rate = environment.get("OPENAI_INPUT_USD_PER_MILLION")
    output_rate = environment.get("OPENAI_OUTPUT_USD_PER_MILLION")
    if input_rate is None or output_rate is None:
        return {
            "status": "unknown",
            "max_cost_usd": None,
            "reason": "operator pricing is not configured and provider pricing was not inferred",
        }
    try:
        input_decimal = Decimal(input_rate)
        output_decimal = Decimal(output_rate)
        if input_decimal < 0 or output_decimal < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return {
            "status": "unknown",
            "max_cost_usd": None,
            "reason": "operator pricing configuration is invalid",
        }
    cost = (
        Decimal(aggregate["max_input_tokens"]) * input_decimal
        + Decimal(aggregate["max_output_tokens"]) * output_decimal
    ) / Decimal(1_000_000)
    return {
        "status": "operator_configured_estimate",
        "max_cost_usd": float(cost.quantize(Decimal("0.000001"))),
        "reason": "computed from operator-supplied per-million-token rates",
    }


def build_preflight(
    *,
    manifest_path: Path,
    paper_paths: Mapping[str, Path],
    reference_paths: Mapping[str, Path],
    environment: Mapping[str, str],
    current_commit: str,
    baseline_is_ancestor: bool,
    generated_at: datetime | None = None,
    pricing_environment: Mapping[str, str] | None = None,
) -> dict:
    manifest = EvaluationManifest.model_validate_json(
        Path(manifest_path).read_text(encoding="utf-8")
    )
    provided_ids = set(paper_paths)
    expected_ids = set(P5R_PAPER_IDS)
    if provided_ids != expected_ids:
        aliases = provided_ids.intersection(manifest.paper_id_aliases)
        detail = f"; aliases supplied: {sorted(aliases)}" if aliases else ""
        raise PreflightError(
            f"P5-R requires exactly the canonical paper IDs {sorted(expected_ids)}{detail}"
        )

    manifest_papers = {paper.paper_id: paper for paper in manifest.papers}
    missing_manifest_ids = expected_ids.difference(manifest_papers)
    if missing_manifest_ids:
        raise PreflightError(
            f"canonical paper IDs are missing from the manifest: {sorted(missing_manifest_ids)}"
        )

    papers = []
    for paper_id in P5R_PAPER_IDS:
        spec = manifest_papers[paper_id]
        path = Path(paper_paths[paper_id])
        if not path.is_file():
            raise PreflightError(f"PDF is missing for {paper_id}")
        actual_digest = _sha256(path)
        if actual_digest != spec.pdf_sha256:
            raise PreflightError(
                f"SHA-256 mismatch for {paper_id}: frozen input validation failed"
            )
        papers.append({
            "paper_id": paper_id,
            "title": spec.title,
            "version": spec.version,
            "pdf_filename": path.name,
            "pdf_sha256": actual_digest,
            "hash_verified": True,
            "reference_status": spec.reference_status,
        })

    if set(reference_paths) != set(P5R_HOLDOUT_IDS):
        raise PreflightError(
            f"blind references are required for exactly {sorted(P5R_HOLDOUT_IDS)}"
        )
    reference_sets = {}
    for paper_id in P5R_HOLDOUT_IDS:
        reference = BlindReferenceSet.model_validate_json(
            Path(reference_paths[paper_id]).read_text(encoding="utf-8")
        )
        spec = manifest_papers[paper_id]
        if reference.paper_id != paper_id or reference.pdf_sha256 != spec.pdf_sha256:
            raise PreflightError(f"blind reference identity mismatch for {paper_id}")
        reference_sets[paper_id] = {
            "paper_id": reference.paper_id,
            "pdf_sha256": reference.pdf_sha256,
            "identity_verified": True,
            "frozen_before_etpaper_output": reference.frozen_before_etpaper_output,
            "etpaper_output_viewed": reference.etpaper_output_viewed,
            "provenance": reference.provenance,
            "issue_count": len(reference.issues),
        }

    by_paper = {
        paper_id: _budget_dict(manifest.budgets[paper_id]) for paper_id in P5R_PAPER_IDS
    }
    common_budget = by_paper[P5R_PAPER_IDS[0]]
    if any(budget != common_budget for budget in by_paper.values()):
        raise PreflightError("P5-R frozen per-paper budgets are inconsistent")
    aggregate = {
        key: sum(budget[key] for budget in by_paper.values())
        for key in ("max_calls", "max_input_tokens", "max_output_tokens", "max_duration_seconds")
    }
    aggregate["max_retries"] = sum(budget["max_retries"] for budget in by_paper.values())

    api_key_status = environment.get("model_api_key", "not_configured")
    blockers = ["capability_probe_not_run", "actual_model_unknown"]
    if api_key_status != "configured":
        blockers.insert(0, "p5r_runtime_credential_not_configured")

    generated = generated_at or datetime.now(timezone.utc)
    return {
        "preflight_schema_version": "1.0",
        "generated_at": generated.isoformat(),
        "execution_mode": "fresh_model_run_preparation",
        "p5r_baseline_commit": P5R_BASELINE,
        "current_commit": current_commit,
        "baseline_is_ancestor": baseline_is_ancestor,
        "frozen_manifest": {
            "manifest_schema_version": manifest.manifest_schema_version,
            "baseline_commit": manifest.baseline_commit,
            "code_commit": manifest.code_commit,
            "paper_id_aliases": manifest.paper_id_aliases,
            "chatgpt_benchmark_status": manifest.chatgpt_benchmark_status,
        },
        "papers": papers,
        "reference_sets": reference_sets,
        "model_environment": {
            "api_key_status": api_key_status,
            "endpoint_label": environment.get(
                "model_endpoint", "default_application_endpoint"
            ),
            "requested_model": environment.get("requested_model", "gpt-6.1-sol"),
            "actual_model": None,
            "provider_label": environment.get(
                "model_endpoint", "default_application_endpoint"
            ),
            "python_version": environment.get("python_version"),
            "request_timeout_seconds": 180,
            "sdk_max_retries": 0,
            "context_window": "unknown_pending_provider_verification",
            "model_output_limit": "unknown_pending_provider_verification",
            "capability_probe": "not_run_pending_authorization",
            "network_connection": "not_tested_pending_authorization",
            "structured_output": "not_tested",
            "streaming_output": "not_tested",
            "usage_reporting": "not_tested",
        },
        "budget": {
            "per_paper": common_budget,
            "by_paper": by_paper,
            "aggregate": aggregate,
            "cost": _pricing_estimate(pricing_environment or {}, aggregate),
        },
        "assets_verified": True,
        "ready_for_paid_run": False,
        "blockers": blockers,
        "next_authorized_step": "minimal_model_capability_probe",
        "high_cost_run_order": list(P5R_PAPER_IDS),
    }


def _mapping(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected PAPER_ID=PATH")
    paper_id, path = value.split("=", 1)
    if not paper_id or not path:
        raise argparse.ArgumentTypeError("expected PAPER_ID=PATH")
    return paper_id, Path(path)


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", P5R_BASELINE, "HEAD"],
        capture_output=True,
    ).returncode == 0
    return commit, ancestor


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate frozen P5-R inputs without making a model call"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/examples/p5-regression/evaluation-manifest.json"),
    )
    parser.add_argument("--paper", type=_mapping, action="append", required=True)
    parser.add_argument("--reference", type=_mapping, action="append", required=True)
    parser.add_argument("--environment-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.environment_file.is_file():
        load_dotenv(args.environment_file, override=True)
    current_commit, baseline_is_ancestor = _git_state()
    result = build_preflight(
        manifest_path=args.manifest,
        paper_paths=dict(args.paper),
        reference_paths=dict(args.reference),
        environment=public_environment_snapshot(),
        current_commit=current_commit,
        baseline_is_ancestor=baseline_is_ancestor,
        pricing_environment=os.environ,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "assets_verified": result["assets_verified"],
        "ready_for_paid_run": result["ready_for_paid_run"],
        "blockers": result["blockers"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
