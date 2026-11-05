import json
import sqlite3

from fastapi.testclient import TestClient

from backend.app import create_app
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
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("doc1", "paper.pdf", bundle["document"]["sha256"], "snap1", 1, "[]", "[]"))
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
