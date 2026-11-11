"""Offline identity and asset validation for P5-C. This module cannot call a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Mapping
from typing import get_args

from dotenv import dotenv_values
from pydantic import ValidationError

from scripts.p5.contracts import EvaluationManifest
from scripts.p5.environment import prompt_inventory
from scripts.p5c.build_manifest import (
    BASELINE_COMMIT, BASELINE_TREE, HOLDOUT_IDS, OLD_MANIFEST_SHA256,
    REFERENCE_CONTRACT, SCHEMA_VERSIONS,
)
from scripts.p5c.contracts import ExecutionLock, VersionedManifest


class PreflightError(ValueError):
    """Stable, redacted validation failure. Never attach source values."""


FROZEN_PDF_CONTRACT = {
    HOLDOUT_IDS[0]: "3f4e6cddbf32524853f3493a5e7975490b8ffeae274cf139acf2b576736998b9",
    HOLDOUT_IDS[1]: "73ace22a61c52152fc84a94239cb3ea5f9fa9e753aa8ef5995f277365e25b764",
}
FROZEN_REFERENCE_CONTRACT = {
    paper_id: {"file_sha256": sha, "issue_ids": tuple(issue_ids)}
    for paper_id, (_, sha, issue_ids) in REFERENCE_CONTRACT.items()
}
MODEL_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_private_json(path: Path, value: Mapping) -> None:
    """Create a private audit file exactly once, without a check/write race."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _sources(process: Mapping[str, str | None], file: Mapping[str, str | None]) -> None:
    relevant = set(MODEL_KEYS) | {key for key in set(process) | set(file)
                                  if key.startswith(("P2A_", "P2B_", "P2C_", "P3_", "P4_"))
                                  or key == "PAPER_REVIEW_WORKER_COUNT"}
    for key in relevant:
        left, right = process.get(key), file.get(key)
        if left and right and left.strip() and right.strip() and left != right:
            raise PreflightError(f"configuration_source_conflict:{key}")


def _manifest(path: Path) -> VersionedManifest:
    try:
        manifest = VersionedManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise PreflightError("p5c_manifest_invalid") from None
    if (manifest.scientific_baseline.commit != BASELINE_COMMIT or
            manifest.scientific_baseline.tree != BASELINE_TREE or
            manifest.schema_versions != SCHEMA_VERSIONS or
            {paper.paper_id: paper.pdf_sha256 for paper in manifest.papers} != FROZEN_PDF_CONTRACT or
            {key: (ref.file_sha256, tuple(ref.issue_ids)) for key, ref in manifest.references.items()}
            != {key: (value["file_sha256"], tuple(value["issue_ids"]))
                for key, value in FROZEN_REFERENCE_CONTRACT.items()}):
        raise PreflightError("p5c_frozen_contract_mismatch")
    historical_path = (Path(__file__).resolve().parents[2] /
                       "docs/examples/p5-regression/evaluation-manifest.json")
    try:
        if _sha(historical_path) != OLD_MANIFEST_SHA256:
            raise PreflightError("historical_manifest_fingerprint_mismatch")
        historical = EvaluationManifest.model_validate_json(historical_path.read_bytes())
    except (OSError, ValidationError):
        raise PreflightError("historical_manifest_invalid") from None
    historical_papers = {paper.paper_id: paper for paper in historical.papers}
    for paper in manifest.papers:
        candidate = paper.model_dump(exclude={"pdf_sha256"})
        accepted = historical_papers[paper.paper_id].model_dump(exclude={"pdf_sha256"})
        if candidate != accepted:
            raise PreflightError("holdout_paper_identity_mismatch")
    return manifest


def runtime_schema_versions() -> dict[str, str]:
    """Inspect currently imported contracts; the lock also binds their exact code tree."""
    from backend.source_bundle import SCHEMA_VERSION as source_bundle
    from backend.p2a_resume import N3_SCHEMA_VERSION as p2a
    from backend.p2b_schema import SCHEMA_VERSION as p2b
    from backend.p2c_schema import SCHEMA_VERSION as p2c
    from backend.p3_schema import SCHEMA_VERSION as p3
    from backend.p4_report_schema import SCHEMA_VERSION as p4, ReportWritingResult

    writing_version = get_args(ReportWritingResult.model_fields["schema_version"].annotation)
    return {
        "source_bundle": source_bundle, "p2a": p2a, "p2b": p2b, "p2c": p2c,
        "p3": f"{p3}/p3.1", "p4_plan_and_evidence": f"{p4}/p4.1",
        "p4_writing_result": writing_version[0] if len(writing_version) == 1 else "invalid",
    }


def _git(facts: Mapping[str, object]) -> None:
    if not facts.get("clean"):
        raise PreflightError("git_worktree_dirty")
    if not facts.get("baseline_is_ancestor"):
        raise PreflightError("scientific_baseline_not_ancestor")
    if not isinstance(facts.get("commit"), str) or len(facts["commit"]) != 40:
        raise PreflightError("git_commit_invalid")
    if not isinstance(facts.get("tree"), str) or len(facts["tree"]) != 40:
        raise PreflightError("git_tree_invalid")


def create_execution_lock(
    manifest_path: Path, git_facts: Mapping[str, object],
    process_environment: Mapping[str, str | None],
    environment_file_values: Mapping[str, str | None],
) -> dict:
    """Create a candidate lock *after* an execution commit exists; not paid authorization."""
    _git(git_facts)
    _sources(process_environment, environment_file_values)
    manifest = _manifest(Path(manifest_path))
    return ExecutionLock(
        lock_schema_version="1.0", evaluation_version=manifest.evaluation_version,
        asset_version=manifest.asset_version, manifest_sha256=_sha(Path(manifest_path)),
        execution_commit=git_facts["commit"], execution_tree=git_facts["tree"],
    ).model_dump()


def validate_preflight(
    *, manifest_path: Path, prompt_directory: Path,
    paper_paths: Mapping[str, Path], reference_paths: Mapping[str, Path],
    git_facts: Mapping[str, object], lock_bytes: bytes,
    lock_sha256: str, process_environment: Mapping[str, str | None],
    environment_file_values: Mapping[str, str | None],
) -> dict:
    _git(git_facts)
    _sources(process_environment, environment_file_values)
    manifest_path = Path(manifest_path)
    manifest = _manifest(manifest_path)
    if runtime_schema_versions() != manifest.schema_versions:
        raise PreflightError("runtime_schema_mismatch")
    if hashlib.sha256(lock_bytes).hexdigest() != lock_sha256:
        raise PreflightError("execution_lock_fingerprint_mismatch")
    try:
        binding = ExecutionLock.model_validate_json(lock_bytes)
    except ValidationError:
        raise PreflightError("execution_lock_invalid") from None
    if (binding.manifest_sha256 != _sha(manifest_path) or
            binding.evaluation_version != manifest.evaluation_version or
            binding.asset_version != manifest.asset_version or
            binding.execution_commit != git_facts["commit"] or
            binding.execution_tree != git_facts["tree"]):
        raise PreflightError("execution_identity_mismatch")
    configured_model = process_environment.get("OPENAI_MODEL") or environment_file_values.get("OPENAI_MODEL")
    if configured_model and configured_model != manifest.requested_model:
        raise PreflightError("requested_model_mismatch:OPENAI_MODEL")
    # No key is required to inspect public assets; credentials are checked at paid-stage authorization.
    if prompt_inventory(Path(prompt_directory)) != manifest.prompt_inventory:
        raise PreflightError("prompt_inventory_mismatch")
    if set(paper_paths) != set(HOLDOUT_IDS) or set(reference_paths) != set(HOLDOUT_IDS):
        raise PreflightError("holdout_asset_set_mismatch")
    papers = {paper.paper_id: paper for paper in manifest.papers}
    for paper_id in HOLDOUT_IDS:
        pdf = Path(paper_paths[paper_id])
        reference_path = Path(reference_paths[paper_id])
        try:
            if _sha(pdf) != papers[paper_id].pdf_sha256:
                raise PreflightError(f"pdf_fingerprint_mismatch:{paper_id}")
            if _sha(reference_path) != manifest.references[paper_id].file_sha256:
                raise PreflightError(f"reference_fingerprint_mismatch:{paper_id}")
            # Only extract frozen identity fields. Never materialize scientific questions
            # in the preflight result or pass them to a model input.
            reference = json.loads(reference_path.read_bytes())
        except (OSError, ValidationError, ValueError) as exc:
            if isinstance(exc, PreflightError):
                raise
            raise PreflightError(f"holdout_asset_invalid:{paper_id}") from None
        identity = manifest.references[paper_id]
        try:
            valid_identity = (
                reference["reference_schema_version"] == "1.0" and
                reference["paper_id"] == paper_id and
                reference["pdf_sha256"] == papers[paper_id].pdf_sha256 and
                reference["provenance"] == identity.provenance and
                reference["frozen_before_etpaper_output"] is True and
                reference["etpaper_output_viewed"] is False and
                [issue["reference_issue_id"] for issue in reference["issues"]] == identity.issue_ids
            )
        except (KeyError, TypeError, ValueError):
            valid_identity = False
        if not valid_identity:
            raise PreflightError(f"reference_identity_mismatch:{paper_id}")
    return {
        "preflight_schema_version": "2.0", "evaluation_version": manifest.evaluation_version,
        "asset_version": manifest.asset_version, "execution_commit": binding.execution_commit,
        "execution_tree": binding.execution_tree, "manifest_sha256": binding.manifest_sha256,
        "assets_verified": True, "blind_reference_content_exposed": False,
        "network_attempted": False, "model_calls": 0, "ready_for_paid_run": False,
        "reason": "separate stage authorization and budget approval required",
    }


def git_facts(root: Path) -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    try:
        commit = run("rev-parse", "HEAD")
        tree = run("rev-parse", "HEAD^{tree}")
        clean = not run("status", "--porcelain")
        ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                                   BASELINE_COMMIT, "HEAD"], capture_output=True).returncode == 0
    except (OSError, subprocess.CalledProcessError):
        raise PreflightError("git_identity_unavailable") from None
    return {"commit": commit, "tree": tree, "clean": clean,
            "baseline_is_ancestor": ancestor}


def main() -> int:
    parser = argparse.ArgumentParser(description="P5-C offline frozen-asset preflight")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--paper", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--expected-lock-sha256")
    parser.add_argument("--create-lock", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        file_values = (dotenv_values(args.environment_file) if args.environment_file else {})
        if args.environment_file and not args.environment_file.is_file():
            raise PreflightError("environment_file_unavailable")
        facts = git_facts(args.root)
        if args.create_lock:
            value = create_execution_lock(args.manifest, facts, os.environ, file_values)
            _write_private_json(args.create_lock, value)
            print(json.dumps({"lock_sha256": _sha(args.create_lock), "model_calls": 0}))
            return 0
        if not args.lock or not args.expected_lock_sha256:
            raise PreflightError("reviewed_execution_lock_required")
        paper_paths = dict(item.split("=", 1) for item in args.paper)
        manifest = _manifest(args.manifest)
        result = validate_preflight(
            manifest_path=args.manifest, prompt_directory=args.root / "backend/prompts",
            paper_paths={key: Path(value) for key, value in paper_paths.items()},
            reference_paths={key: args.root / ref.path for key, ref in manifest.references.items()},
            git_facts=facts, lock_bytes=args.lock.read_bytes(),
            lock_sha256=args.expected_lock_sha256,
            process_environment=os.environ, environment_file_values=file_values,
        )
        if args.output:
            _write_private_json(args.output, result)
        print(json.dumps(result))
        return 0
    except (PreflightError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, PreflightError) else "preflight_input_invalid"
        print(json.dumps({"status": "blocked", "error_code": code,
                          "network_attempted": False, "model_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
