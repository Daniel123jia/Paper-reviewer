from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.p5c.build_manifest import build_manifest
from scripts.p5c.contracts import VersionedManifest
from scripts.p5c.preflight import (
    PreflightError, create_execution_lock, validate_preflight,
)


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "docs/examples/p5-regression/evaluation-manifest.json"
NEW = ROOT / "docs/examples/p5-c-v2/evaluation-manifest.json"
OLD_SHA = "cb312f2b2f2d89157104af93586dfbecf0827b8021137e12a00c6c0202901289"
REFERENCE_SHAS = {
    "sardet100k-neurips2024": "c1296fc1b8c0decc772fbd5e4eddabab489e1d2aea91ac8aad11baf23ed36471",
    "sam-iclr2021": "77eaa70cdd6b6e2440d9d8b09c08a2dc01e15b0d14692ae72bf5231e088dfea5",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_old_assets_unchanged_and_versions_separate():
    assert digest(OLD) == OLD_SHA
    with pytest.raises(ValidationError):
        VersionedManifest.model_validate_json(OLD.read_bytes())
    manifest = build_manifest(root=ROOT)
    assert manifest.manifest_schema_version == "2.0"
    assert manifest.evaluation_version == "p5-c/v2.0"
    assert manifest.requested_model == "gpt-6.1-sol"
    assert len(manifest.prompt_inventory) == 30
    assert {paper.paper_id for paper in manifest.papers} == set(REFERENCE_SHAS)
    assert manifest.scopes["c1_trial"].report_kinds == ["internal:zh"]
    assert len(manifest.scopes["full_p5"].report_kinds) == 4
    assert manifest.model_dump_json(indent=2) + "\n" == NEW.read_text()
    assert "scientific_question" not in NEW.read_text()
    assert "OPENAI_API_KEY" not in NEW.read_text()
    assert "Secret blind question" not in NEW.read_text()


@pytest.fixture
def fixture_assets(tmp_path: Path, monkeypatch):
    import scripts.p5c.preflight as preflight

    manifest = build_manifest(root=ROOT).model_copy(deep=True)
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "test.v1.txt").write_text("fixed", encoding="utf-8")
    from scripts.p5.environment import prompt_inventory
    manifest.prompt_inventory = prompt_inventory(prompt_dir)
    paper_paths = {}
    reference_paths = {}
    frozen = {}
    for paper in manifest.papers:
        pdf = tmp_path / f"{paper.paper_id}.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake " + paper.paper_id.encode())
        paper.pdf_sha256 = digest(pdf)
        paper_paths[paper.paper_id] = pdf
        ref = tmp_path / f"{paper.paper_id}.json"
        issue_ids = ([f"h1-r{i}" for i in range(1, 7)] if paper.paper_id.startswith("sardet")
                     else [f"h2-r{i}" for i in range(1, 7)])
        ref.write_text(json.dumps({
            "reference_schema_version": "1.0", "paper_id": paper.paper_id,
            "pdf_sha256": paper.pdf_sha256, "created_at": "2026-11-01T00:00:00Z",
            "provenance": "non_expert_source_grounded_reference",
            "frozen_before_etpaper_output": True, "etpaper_output_viewed": False,
            "limitations": [], "issues": [{
                "reference_issue_id": issue_id, "scientific_question": "Secret blind question body long enough",
                "importance": "major", "reference_category": "clarification",
                "evidence": [{"pdf_page": 1, "object_label": "Table 1", "source_note": "source"}],
                "verification_condition": "Check against the original PDF",
                "current_assessment": "open_question",
            } for issue_id in issue_ids],
        }), encoding="utf-8")
        reference_paths[paper.paper_id] = ref
        frozen[paper.paper_id] = {"file_sha256": digest(ref), "issue_ids": tuple(issue_ids)}
        manifest.references[paper.paper_id].file_sha256 = digest(ref)
    monkeypatch.setattr(preflight, "FROZEN_REFERENCE_CONTRACT", frozen)
    monkeypatch.setattr(preflight, "FROZEN_PDF_CONTRACT",
                        {paper.paper_id: paper.pdf_sha256 for paper in manifest.papers})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    facts = {"commit": "a" * 40, "tree": "b" * 40, "clean": True,
             "baseline_is_ancestor": True}
    lock = create_execution_lock(manifest_path, facts, {}, {})
    return {"manifest_path": manifest_path, "prompt_directory": prompt_dir,
            "paper_paths": paper_paths, "reference_paths": reference_paths,
            "git_facts": facts, "lock": lock,
            "lock_sha256": hashlib.sha256((json.dumps(lock, sort_keys=True) + "\n").encode()).hexdigest(),
            "process_environment": {}, "environment_file_values": {}}


def test_preflight_passes_without_model_client_or_blind_text(fixture_assets, monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **_: pytest.fail("model client constructed"))
    result = validate_preflight(**fixture_assets)
    assert result["assets_verified"] is True
    assert result["ready_for_paid_run"] is False
    assert result["model_calls"] == 0
    assert "Secret blind question" not in json.dumps(result)


@pytest.mark.parametrize("change", ["commit", "tree", "clean", "baseline_is_ancestor"])
def test_git_identity_and_dirty_fail_closed(fixture_assets, change):
    fixture_assets["git_facts"] = dict(fixture_assets["git_facts"])
    fixture_assets["git_facts"][change] = False if change in {"clean", "baseline_is_ancestor"} else "c" * 40
    with pytest.raises(PreflightError):
        validate_preflight(**fixture_assets)


@pytest.mark.parametrize("asset", ["prompt", "pdf", "reference"])
def test_asset_tampering_fails_closed(fixture_assets, asset):
    if asset == "prompt":
        path = fixture_assets["prompt_directory"] / "test.v1.txt"
    else:
        path = next(iter(fixture_assets["paper_paths" if asset == "pdf" else "reference_paths"].values()))
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(PreflightError):
        validate_preflight(**fixture_assets)


def test_manifest_and_scope_tampering_fails_closed(fixture_assets):
    path = fixture_assets["manifest_path"]
    data = json.loads(path.read_text())
    data["scopes"]["c1_trial"]["report_kinds"].append("author:zh")
    path.write_text(json.dumps(data))
    with pytest.raises(PreflightError):
        validate_preflight(**fixture_assets)


@pytest.mark.parametrize("field,value", [
    ("schema_versions", {"p4_writing_result": "1.1"}),
    ("requested_model", "gpt-5.6-sol"),
    ("scientific_baseline", {"commit": "c" * 40, "tree": "b" * 40}),
])
def test_frozen_manifest_fields_rejected(fixture_assets, field, value):
    path = fixture_assets["manifest_path"]
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(PreflightError):
        validate_preflight(**fixture_assets)


def test_reviewer_lock_sha_is_required(fixture_assets):
    fixture_assets["lock_sha256"] = "0" * 64
    with pytest.raises(PreflightError, match="execution_lock_fingerprint_mismatch"):
        validate_preflight(**fixture_assets)


def test_runtime_schema_drift_fails_before_execution(fixture_assets, monkeypatch):
    import scripts.p5c.preflight as preflight
    monkeypatch.setattr(preflight, "runtime_schema_versions",
                        lambda: {"source_bundle": "9.9"})
    with pytest.raises(PreflightError, match="runtime_schema_mismatch"):
        validate_preflight(**fixture_assets)


def test_reference_metadata_is_checked_not_exposed(fixture_assets):
    path = next(iter(fixture_assets["reference_paths"].values()))
    data = json.loads(path.read_text())
    data["etpaper_output_viewed"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(PreflightError) as error:
        validate_preflight(**fixture_assets)
    assert "Secret blind question" not in str(error.value)


def test_paper_metadata_cannot_be_relabelled(fixture_assets):
    path = fixture_assets["manifest_path"]
    data = json.loads(path.read_text())
    data["papers"][0]["version"] = "unreviewed replacement"
    path.write_text(json.dumps(data))
    fixture_assets["lock"]["manifest_sha256"] = digest(path)
    fixture_assets["lock_sha256"] = hashlib.sha256(
        (json.dumps(fixture_assets["lock"], sort_keys=True) + "\n").encode()).hexdigest()
    with pytest.raises(PreflightError):
        validate_preflight(**fixture_assets)


def test_model_source_conflict_is_redacted(fixture_assets):
    fixture_assets["process_environment"] = {"OPENAI_MODEL": "wrong-model", "OPENAI_API_KEY": "secret-A"}
    fixture_assets["environment_file_values"] = {"OPENAI_MODEL": "gpt-6.1-sol", "OPENAI_API_KEY": "secret-B"}
    with pytest.raises(PreflightError) as error:
        validate_preflight(**fixture_assets)
    assert "secret-A" not in str(error.value)
    assert "secret-B" not in str(error.value)
    assert "wrong-model" not in str(error.value)


def test_lock_creation_rejects_dirty_or_conflicting_sources(fixture_assets):
    facts = dict(fixture_assets["git_facts"], clean=False)
    with pytest.raises(PreflightError):
        create_execution_lock(fixture_assets["manifest_path"], facts, {}, {})
    with pytest.raises(PreflightError):
        create_execution_lock(fixture_assets["manifest_path"], fixture_assets["git_facts"],
                              {"OPENAI_BASE_URL": "https://a.example/v1"},
                              {"OPENAI_BASE_URL": "https://b.example/v1"})
