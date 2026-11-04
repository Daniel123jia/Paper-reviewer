import itertools
import json

from backend.p2b_engine import run_p2b
from backend.p2c_grouping import consolidate_from_relations, run_semantic_grouping
from backend.p2c_normalizer import normalize_candidates
from backend.p2c_schema import CandidateRelation
from backend.tests.test_p2b_engine import candidate, inputs, reply


def case(stages=("D1", "D2", "D3", "D4")):
    bundle, plan, profile, mapping = inputs()
    def model(stage, system, user):
        if stage not in stages:
            return reply()
        item = candidate(title=f"Different title {stage}")
        item["verification_questions"] = [f"Check {stage}"]
        item["source_limitations"] = [f"Limit {stage}"]
        item["potential_impact"] = "minor_candidate" if stage == "D2" else "major_candidate"
        item["external_verification_required"] = stage == "D4"
        item["possible_counterevidence"] = [{"source_id": "ru2", "evidence_role": "counterevidence"}]
        return reply([item])
    review = run_p2b(bundle, plan, profile, mapping, "u1", "b1", model, "model")
    inventory = normalize_candidates(bundle, plan, profile, mapping, review, "u1", "b1", "limited")
    return inventory, mapping


def relations(ids, overrides=None):
    overrides = overrides or {}
    return [CandidateRelation(left_issue_id=a, right_issue_id=b,
            relation=overrides.get((a, b), "semantic_duplicate"),
            rationale="same verification need" if overrides.get((a, b), "semantic_duplicate")
            == "semantic_duplicate" else "independent verification need")
            for a, b in itertools.combinations(sorted(ids), 2)]


def test_four_dimensions_same_question_merge_without_evidence_loss():
    inventory, mapping = case()
    ids = sorted(item.issue_id for item in inventory.candidates)
    issues, lineage = consolidate_from_relations(inventory, mapping, relations(ids),
                                                  [{"issue_ids": ids, "rationale": "same test"}])
    assert len(issues) == 1
    assert {item.original_issue_id for item in lineage} == set(ids)
    merged = issues[0]
    assert merged.source_dimension_ids == ["D1", "D2", "D3", "D4"]
    assert {ref.source_id for ref in merged.counterevidence_refs} == {"ru2"}
    assert set(merged.verification_questions) == {f"Check D{i}" for i in range(1, 5)}
    assert set(merged.source_limitations) == {f"Limit D{i}" for i in range(1, 5)}
    assert merged.potential_impact == "undetermined"
    assert merged.external_verification_required and merged.external_flag_disagreement
    assert merged.verification_status == "unverified"


def test_related_efficiency_math_and_gpu_measurement_remain_distinct():
    inventory, mapping = case(("D1", "D2"))
    ids = sorted(item.issue_id for item in inventory.candidates)
    rel = relations(ids, {(ids[0], ids[1]): "related_but_distinct"})
    issues, lineage = consolidate_from_relations(inventory, mapping, rel,
        [{"issue_ids": ids, "rationale": "both efficiency"}])
    assert len(issues) == 2
    assert len({item.consolidated_issue_id for item in lineage}) == 2


def test_duplicate_chain_does_not_force_transitive_three_way_merge():
    inventory, mapping = case(("D1", "D2", "D3"))
    ids = sorted(item.issue_id for item in inventory.candidates)
    rel = relations(ids, {(ids[0], ids[2]): "distinct"})
    issues, _ = consolidate_from_relations(inventory, mapping, rel,
        [{"issue_ids": ids, "rationale": "transitive proposal"}])
    assert sorted(len(item.source_candidate_ids) for item in issues) == [1, 2]


def test_uncertain_relation_stays_separate_and_ids_are_stable():
    inventory, mapping = case(("D1", "D2"))
    ids = sorted(item.issue_id for item in inventory.candidates)
    rel = relations(ids, {(ids[0], ids[1]): "uncertain_relation"})
    first, _ = consolidate_from_relations(inventory, mapping, rel)
    second, _ = consolidate_from_relations(inventory, mapping, rel)
    assert len(first) == 2
    assert [item.consolidated_issue_id for item in first] == [item.consolidated_issue_id for item in second]


def test_invalid_model_json_degrades_to_uncertain_without_losing_candidates():
    inventory, mapping = case(("D1", "D2"))
    traces = []
    outcome = run_semantic_grouping(inventory, mapping,
        lambda stage, system, user: {"content": "not JSON", "model_identifier": "mock",
                                     "input_tokens": 50, "output_tokens": 10, "duration_ms": 2},
        "mock", on_trace=traces.append)
    assert outcome.status == "limited"
    assert len(outcome.issues) == 2
    assert len(outcome.lineage) == inventory.candidate_count
    assert outcome.relations[0].relation == "uncertain_relation"
    assert outcome.retry_count >= 1
    assert traces and all(trace.error is None or "not JSON" not in trace.error for trace in traces)


def test_model_pair_and_reconciliation_contract():
    inventory, mapping = case(("D1", "D2"))
    calls = []
    def model(stage, system, user):
        payload = json.loads(user)
        calls.append((stage, payload))
        if stage.startswith("pair-"):
            a, b = payload["pairs"][0]
            content = {"relations": [{"left_issue_id": a, "right_issue_id": b,
                        "relation": "semantic_duplicate", "rationale": "same question",
                        "shared_verification_need": "one source check"}]}
        else:
            content = {"groups": [{"issue_ids": sorted(item.issue_id for item in inventory.candidates),
                                    "rationale": "one verification check"}]}
        return {"content": json.dumps(content), "model_identifier": "mock",
                "input_tokens": 100, "output_tokens": 20, "duration_ms": 3}
    outcome = run_semantic_grouping(inventory, mapping, model, "mock")
    assert outcome.status == "completed"
    assert len(outcome.issues) == 1
    assert [stage for stage, _ in calls] == ["pair-1", "reconciliation"]
    assert outcome.call_count == 2
