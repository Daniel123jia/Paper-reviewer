import json
import sqlite3

from backend.p4_planner import build_report_plan
from backend.tests.test_p4_api import seeded_p4_client


def snapshot(tmp_path, client):
    project = client.post("/api/evidence-verification-runs/v1/report-projects")
    assert project.status_code == 201, project.text
    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
                                       "WHERE id = ?", (project.json()["id"],)).fetchone()[0])
    return frozen


def test_internal_plan_keeps_unresolved_issue_while_author_draft_has_no_filler(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    frozen = snapshot(tmp_path, client)
    internal, internal_pack = build_report_plan(frozen, [], [], [], "internal", "zh")
    author, author_pack = build_report_plan(frozen, [], [], [], "author", "en")
    assert len(internal.selected_issue_ids) == len(internal_pack.issues) == 1
    assert internal_pack.issues[0].verdict == "inconclusive"
    assert author.selected_issue_ids == author_pack.issues == []
    assert author.internal_only_issue_ids == internal.selected_issue_ids


def test_reportable_narrowed_issue_keeps_revised_question_and_pending_approval(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    frozen = snapshot(tmp_path, client)
    plan, pack = build_report_plan(frozen, [], [], [], "author", "en")
    assert len(plan.groups) == len(pack.issues) == 1
    assert pack.issues[0].scientific_question == pack.issues[0].revised_question
    assert pack.issues[0].reviewer_approval_status == "pending"
    assert plan.groups[0].category == "clarification"
    assert pack.issues[0].observed_statements


def test_same_question_groups_without_losing_ids_and_distinct_questions_stay_separate(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    frozen = snapshot(tmp_path, client)
    # Exercise grouping without mutating accepted P2-C inventory and lineage.
    from backend.p4_planner import _groups
    from backend.p4_report_schema import EligibilityEntry
    from backend.p2c_schema import ConsolidatedIssue
    base_issue = ConsolidatedIssue.model_validate(frozen["consolidation"]["consolidated_issues"][0])
    other_issue = base_issue.model_copy(update={"consolidated_issue_id": "second"})
    from types import SimpleNamespace
    consolidation = SimpleNamespace(consolidated_issues=[base_issue, other_issue])
    one = EligibilityEntry(consolidated_issue_id=base_issue.consolidated_issue_id,
        verification_task_id="one", title="Method", scientific_question="Could the method and novelty claim be clarified?",
        verdict="concern_narrowed", report_eligibility="eligible_with_qualification",
        human_review_status="pending", processing_status="auto_draft_candidate",
        source_refs=["ru1"], counterevidence_refs=[], outstanding_checks=[],
        provisional_impact="undetermined")
    two = one.model_copy(update={"consolidated_issue_id": "second", "verification_task_id": "two"})
    grouped = _groups([one, two], consolidation, {}, [])
    assert len(grouped) == 1
    assert grouped[0].source_issue_ids == [one.consolidated_issue_id, "second"]
    distinct = two.model_copy(update={"scientific_question": "How were the dataset labels audited?"})
    assert len(_groups([one, distinct], consolidation, {}, [])) == 2


def test_shared_technical_request_merges_but_different_claim_does_not(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    frozen = snapshot(tmp_path, client)
    from backend.p4_planner import _groups
    from backend.p4_report_schema import EligibilityEntry
    from backend.p2c_schema import ConsolidatedIssue
    from types import SimpleNamespace
    base = ConsolidatedIssue.model_validate(frozen["consolidation"]["consolidated_issues"][0])
    same = base.model_copy(update={"consolidated_issue_id": "same"})
    different = base.model_copy(update={"consolidated_issue_id": "different",
                                "related_claim_ids": ["another_claim"]})
    one = EligibilityEntry(consolidated_issue_id=base.consolidated_issue_id,
        verification_task_id="one", title="One",
        scientific_question="Does the full objective have a matched masked language modeling alone baseline?",
        verdict="inconclusive", report_eligibility="requires_human_review",
        human_review_status="pending", processing_status="internal_only",
        source_refs=[], counterevidence_refs=[], outstanding_checks=[],
        provisional_impact="undetermined")
    two = one.model_copy(update={"consolidated_issue_id": "same", "verification_task_id": "two",
        "scientific_question": "Is masked language modeling alone compared under the same architecture and training budget?"})
    three = two.model_copy(update={"consolidated_issue_id": "different", "verification_task_id": "three"})
    groups = _groups([one, two, three], SimpleNamespace(consolidated_issues=[
        base, same, different]), {}, [])
    assert len(groups) == 2
    assert groups[0].source_issue_ids == [base.consolidated_issue_id, "same"]
    assert groups[1].source_issue_ids == ["different"]
