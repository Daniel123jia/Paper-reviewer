import json

from backend.p2b_engine import run_p2b
from backend.p2c_coverage import audit_coverage
from backend.p2c_normalizer import normalize_candidates
from backend.tests.test_p2b_engine import inputs, reply


def case(inspected=True):
    bundle, plan, profile, mapping = inputs()
    def model(stage, system, user):
        raw = reply()
        payload = json.loads(raw["content"])
        if not inspected:
            payload["inspected_claim_ids"] = []
        raw["content"] = json.dumps(payload)
        return raw
    review = run_p2b(bundle, plan, profile, mapping, "u1", "b1", model, "model")
    inventory = normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    return bundle, plan, profile, mapping, review, inventory


def test_no_issue_with_reported_inspection_is_not_a_missing_review():
    args = case()
    audit = audit_coverage(*args)
    assert audit.claims[0].status in {"reported_inspected_no_issue", "source_limited"}
    assert audit.claims[0].evidence_level == "model_report_only"
    assert not any(gap.gap_type == "claim_not_inspected" for gap in audit.gaps)
    assert not any(gap.gap_type == "dimension_unfinished" and "D6" in gap.affected_dimension_ids
                   for gap in audit.gaps)
    assert audit.dimensions[5].candidate_count == 0
    assert audit.dimensions[5].no_issue_explanation


def test_uninspected_claim_creates_gap_without_inventing_issue():
    args = case(inspected=False)
    audit = audit_coverage(*args)
    assert audit.claims[0].status == "not_reported_inspected"
    assert any(gap.gap_type == "claim_not_inspected" and gap.affected_claim_ids == ["cl1"]
               for gap in audit.gaps)
    assert not args[-1].candidates


def test_limited_source_and_missing_formula_are_not_full_coverage():
    args = case()
    audit = audit_coverage(*args)
    assert all(item.status != "reported_inspected" for item in audit.professional_focus)
    assert any(gap.gap_type == "source_quality" for gap in audit.gaps)
    assert any("Equation needs PDF check" in gap.description for gap in audit.gaps)


def test_inapplicable_dimension_is_recorded_without_false_gap():
    bundle, plan, profile, mapping = inputs()
    plan.excluded_dimensions = ["D3"]
    review = run_p2b(bundle, plan, profile, mapping, "u1", "b1",
                     lambda stage, system, user: reply(), "model")
    inventory = normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    audit = audit_coverage(bundle, plan, profile, mapping, review, inventory)
    assert audit.dimensions[2].execution_status == "skipped"
    assert all(item.status == "not_applicable" for item in audit.professional_focus
               if item.dimension_id == "D3")
    assert not any(gap.gap_type == "dimension_unfinished" and "D3" in gap.affected_dimension_ids
                   for gap in audit.gaps)
