import pytest

from backend.p3_retrieval import CounterEvidenceRetriever, validate_challenge
from backend.tests.test_p3_sources import frozen_case


def test_full_unit_index_finds_authors_ablation_outside_original_refs(tmp_path):
    bundle, _, profile, mapping, _, consolidation, _ = frozen_case(tmp_path)
    bundle["body"].append({"evidence_id": "ev3", "page_index": 0,
                           "text": "We provide an ablation experiment for the method.",
                           "bbox": [1, 20, 30, 35]})
    bundle["reading_units"].append({"id": "ru3", "page_index": 0, "section_id": "s1",
                                    "evidence_ids": ["ev3"],
                                    "text": "We provide an ablation experiment for the method."})
    task = consolidation.verification_tasks[0]
    issue = consolidation.consolidated_issues[0]
    pack = CounterEvidenceRetriever(bundle, mapping, profile).retrieve(task, issue)
    assert pack.search.indexed_unit_count == 3
    assert "ru3" in pack.search.selected_source_ids
    assert pack.search.absence_confirmed is False
    assert any(item.source_id == "ru3" and item.reason == "full_text_search"
               for item in pack.entries)


def test_absent_search_hit_never_proves_author_omission(tmp_path):
    bundle, _, profile, mapping, _, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    issue = consolidation.consolidated_issues[0]
    pack = CounterEvidenceRetriever(bundle, mapping, profile).retrieve(task, issue)
    assert pack.search.indexed_unit_count == len(bundle["reading_units"])
    assert not pack.search.absence_confirmed
    assert pack.search.source_quality_limits


def test_challenge_can_record_counterevidence_but_rejects_foreign_refs(tmp_path):
    bundle, _, profile, mapping, _, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    issue = consolidation.consolidated_issues[0]
    pack = CounterEvidenceRetriever(bundle, mapping, profile).retrieve(task, issue)
    source_id = pack.entries[0].source_id
    raw = {"supporting_source_ids": [], "contradicting_source_ids": [source_id],
           "author_answer_source_ids": [source_id],
           "author_answer_assessment": "The author gives a possible explanation",
           "unresolved_questions": [], "source_interpretation": "Needs PDF review",
           "source_limitations": [], "evidence_level": "model_interpretation"}
    assert validate_challenge(raw, pack).contradicting_source_ids == [source_id]
    raw["contradicting_source_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="outside"):
        validate_challenge(raw, pack)
