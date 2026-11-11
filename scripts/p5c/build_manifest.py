"""Build the public P5-C candidate asset deterministically; never read blind question text."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from scripts.p5.contracts import EvaluationManifest, PaperBudget
from scripts.p5.environment import prompt_inventory
from scripts.p5c.contracts import Baseline, ReferenceIdentity, Scope, VersionedManifest


OLD_MANIFEST_SHA256 = "cb312f2b2f2d89157104af93586dfbecf0827b8021137e12a00c6c0202901289"
BASELINE_COMMIT = "69752745fe7c4a0a0289c5da75379c79e99e1cf5"
BASELINE_TREE = "da319d0d0b02e7b592955a7c89cafd6a4ebfcea1"
HOLDOUT_IDS = ("sardet100k-neurips2024", "sam-iclr2021")
REFERENCE_CONTRACT = {
    HOLDOUT_IDS[0]: ("docs/examples/p5-holdout-1/blind-reference.json",
                     "c1296fc1b8c0decc772fbd5e4eddabab489e1d2aea91ac8aad11baf23ed36471",
                     [f"h1-r{i}" for i in range(1, 7)]),
    HOLDOUT_IDS[1]: ("docs/examples/p5-holdout-2/blind-reference.json",
                     "77eaa70cdd6b6e2440d9d8b09c08a2dc01e15b0d14692ae72bf5231e088dfea5",
                     [f"h2-r{i}" for i in range(1, 7)]),
}
SCHEMA_VERSIONS = {"source_bundle": "1.0", "p2a": "1.1", "p2b": "1.0",
                   "p2c": "1.1", "p3": "1.1/p3.1", "p4_plan_and_evidence": "1.1/p4.1",
                   "p4_writing_result": "1.0"}


def build_manifest(*, root: Path) -> VersionedManifest:
    root = Path(root)
    old_path = root / "docs/examples/p5-regression/evaluation-manifest.json"
    raw = old_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != OLD_MANIFEST_SHA256:
        raise ValueError("historical P5 manifest fingerprint mismatch")
    old = EvaluationManifest.model_validate_json(raw)
    papers = [paper for paper in old.papers if paper.paper_id in HOLDOUT_IDS]
    if [paper.paper_id for paper in papers] != list(HOLDOUT_IDS):
        raise ValueError("historical holdout identity mismatch")
    references = {
        paper_id: ReferenceIdentity(
            path=path, file_sha256=sha, issue_ids=issue_ids,
            provenance="non_expert_source_grounded_reference",
            frozen_before_etpaper_output=True, etpaper_output_viewed=False,
        )
        for paper_id, (path, sha, issue_ids) in REFERENCE_CONTRACT.items()
    }
    return VersionedManifest(
        manifest_schema_version="2.0", evaluation_version="p5-c/v2.0",
        asset_version="p5-c/v2.0-assets.1",
        scientific_baseline=Baseline(commit=BASELINE_COMMIT, tree=BASELINE_TREE),
        execution_binding="external_lock_after_final_commit",
        requested_model="gpt-6.1-sol", schema_versions=SCHEMA_VERSIONS,
        prompt_inventory=prompt_inventory(root / "backend/prompts"),
        papers=papers, references=references,
        scopes={
            "c1_trial": Scope(paper_count=1, report_kinds=["internal:zh"],
                              stage_authorization="manual_per_paid_stage",
                              budget_policy="explicit_per_stage_and_paper"),
            "full_p5": Scope(paper_count=1,
                             report_kinds=["internal:zh", "internal:en", "author:zh", "author:en"],
                             stage_authorization="separately_authorized_full_run",
                             budget_policy="explicit_per_paper"),
        },
        full_p5_per_paper_budget=PaperBudget(
            max_calls=90, max_input_tokens=1_200_000,
            max_output_tokens=220_000, max_duration_seconds=10_800,
            max_retries=2, max_cost_usd=None,
            price_source="unknown; local ceilings do not cap provider charges"),
        pricing_status="unknown",
        external_material_policy="no_blind_reference_content_in_model_inputs",
        reference_scoring_gate="after_result_seal",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P5-C public candidate manifest")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(root=args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
