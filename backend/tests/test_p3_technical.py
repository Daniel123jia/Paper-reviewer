import pytest

from backend.p3_retrieval import CounterEvidenceRetriever
from backend.p3_schema import SourceObservation, VerificationSource
from backend.p3_technical import (MetricRecord, ScalarFormulaProposal, TechnicalEvidenceProposal,
                                  run_technical_checks, validate_technical_proposal)
from backend.tests.test_p3_sources import frozen_case


def source(source_id, text, kind="reading_unit", status="pdf_text_located"):
    return VerificationSource(source_id=source_id, source_type=kind,
        document_id="doc1", snapshot_id="snap1", page_index=0, bbox=None,
        evidence_ids=["ev1"], source_method="frozen_bundle_plus_pdf_text",
        source_verification_status=status, relevant_excerpt=text, limitations=[])


def observation(source_id, text, task_id):
    return SourceObservation(observation_id=f"obs_{source_id}",
        verification_task_id=task_id, related_source_ref_ids=[source_id],
        document_id="doc1", snapshot_id="snap1", page_index=0, bbox=None,
        original_excerpt=text, observed_statement=text, observation_type="source_text",
        source_check_method="pdf_text_extraction", source_check_status="pdf_text_located",
        limitations=[])


def record(record_id, source_id, value, configuration="pretrained"):
    return MetricRecord(record_id=record_id, source_id=source_id, dataset="SQuAD",
        metric="EM", model_name="DistilBERT", configuration=configuration,
        value=value, unit="points")


def test_matching_setting_difference_is_only_a_potential_discrepancy(tmp_path):
    *_, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    records = [record("a", "ru1", "70.4"), record("b", "ru2", "79.1")]
    proposal = TechnicalEvidenceProposal(metric_records=records,
        scalar_formula_checks=[], unavailable_checks=[])
    sources = [source("ru1", "70.4 EM"), source("ru2", "79.1 EM")]
    observations = [observation(item.source_id, item.relevant_excerpt,
                                task.verification_task_id) for item in sources]
    checks = run_technical_checks(task, proposal, sources, observations)
    pair = next(item for item in checks if item.result.get("comparison") == "potential_discrepancy")
    assert pair.result["difference"] == "-8.7"
    assert "potential" in pair.result["comparison"]


def test_different_experiment_configurations_are_not_numeric_conflicts(tmp_path):
    *_, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    proposal = TechnicalEvidenceProposal(metric_records=[
        record("a", "ru1", "70.4", "two-stage"),
        record("b", "ru2", "79.1", "adaptation-only")],
        scalar_formula_checks=[], unavailable_checks=[])
    sources = [source("ru1", "70.4 EM"), source("ru2", "79.1 EM")]
    observations = [observation(item.source_id, item.relevant_excerpt,
                                task.verification_task_id) for item in sources]
    checks = run_technical_checks(task, proposal, sources, observations)
    pair = next(item for item in checks if "comparison" in item.result)
    assert pair.result["comparison"] == "not_comparable"
    assert pair.result["difference"] is None


def test_unverified_formula_transcription_cannot_prove_author_error(tmp_path):
    *_, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    proposal = TechnicalEvidenceProposal(metric_records=[], scalar_formula_checks=[
        ScalarFormulaProposal(check_id="f1", source_id="formula1", lhs="x+1", rhs="x+2",
                              assumptions={"x": "positive"}, domain_note="x is positive")],
        unavailable_checks=[])
    formula = source("formula1", "x+1=x+2", "formula", "visual_check_required")
    checks = run_technical_checks(task, proposal, [formula],
        [observation("formula1", "x+1=x+2", task.verification_task_id)])
    result = checks[0]
    assert result.result["status"] == "counterexample"
    assert result.result["sample_satisfies_declared_assumptions"] is True
    assert result.status == "inconclusive"
    assert any("transcription" in item for item in result.limitations)


def test_unsupported_tensor_expression_is_not_reported_as_verified(tmp_path):
    *_, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    proposal = TechnicalEvidenceProposal(metric_records=[], scalar_formula_checks=[
        ScalarFormulaProposal(check_id="f1", source_id="formula1", lhs="A @ B", rhs="C",
                              assumptions={}, domain_note="matrix relation")],
        unavailable_checks=[])
    checks = run_technical_checks(task, proposal,
        [source("formula1", "A @ B = C", "formula", "visual_check_required")], [])
    assert checks[0].result["status"] == "unsupported_expression"
    assert checks[0].status == "inconclusive"


def test_model_cannot_propose_foreign_metric_source(tmp_path):
    bundle, _, profile, mapping, _, consolidation, _ = frozen_case(tmp_path)
    pack = CounterEvidenceRetriever(bundle, mapping, profile).retrieve(
        consolidation.verification_tasks[0], consolidation.consolidated_issues[0])
    raw = {"metric_records": [record("a", "foreign", "70.4").model_dump()],
           "scalar_formula_checks": [], "unavailable_checks": [],
           "evidence_level": "model_proposal"}
    with pytest.raises(ValueError, match="outside"):
        validate_technical_proposal(raw, pack)
