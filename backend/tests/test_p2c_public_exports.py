"""Check published structured examples; full Source Bundle validation runs at export time."""

import json
from pathlib import Path

import pytest

from backend.p2c_schema import CandidateConsolidationResult


ROOT = Path(__file__).resolve().parents[2] / "docs/examples"


@pytest.mark.parametrize("paper", ("shaw2018", "distilbert2019", "fasttext2016"))
def test_public_result_preserves_p2b_candidates_and_evidence_mapping(paper):
    p2c = ROOT / f"p2c-{paper}"
    p2b = ROOT / f"p2b-{paper}"
    result = CandidateConsolidationResult.model_validate_json(
        (p2c / "candidate_consolidation_result.json").read_text())
    original = json.loads((p2b / "issue_candidates.json").read_text())
    upstream = json.loads((p2b / "run_summary.json").read_text())
    source_mapping = json.loads((p2b / "evidence_mapping.json").read_text())
    assert [item.model_dump() for item in result.inventory.candidates] == original
    assert result.pdf_sha256 == upstream["source_pdf_sha256"]
    assert result.professional_review_run_id == upstream["professional_review_run_id"]
    assert all(ref.source_id in source_mapping for issue in result.consolidated_issues
               for ref in issue.original_source_refs + issue.counterevidence_refs)
    assert len(result.verification_tasks) == len(result.consolidated_issues)
    assert all(task.central_question == next(issue.central_question for issue in result.consolidated_issues
               if issue.consolidated_issue_id == task.consolidated_issue_id)
               for task in result.verification_tasks)
    assert all(issue.verification_status == "unverified" for issue in result.consolidated_issues)
