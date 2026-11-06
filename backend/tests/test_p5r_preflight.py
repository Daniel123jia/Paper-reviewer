from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.p5.contracts import (
    BlindReferenceSet,
    EvaluationManifest,
    PaperBudget,
    PaperSpec,
)
from scripts.p5r.preflight import PreflightError, build_preflight


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
PAPER_IDS = ("shaw2018", "sardet100k-neurips2024", "sam-iclr2021")
FROZEN_MANIFEST_COMMIT = "c4b613f486b4db6f118dc0ef99b7c0c103676843"
FROZEN_REFERENCE_DIGESTS = {
    "sardet100k-neurips2024": "c1296fc1b8c0decc772fbd5e4eddabab489e1d2aea91ac8aad11baf23ed36471",
    "sam-iclr2021": "77eaa70cdd6b6e2440d9d8b09c08a2dc01e15b0d14692ae72bf5231e088dfea5",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paper(paper_id: str, digest: str) -> PaperSpec:
    return PaperSpec(
        paper_id=paper_id,
        title=paper_id,
        group="historical_regression" if paper_id == "shaw2018" else "holdout",
        official_pdf_url=f"https://example.org/{paper_id}.pdf",
        pdf_sha256=digest,
        version="frozen-test-version",
        reference_status="frozen",
    )


def _budget() -> PaperBudget:
    return PaperBudget(
        max_calls=90,
        max_input_tokens=1_200_000,
        max_output_tokens=220_000,
        max_duration_seconds=10_800,
        max_retries=2,
        max_cost_usd=None,
        price_source="unknown; token and call ceilings enforced",
    )


def _reference(paper_id: str, digest: str) -> BlindReferenceSet:
    return BlindReferenceSet(
        paper_id=paper_id,
        pdf_sha256=digest,
        created_at=NOW,
        provenance="non_expert_source_grounded_reference",
        frozen_before_etpaper_output=True,
        etpaper_output_viewed=False,
        limitations=["test reference only"],
        issues=[{
            "reference_issue_id": f"{paper_id}-r1",
            "scientific_question": "Does the evidence support the stated scientific conclusion?",
            "importance": "moderate",
            "reference_category": "clarification",
            "evidence": [{
                "pdf_page": 1,
                "object_label": "test object",
                "source_note": "Frozen source-grounded test evidence.",
            }],
            "verification_condition": "Check the claim against the frozen source.",
            "current_assessment": "open_question",
        }],
    )


def _fixtures(tmp_path: Path):
    pdf_paths = {}
    for index, paper_id in enumerate(PAPER_IDS):
        path = tmp_path / f"paper-{index}.pdf"
        path.write_bytes(f"%PDF-1.4\n{paper_id}\n%%EOF".encode())
        pdf_paths[paper_id] = path

    papers = [_paper(paper_id, _digest(pdf_paths[paper_id])) for paper_id in PAPER_IDS]
    manifest = EvaluationManifest(
        baseline_commit=FROZEN_MANIFEST_COMMIT,
        code_commit=FROZEN_MANIFEST_COMMIT,
        created_at=NOW,
        operating_system="test-os",
        python_version="3.11",
        node_version="22",
        schema_versions={},
        prompt_inventory=[],
        requested_model="requested-test-model",
        provider_label="default_application_endpoint",
        papers=papers,
        budgets={paper.paper_id: _budget() for paper in papers},
        paper_id_aliases={
            "sardet-legacy": "sardet100k-neurips2024",
            "sam-legacy": "sam-iclr2021",
        },
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    reference_paths = {}
    for paper_id in PAPER_IDS[1:]:
        path = tmp_path / f"{paper_id}-reference.json"
        path.write_text(
            _reference(paper_id, _digest(pdf_paths[paper_id])).model_dump_json(indent=2),
            encoding="utf-8",
        )
        reference_paths[paper_id] = path
    return manifest_path, pdf_paths, reference_paths


def _reference_contract(reference_paths: dict[str, Path]) -> dict[str, dict]:
    contract = {}
    for paper_id, path in reference_paths.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        contract[paper_id] = {
            "file_sha256": _digest(path),
            "issue_ids": [item["reference_issue_id"] for item in payload["issues"]],
        }
    return contract


def test_repository_manifest_references_aliases_and_budgets_are_frozen_consistently():
    manifest = EvaluationManifest.model_validate_json(Path(
        "docs/examples/p5-regression/evaluation-manifest.json"
    ).read_text(encoding="utf-8"))
    papers = {paper.paper_id: paper for paper in manifest.papers}

    assert set(PAPER_IDS).issubset(papers)
    assert manifest.resolve_paper_id("sardet-100k-neurips2024") == PAPER_IDS[1]
    assert manifest.resolve_paper_id("sam-arxiv-2010-01412v3") == PAPER_IDS[2]
    for paper_id in PAPER_IDS:
        assert manifest.budgets[paper_id] == _budget()
    for paper_id, path in {
        PAPER_IDS[1]: Path("docs/examples/p5-holdout-1/blind-reference.json"),
        PAPER_IDS[2]: Path("docs/examples/p5-holdout-2/blind-reference.json"),
    }.items():
        reference = BlindReferenceSet.model_validate_json(path.read_text(encoding="utf-8"))
        assert reference.paper_id == paper_id
        assert reference.pdf_sha256 == papers[paper_id].pdf_sha256
        assert reference.frozen_before_etpaper_output is True
        assert reference.etpaper_output_viewed is False
        assert _digest(path) == FROZEN_REFERENCE_DIGESTS[paper_id]


def test_public_preflight_names_the_clean_commit_that_was_actually_executed():
    payload = json.loads(Path("docs/examples/p5r/preflight.json").read_text(encoding="utf-8"))
    snapshot = payload["source_snapshot"]

    assert payload["preflight_schema_version"] == "1.1"
    assert payload["baseline_is_ancestor"] is True
    assert payload["current_commit"] == snapshot["head_commit"]
    assert snapshot["kind"] == "committed_source_validation"
    assert snapshot["includes_uncommitted_changes"] is False
    assert payload["frozen_manifest"]["prompt_inventory_verified"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", payload["current_commit"])


def test_preflight_verifies_canonical_ids_hashes_references_and_budget(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)

    result = build_preflight(
        manifest_path=manifest_path,
        paper_paths=pdf_paths,
        reference_paths=reference_paths,
        environment={
            "model_api_key": "not_configured",
            "model_endpoint": "default_application_endpoint",
            "requested_model": "requested-test-model",
            "python_version": "3.11",
        },
        current_commit="1" * 40,
        baseline_is_ancestor=True,
        working_tree_dirty=True,
        generated_at=NOW,
        frozen_reference_contract=_reference_contract(reference_paths),
    )

    assert result["preflight_schema_version"] == "1.1"
    assert [paper["paper_id"] for paper in result["papers"]] == list(PAPER_IDS)
    assert all(paper["hash_verified"] for paper in result["papers"])
    assert result["reference_sets"]["sardet100k-neurips2024"]["identity_verified"]
    assert result["reference_sets"]["sam-iclr2021"]["identity_verified"]
    assert result["budget"]["per_paper"]["max_calls"] == 90
    assert result["budget"]["aggregate"]["max_calls"] == 270
    assert result["budget"]["aggregate"]["max_input_tokens"] == 3_600_000
    assert result["budget"]["aggregate"]["max_output_tokens"] == 660_000
    assert result["model_environment"]["actual_model"] is None
    assert result["model_environment"]["capability_probe"] == "not_run_pending_authorization"
    assert result["model_environment"]["context_window"] == "unknown_pending_provider_verification"
    assert result["model_environment"]["model_output_limit"] == \
        "unknown_pending_provider_verification"
    assert result["frozen_manifest"]["prompt_inventory"] == []
    assert result["source_snapshot"] == {
        "kind": "pre_commit_worktree_validation",
        "head_commit": "1" * 40,
        "includes_uncommitted_changes": True,
        "note": "head_commit identifies the checked-out parent; validation included uncommitted P5-R changes",
    }
    assert result["ready_for_paid_run"] is False


def test_preflight_rejects_execution_commit_without_p5r_baseline_ancestor(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)

    with pytest.raises(PreflightError, match="P5-R baseline is not an ancestor"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=False,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


@pytest.mark.parametrize("field", ["baseline_commit", "code_commit"])
def test_preflight_rejects_changed_frozen_manifest_commit(tmp_path, field):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = "f" * 40
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreflightError, match="frozen manifest commit mismatch"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


def test_preflight_rejects_requested_model_that_differs_from_manifest(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)

    with pytest.raises(PreflightError, match="requested model mismatch"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "unapproved-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


def test_preflight_rejects_prompt_inventory_that_differs_from_manifest(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    prompt_directory = tmp_path / "prompts"
    prompt_directory.mkdir()
    (prompt_directory / "unauthorized.v1.txt").write_text(
        "changed prompt", encoding="utf-8"
    )

    with pytest.raises(PreflightError, match="prompt inventory mismatch"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
            prompt_directory=prompt_directory,
        )


def test_preflight_fails_closed_on_pdf_hash_mismatch(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    pdf_paths["shaw2018"].write_bytes(b"different PDF bytes")

    with pytest.raises(PreflightError, match="SHA-256 mismatch.*shaw2018"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


def test_preflight_rejects_alias_instead_of_canonical_execution_id(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    pdf_paths["sardet-legacy"] = pdf_paths.pop("sardet100k-neurips2024")

    with pytest.raises(PreflightError, match="canonical paper IDs"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


def test_preflight_fails_when_blind_reference_identity_differs(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    bad = json.loads(reference_paths["sam-iclr2021"].read_text(encoding="utf-8"))
    bad["pdf_sha256"] = "f" * 64
    reference_paths["sam-iclr2021"].write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(PreflightError, match="blind reference identity mismatch.*sam-iclr2021"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=_reference_contract(reference_paths),
        )


def test_preflight_public_record_never_serializes_secrets_or_absolute_paths(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    result = build_preflight(
        manifest_path=manifest_path,
        paper_paths=pdf_paths,
        reference_paths=reference_paths,
        environment={
            "model_api_key": "configured",
            "model_endpoint": "configured_custom_endpoint",
            "requested_model": "requested-test-model",
            "python_version": "3.11",
            "secret_for_test": "configured-secret-sentinel",
            "endpoint_for_test": "private-host-sentinel",
        },
        current_commit="1" * 40,
        baseline_is_ancestor=True,
        generated_at=NOW,
        frozen_reference_contract=_reference_contract(reference_paths),
    )
    encoded = json.dumps(result)

    assert "configured-secret-sentinel" not in encoded
    assert "private-host-sentinel" not in encoded
    assert str(tmp_path) not in encoded
    assert result["model_environment"]["api_key_status"] == "configured"
    assert result["model_environment"]["endpoint_label"] == "configured_custom_endpoint"
    assert "capability_probe_not_run" in result["blockers"]


def test_preflight_rejects_reference_not_frozen_before_output(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    contract = _reference_contract(reference_paths)
    path = reference_paths["sardet100k-neurips2024"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["frozen_before_etpaper_output"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreflightError, match="reference was not frozen before EtPaper output"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=contract,
        )


def test_preflight_rejects_reference_that_viewed_etpaper_output(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    contract = _reference_contract(reference_paths)
    path = reference_paths["sam-iclr2021"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["etpaper_output_viewed"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreflightError, match="blind reference contract invalid.*sam-iclr2021"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=contract,
        )


def test_preflight_rejects_changed_reference_issue_count(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    contract = _reference_contract(reference_paths)
    path = reference_paths["sam-iclr2021"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["issues"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreflightError, match="blind reference contract invalid.*sam-iclr2021"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=contract,
        )


def test_preflight_rejects_rewritten_reference_with_same_issue_ids(tmp_path):
    manifest_path, pdf_paths, reference_paths = _fixtures(tmp_path)
    contract = _reference_contract(reference_paths)
    path = reference_paths["sardet100k-neurips2024"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["issues"][0]["scientific_question"] = \
        "A replacement question with enough words to satisfy the schema but not the freeze."
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreflightError, match="reference content fingerprint mismatch"):
        build_preflight(
            manifest_path=manifest_path,
            paper_paths=pdf_paths,
            reference_paths=reference_paths,
            environment={"requested_model": "requested-test-model"},
            current_commit="1" * 40,
            baseline_is_ancestor=True,
            generated_at=NOW,
            frozen_reference_contract=contract,
        )
