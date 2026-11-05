import hashlib

import fitz
import pytest

from backend.p2c_engine import run_p2c
from backend.p3_sources import EvidenceVerifier, validate_p3_inputs
from backend.tests.test_p2c_engine import grouping_model
from backend.tests.test_p2c_normalizer import fixture_review


def frozen_case(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Method and novelty")
        page.insert_text((72, 110), "Experiment result")
        pdf.save(pdf_path)
    bundle, plan, profile, mapping, review = fixture_review()
    bundle["document"]["sha256"] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with fitz.open(pdf_path) as pdf:
        for text, evidence_id in (("Method and novelty", "ev1"),
                                  ("Experiment result", "ev2")):
            box = list(pdf[0].search_for(text)[0])
            next(item for item in bundle["body"] if item["evidence_id"] == evidence_id)["bbox"] = box
    consolidation = run_p2c(bundle, plan, profile, mapping, review,
        "u1", "b1", "c1", grouping_model, "mock", "limited")
    return bundle, plan, profile, mapping, review, consolidation, pdf_path


def test_pdf_sha_and_frozen_chain_are_checked(tmp_path):
    args = frozen_case(tmp_path)
    assert validate_p3_inputs(*args) == "sha256_matched"
    bad = tmp_path / "other.pdf"
    bad.write_bytes(args[-1].read_bytes() + b"unexpected")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_p3_inputs(*args[:-1], bad)
    args[-2].snapshot_id = "foreign"
    with pytest.raises(ValueError, match="snapshot|identity"):
        validate_p3_inputs(*args)


def test_missing_pdf_is_explicit_and_foreign_task_source_is_rejected(tmp_path):
    args = frozen_case(tmp_path)
    assert validate_p3_inputs(*args[:-1], tmp_path / "missing.pdf") == "pdf_unavailable"
    task = args[-2].verification_tasks[0]
    task.source_refs_to_check[0].source_id = "foreign"
    with pytest.raises(ValueError, match="source|Evidence"):
        validate_p3_inputs(*args)


def test_pdf_text_location_is_not_visual_or_scientific_verification(tmp_path):
    bundle, *_, pdf_path = frozen_case(tmp_path)
    with EvidenceVerifier(bundle, pdf_path, tmp_path / "regions") as verifier:
        source, observation = verifier.observe("ru1", "task1")
    assert source.source_verification_status == "pdf_text_located"
    assert observation.source_check_method == "pdf_text_extraction"
    assert "does not establish" in source.limitations[0]
    assert observation.observation_type == "source_text"


def test_formula_region_is_generated_but_remains_visually_unverified(tmp_path):
    bundle, *_, pdf_path = frozen_case(tmp_path)
    bundle["formulas"] = [{"id": "formula1", "evidence_ids": ["ev1"],
                           "page_index": 0, "bbox": bundle["body"][0]["bbox"],
                           "text": "Method and novelty", "document_id": "doc1"}]
    with EvidenceVerifier(bundle, pdf_path, tmp_path / "regions") as verifier:
        source, _ = verifier.observe("formula1", "task1")
    assert source.source_verification_status == "visual_check_required"
    assert source.region_image_sha256
    assert (tmp_path / "regions" / f"{source.region_image_sha256}.png").exists()
    assert not source.source_method == "human_pdf_review"
