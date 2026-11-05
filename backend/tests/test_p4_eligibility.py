from types import SimpleNamespace

import pytest

from backend.p4_eligibility import classify_adjudication


@pytest.mark.parametrize(("verdict", "report_eligibility", "expected"), [
    ("concern_refuted", "not_reportable", "excluded"),
    ("inconclusive", "requires_human_review", "internal_only"),
    ("blocked", "requires_human_review", "internal_only"),
    ("concern_narrowed", "requires_human_review", "human_review_required"),
    ("concern_supported", "requires_human_review", "human_review_required"),
    ("concern_narrowed", "eligible_with_qualification", "auto_draft_candidate"),
    ("concern_supported", "eligible_with_qualification", "auto_draft_candidate"),
])
def test_p3_statuses_are_not_promoted_by_report_admission(verdict, report_eligibility, expected):
    item = SimpleNamespace(verdict=verdict, report_eligibility=report_eligibility,
                           human_review_status="pending")
    assert classify_adjudication(item) == expected
    assert item.human_review_status == "pending"
