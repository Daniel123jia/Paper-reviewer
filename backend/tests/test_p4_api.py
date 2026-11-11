import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import fitz
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.p4_api import ReviewerIssueInput, reviewer_issue_fingerprint
from backend.p3_engine import run_p3
from backend.tests.test_p3_engine import model
from backend.tests.test_p3_sources import frozen_case


def seeded_p4_client(tmp_path, *, reportable=False):
    bundle, plan, profile, mapping, review, consolidation, pdf_path = frozen_case(tmp_path)
    result = run_p3(bundle, plan, profile, mapping, review, consolidation, pdf_path,
                    "v1", model, "mock")
    if reportable:
        adjudication = result.adjudications[0]
        adjudication.verdict = "concern_narrowed"
        adjudication.revised_question = "Could the method and novelty claim be clarified?"
        adjudication.report_eligibility = "eligible_with_qualification"
    client = TestClient(create_app(tmp_path))
    (tmp_path / "files" / "doc1.pdf").write_bytes(pdf_path.read_bytes())
    with fitz.open(pdf_path) as pdf:
        pages = [{"page_index": 0, "width": pdf[0].rect.width,
                  "height": pdf[0].rect.height, "rotation": 0, "needs_ocr": False}]
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("doc1", "paper.pdf", bundle["document"]["sha256"], "snap1", 1,
                    "[]", json.dumps(pages)))
        for evidence in bundle["body"]:
            db.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
                       (evidence["evidence_id"], "doc1", evidence["page_index"],
                        evidence["text"], json.dumps(evidence["bbox"]), "block"))
        db.execute("INSERT INTO paper_understanding_runs "
                   "(id, document_id, source_bundle_id, source_bundle_json, status, "
                   "review_plan, paper_profile, claim_evidence_map, created_at) "
                   "VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, datetime('now'))",
                   ("u1", "doc1", "sb1", json.dumps(bundle), plan.model_dump_json(),
                    profile.model_dump_json(), mapping.model_dump_json()))
        db.execute("INSERT INTO professional_review_runs "
                   "(id, understanding_run_id, document_id, source_bundle_id, status, "
                   "result_json, created_at) VALUES (?, ?, ?, ?, 'limited', ?, datetime('now'))",
                   ("b1", "u1", "doc1", "sb1", review.model_dump_json()))
        db.execute("INSERT INTO candidate_consolidation_runs "
                   "(id, professional_review_run_id, understanding_run_id, document_id, "
                   "source_bundle_id, status, result_json, created_at) "
                   "VALUES (?, ?, ?, ?, ?, 'limited', ?, datetime('now'))",
                   ("c1", "b1", "u1", "doc1", "sb1", consolidation.model_dump_json()))
        db.execute("INSERT INTO evidence_verification_runs "
                   "(id, consolidation_run_id, professional_review_run_id, understanding_run_id, "
                   "document_id, source_bundle_id, status, result_json, created_at) "
                   "VALUES ('v1', 'c1', 'b1', 'u1', 'doc1', 'sb1', 'limited', ?, datetime('now'))",
                   (result.model_dump_json(),))
    return client, result


def test_project_freezes_p3_snapshot_and_rejects_foreign_or_unfinished_source(tmp_path):
    client, result = seeded_p4_client(tmp_path)
    assert client.get("/api/documents/doc1/evidence-verification-runs").json()[0]["id"] == "v1"
    created = client.post("/api/evidence-verification-runs/v1/report-projects")
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["source"]["evidence_verification_run_id"] == "v1"
    assert project["issues"][0]["processing_status"] == "internal_only"
    assert project["issues"][0]["human_review_status"] == "pending"
    project_id = project["id"]
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE evidence_verification_runs SET status = 'failed' WHERE id = 'v1'")
    assert client.get(f"/api/review-report-projects/{project_id}").json()["source"] == project["source"]
    assert client.post("/api/evidence-verification-runs/v1/report-projects").status_code == 409
    assert result.adjudications[0].verdict == "inconclusive"


def test_reviewer_decision_cannot_promote_inconclusive_or_borrow_evidence(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    issue = project["issues"][0]
    url = f'/api/review-report-projects/{project["id"]}/issues/{issue["consolidated_issue_id"]}/decision'
    request = {"decision": "approve_for_final", "reviewer_rationale": "I checked the source",
               "selected_source_refs": issue["source_refs"]}
    assert client.patch(url, json=request).status_code == 409
    assert client.patch(url, json={**request, "decision": "revise",
                                   "selected_source_refs": ["foreign"]}).status_code == 400


def test_reviewer_added_issue_keeps_origin_and_separate_approval(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    path = f'/api/review-report-projects/{project["id"]}'
    added = client.post(path + "/reviewer-issues", json={
        "scientific_question": "Could the method's novelty scope be specified?",
        "evidence_basis": "Reviewer inspected the method statement in the uploaded PDF.",
        "source_refs": ["ru1"], "comment_body": "Clarify the method scope."})
    assert added.status_code == 201, added.text
    issue = added.json()
    assert issue["origin"] == "reviewer_added"
    assert client.get(path).json()["decisions"] == []
    approved = client.patch(path + f'/issues/{issue["issue_id"]}/decision', json={
        "decision": "approve_for_draft", "reviewer_rationale": "I reviewed the PDF",
        "selected_source_refs": ["ru1"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["reviewer_approval_status"] == "approved_for_draft"


def test_project_exposes_read_only_coverage_and_manual_source_navigation(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    project_id = project["id"]
    client.post(f"/api/review-report-projects/{project_id}/reviewer-issues",
                json=_manual_issue_payload())
    view = client.get(f"/api/review-report-projects/{project_id}").json()
    assert view["coverage_gaps"] == project["coverage_gaps"]
    assert view["bundle_source_refs"]["ru1"]["evidence_ids"]
    assert view["bundle_source_refs"]["ru1"]["page_index"] == 0


def _manual_issue_payload():
    return {"scientific_question": "Could the method's novelty scope be specified?",
            "evidence_basis": "Reviewer inspected the method statement in the uploaded PDF.",
            "source_refs": ["ru1"], "comment_body": "Clarify the method scope.",
            "category": "clarification"}


def test_reviewer_issue_fingerprint_is_unicode_and_source_order_stable():
    original = ReviewerIssueInput(scientific_question="Café method?", evidence_basis="A cited result.",
                                  source_refs=["ru1", "ru2"], comment_body="Please clarify.")
    retry = ReviewerIssueInput(scientific_question="  Cafe\u0301   method? ", evidence_basis="A  cited result.",
                               source_refs=["ru2", " ru1 ", "ru2"], comment_body="Please clarify.")
    distinct = ReviewerIssueInput(scientific_question="Café method!", evidence_basis="A cited result.",
                                  source_refs=["ru1", "ru2"], comment_body="Please clarify.")
    assert reviewer_issue_fingerprint(original) == reviewer_issue_fingerprint(retry)
    assert reviewer_issue_fingerprint(original) != reviewer_issue_fingerprint(distinct)


def test_reviewer_issue_exact_retry_returns_original_without_changing_decision(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project_id = client.post("/api/evidence-verification-runs/v1/report-projects").json()["id"]
    path = f"/api/review-report-projects/{project_id}"
    draft = client.post(path + "/drafts", json={"kind": "author", "language": "zh"})
    assert draft.status_code == 201
    first = client.post(path + "/reviewer-issues", json=_manual_issue_payload())
    assert first.status_code == 201
    issue = first.json()
    approval = client.patch(path + f"/issues/{issue['issue_id']}/decision", json={
        "decision": "approve_for_draft", "reviewer_rationale": "Reviewed the cited source.",
        "selected_source_refs": ["ru1"]})
    assert approval.status_code == 200
    original_decisions = client.get(path).json()["decisions"]
    original_draft = client.get(f"/api/review-report-drafts/{draft.json()['id']}").json()
    retry = client.post(path + "/reviewer-issues", json={
        **_manual_issue_payload(),
        "scientific_question": "  Could  the method's novelty scope be specified?  ",
        "evidence_basis": "Reviewer inspected the method\nstatement in the uploaded PDF.",
        "source_refs": [" ru1 ", "ru1"],
    })
    assert retry.status_code == 200
    assert retry.json() == issue
    view = client.get(path).json()
    assert [item["issue_id"] for item in view["reviewer_added"]] == [issue["issue_id"]]
    assert view["decisions"] == original_decisions
    assert client.get(f"/api/review-report-drafts/{draft.json()['id']}").json() == original_draft
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM p4_reviewer_added_issues").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM p4_draft_revisions").fetchone()[0] == 1


def test_reviewer_issue_different_project_or_content_is_not_deduplicated(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    projects = [client.post("/api/evidence-verification-runs/v1/report-projects").json()["id"]
                for _ in range(2)]
    original = _manual_issue_payload()
    first = client.post(f"/api/review-report-projects/{projects[0]}/reviewer-issues",
                        json=original)
    second = client.post(f"/api/review-report-projects/{projects[1]}/reviewer-issues",
                         json=original)
    distinct = client.post(f"/api/review-report-projects/{projects[0]}/reviewer-issues",
                           json={**original, "scientific_question":
                                 "Could the novelty claim's experimental scope be specified?"})
    assert [response.status_code for response in (first, second, distinct)] == [201, 201, 201]
    assert len({response.json()["issue_id"] for response in (first, second, distinct)}) == 3


def test_reviewer_issue_concurrent_retries_create_one_record(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project_id = client.post("/api/evidence-verification-runs/v1/report-projects").json()["id"]
    barrier = Barrier(6)

    def submit(_):
        barrier.wait()
        return client.post(f"/api/review-report-projects/{project_id}/reviewer-issues",
                           json=_manual_issue_payload())

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(submit, range(6)))
    assert sorted(item.status_code for item in responses) == [200] * 5 + [201]
    assert len({item.json()["issue_id"] for item in responses}) == 1
    assert len(client.get(f"/api/review-report-projects/{project_id}").json()["reviewer_added"]) == 1


def test_reviewer_issue_historical_duplicates_remain_and_oldest_handles_retry(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project_id = client.post("/api/evidence-verification-runs/v1/report-projects").json()["id"]
    path = f"/api/review-report-projects/{project_id}"
    oldest = client.post(path + "/reviewer-issues", json=_manual_issue_payload()).json()
    duplicate = {**oldest, "issue_id": "reviewer_historical_duplicate"}
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO p4_reviewer_added_issues VALUES (?, ?, ?)",
                   (duplicate["issue_id"], project_id, json.dumps(duplicate)))
        db.execute("DROP TABLE p4_reviewer_issue_fingerprints")
    restarted = TestClient(create_app(tmp_path))
    assert len(restarted.get(path).json()["reviewer_added"]) == 2
    retry = restarted.post(path + "/reviewer-issues", json=_manual_issue_payload())
    assert retry.status_code == 200
    assert retry.json() == oldest
    assert len(restarted.get(path).json()["reviewer_added"]) == 2


def test_table_review_records_page_rows_values_metrics_and_does_not_change_p3(tmp_path):
    client, result = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects")
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    # Isolate P4's evidence-review gate with a frozen visual source; the P3 row remains intact.
    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
                                       "WHERE id = ?", (project_id,)).fetchone()[0])
        table = dict(frozen["p3"]["sources"][0])
        table.update(source_id="table1", source_type="table", page_index=0,
                     source_verification_status="visual_check_required",
                     relevant_excerpt="Table 2: Model EM F1")
        frozen["p3"]["sources"].append(table)
        adjudication = frozen["p3"]["adjudications"][0]
        adjudication["original_source_refs"].append("table1")
        adjudication.update(verdict="concern_narrowed",
            revised_question="Could Table 2 model, EM and F1 values be clarified?",
            report_eligibility="requires_human_review")
        db.execute("UPDATE p4_report_projects SET source_snapshot_json = ? WHERE id = ?",
                   (json.dumps(frozen), project_id))
    entry = client.get(f"/api/review-report-projects/{project_id}").json()["issues"][0]
    assert entry["processing_status"] == "human_review_required"
    issue_id = entry["consolidated_issue_id"]
    decision_url = f"/api/review-report-projects/{project_id}/issues/{issue_id}/decision"
    proposal = {"decision": "approve_for_draft", "reviewer_rationale": "Clarification is useful",
                "selected_source_refs": entry["source_refs"]}
    assert client.patch(decision_url, json=proposal).status_code == 409
    review_url = f"/api/review-report-projects/{project_id}/issues/{issue_id}/evidence-reviews"
    record = {"source_ref_id": "table1", "page_index": 0, "object_label": "Table 2",
              "checked_rows_columns": "DistilBERT (D), EM, F1",
              "checked_values_and_labels": "DistilBERT (D): 79.1 EM / 86.9 F1",
              "metric_names_and_order": "EM then F1",
              "unresolved_configurations": ["checkpoint and split"],
              "reviewer_note": "Compared row and column labels on original PDF page.",
              "confirmation_status": "confirmed"}
    assert client.post(review_url, json={**record, "page_index": 1}).status_code == 400
    assert client.post(review_url, json={**record, "checked_values_and_labels": ""}).status_code == 400
    saved = client.post(review_url, json=record)
    assert saved.status_code == 201, saved.text
    approved = client.patch(decision_url, json={**proposal,
        "evidence_review_record_ids": [saved.json()["record_id"]]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["reviewer_approval_status"] == "approved_for_draft"
    original = client.get("/api/evidence-verification-runs/v1").json()["result"]
    assert original["adjudications"][0]["report_eligibility"] == "requires_human_review"
    assert original["adjudications"][0]["human_review_status"] == "pending"


def _set_external_dependency(tmp_path, project_id, question):
    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
                                       "WHERE id = ?", (project_id,)).fetchone()[0])
        issue = frozen["p3"]["adjudications"][0]
        issue.update(verdict="concern_narrowed", revised_question=question,
                     report_eligibility="requires_human_review")
        task_id = issue["verification_task_id"]
        task = next(item for item in frozen["consolidation"]["verification_tasks"]
                    if item["verification_task_id"] == task_id)
        task["external_verification_required"] = True
        task["additional_materials_needed"] = ["External literature record"]
        db.execute("UPDATE p4_report_projects SET source_snapshot_json = ? WHERE id = ?",
                   (json.dumps(frozen), project_id))


def test_external_fact_cannot_be_approved_without_separate_verification_record(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    project_id = project["id"]
    _set_external_dependency(tmp_path, project_id,
        "Has prior work already proven that this method improves out-of-domain accuracy?")
    issue = client.get(f"/api/review-report-projects/{project_id}").json()["issues"][0]
    assert issue["external_verification_required"] is True
    assert issue["external_dependency_is_critical"] is True
    assert issue["external_verification_completed_in_p3"] is False
    issue_id = issue["consolidated_issue_id"]
    decision_url = f"/api/review-report-projects/{project_id}/issues/{issue_id}/decision"
    proposal = {"decision": "approve_for_draft",
                "reviewer_rationale": "The external comparison is material to this wording.",
                "selected_source_refs": issue["source_refs"],
                "external_dependency_disposition": "verified"}
    rejected = client.patch(decision_url, json=proposal)
    assert rejected.status_code == 409

    review = client.post(
        f"/api/review-report-projects/{project_id}/issues/{issue_id}/external-evidence-reviews",
        json={"material_type": "literature", "material_identifier": "doi:10.0000/example",
              "verification_question": "Does the prior work establish this result?",
              "verified_finding": "The cited study reports the result for the stated setting.",
              "limitations": ["Only the cited evaluation setting was checked"],
              "reviewer_note": "Compared the claim with the cited article and its experiment section.",
              "confirmation_status": "confirmed"})
    assert review.status_code == 201, review.text
    incomplete_decision = client.patch(decision_url, json={
        **proposal, "external_dependency_disposition": "not_assessed",
        "external_evidence_review_record_ids": [review.json()["record_id"]]})
    assert incomplete_decision.status_code == 409
    approved = client.patch(decision_url, json={**proposal,
        "external_evidence_review_record_ids": [review.json()["record_id"]]})
    assert approved.status_code == 200, approved.text
    view = client.get(f"/api/review-report-projects/{project_id}").json()
    assert len(view["external_evidence_reviews"]) == 1
    from backend.p4_planner import build_report_plan
    from backend.p4_report_schema import ExternalEvidenceReviewRecord, ReviewerDecision
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.row_factory = sqlite3.Row
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
            "WHERE id = ?", (project_id,)).fetchone()[0])
        decisions = [ReviewerDecision.model_validate_json(row["record_json"]) for row in
            db.execute("SELECT record_json FROM p4_reviewer_decisions WHERE project_id = ?",
                       (project_id,))]
        external = [ExternalEvidenceReviewRecord.model_validate_json(row["record_json"]) for row in
            db.execute("SELECT record_json FROM p4_external_evidence_reviews WHERE project_id = ?",
                       (project_id,))]
    plan, pack = build_report_plan(frozen, decisions, [], [], "author", "en", external)
    assert plan.selected_issue_ids == [issue_id]
    assert pack.issues[0].external_dependency_status == "reviewer_verified"
    assert pack.issues[0].external_verified_findings == [
        "The cited study reports the result for the stated setting."]


def test_external_material_can_remain_missing_for_scoped_author_clarification(tmp_path):
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    project_id = project["id"]
    _set_external_dependency(tmp_path, project_id,
        "Could the authors clarify whether the conclusion is limited to settings evaluated in this paper?")
    issue = client.get(f"/api/review-report-projects/{project_id}").json()["issues"][0]
    decision = client.patch(
        f"/api/review-report-projects/{project_id}/issues/{issue['consolidated_issue_id']}/decision",
        json={"decision": "approve_for_draft",
              "reviewer_rationale": "This asks only for the scope of the paper's own conclusion.",
              "selected_source_refs": issue["source_refs"],
              "external_dependency_disposition": "clarification_only",
              "external_uncertainty_qualification":
                  "External literature was not checked; retain this only as a request for clarification."})
    assert decision.status_code == 200, decision.text
    view = client.get(f"/api/review-report-projects/{project_id}").json()
    assert view["external_evidence_reviews"] == []
    assert decision.json()["external_evidence_review_record_ids"] == []
