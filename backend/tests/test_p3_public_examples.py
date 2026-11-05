import json
from pathlib import Path

from backend.p3_schema import EvidenceVerificationResult, IssueAdjudication
from backend.p2c_schema import ConsolidatedIssue, VerificationTask


ROOT = Path(__file__).resolve().parents[2] / "docs" / "examples"


def test_public_real_paper_results_preserve_every_frozen_task_and_lineage():
    for paper in ("shaw2018", "distilbert2019", "fasttext2016"):
        directory = ROOT / f"p3-{paper}"
        result = EvidenceVerificationResult.model_validate_json(
            (directory / "evidence_verification_result.json").read_text())
        historical = EvidenceVerificationResult.model_validate_json(
            (directory / "history" / "p3-v1.0" / "evidence_verification_result.json").read_text())
        issues = [ConsolidatedIssue.model_validate(item) for item in json.loads(
            (directory / "p2c_consolidated_issues.json").read_text())]
        tasks = [VerificationTask.model_validate(item) for item in json.loads(
            (directory / "p2c_verification_tasks.json").read_text())]
        adjudications = [IssueAdjudication.model_validate(item) for item in json.loads(
            (directory / "issue_adjudications.json").read_text())]
        summary = json.loads((directory / "run_summary.json").read_text())
        assert result.pdf_identity_status == "sha256_matched"
        assert historical.schema_version == "1.0"
        assert result.schema_version == "1.1"
        assert result.decision_rule_version == "p3.1"
        assert result.execution_mode == "recorded_model_replay"
        assert result.replayed_from_run_id == historical.evidence_verification_run_id
        assert (result.call_count, result.input_tokens, result.output_tokens,
                result.duration_ms) == (historical.call_count, historical.input_tokens,
                                         historical.output_tokens, historical.duration_ms)
        assert len(result.adjudications) == len(tasks) == summary["p2c_tasks"]
        assert {item.verification_task_id for item in result.adjudications} == \
            {item.verification_task_id for item in tasks}
        assert [item.model_dump() for item in adjudications] == \
            [item.model_dump() for item in result.adjudications]
        candidates_by_issue = {item.consolidated_issue_id: item.source_candidate_ids
                               for item in issues}
        for item in result.adjudications:
            assert item.original_candidate_ids == candidates_by_issue[item.consolidated_issue_id]
        assert summary["call_count"] == result.call_count
        assert summary["input_tokens"] == result.input_tokens
        assert summary["output_tokens"] == result.output_tokens
