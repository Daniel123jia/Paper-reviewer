import pytest

from backend.p2b_engine import run_p2b
from backend.tests.test_p2b_engine import candidate, inputs, reply

from backend.p2c_normalizer import normalize_candidates, validate_consolidation_inputs


def fixture_review():
    bundle, plan, profile, mapping = inputs()
    review = run_p2b(bundle, plan, profile, mapping, "u1", "b1",
        lambda stage, system, user: reply([candidate(title=f"Question {stage}")])
        if stage in {"D1", "D2"} else reply(), "model")
    assert review.execution_status == "limited"
    return bundle, plan, profile, mapping, review


def test_inventory_retains_every_candidate_and_indexes_source_claim_contribution():
    bundle, plan, profile, mapping, review = fixture_review()
    inventory = normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    ids = [item.issue_id for item in review.candidates]
    assert inventory.candidate_count == 2
    assert [item.issue_id for item in inventory.candidates] == ids
    assert inventory.dimension_index["D1"] == [ids[0]]
    assert inventory.dimension_index["D2"] == [ids[1]]
    assert inventory.claim_index["cl1"] == ids
    assert inventory.contribution_index["c1"] == ids
    assert inventory.source_index["ru1"] == ids
    assert inventory.snapshot_id == "snap1"


def test_normalizer_rejects_foreign_snapshot_and_missing_source():
    bundle, plan, profile, mapping, review = fixture_review()
    review.source_bundle_id = "foreign"
    with pytest.raises(ValueError, match="snapshot|bundle|document"):
        normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    review.source_bundle_id = "sb1"
    review.candidates[0].source_refs[0].source_id = "not-in-pdf"
    with pytest.raises(ValueError, match="source"):
        normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")


def test_partial_failed_cannot_be_used_as_complete_input():
    bundle, plan, profile, mapping, review = fixture_review()
    with pytest.raises(ValueError, match="complete P2-B"):
        validate_consolidation_inputs(bundle, plan, profile, mapping, review,
                                      "u1", "b1", "partial_failed")


def test_unknown_claim_and_duplicate_issue_are_rejected():
    bundle, plan, profile, mapping, review = fixture_review()
    review.candidates[0].claim_ids = ["missing"]
    with pytest.raises(ValueError, match="claim"):
        normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    review.candidates[0].claim_ids = ["cl1"]
    review.candidates[1].issue_id = review.candidates[0].issue_id
    with pytest.raises(ValueError, match="duplicate"):
        normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
