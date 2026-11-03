import io
import json
import re
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.review import parse_model_json, pick_review_evidence, retrieve_counterevidence, screen_candidates, suggest_formula_links


def upload(client, name):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 80), f"Paper {name}")
    page.insert_text((72, 120), "The reported baseline is 80 percent.")
    data = pdf.tobytes()
    pdf.close()
    return client.post("/api/documents", files={"file": (name, io.BytesIO(data), "application/pdf")}).json()


def test_review_case_modes_and_persistence(tmp_path):
    client = TestClient(create_app(tmp_path))
    original = upload(client, "original.pdf")
    revision = upload(client, "revision.pdf")
    response = upload(client, "response.pdf")
    base = {"original_document_id": original["id"]}
    first = client.post("/api/review-cases", json={**base, "mode": "first_review"})
    assert first.status_code == 201
    assert first.json()["mode"] == "first_review"
    assert client.post("/api/review-cases", json={**base, "mode": "own_rereview"}).status_code == 400
    own = client.post("/api/review-cases", json={**base, "mode": "own_rereview",
                                                   "revised_document_id": revision["id"],
                                                   "response_document_id": response["id"]})
    assert own.status_code == 201
    new = client.post("/api/review-cases", json={**base, "mode": "new_reviewer",
                                                   "revised_document_id": revision["id"]})
    assert new.status_code == 201
    assert client.get(f'/api/review-cases/{own.json()["id"]}').json()["response_document_id"] == response["id"]
    assert len(TestClient(create_app(tmp_path)).get("/api/review-cases").json()) == 3


def test_candidate_screening_keeps_unsupported_issue_out_of_verified_state():
    available = {"ev_a": {"document_id": "doc1", "excerpt": "The baseline is 80 percent.", "page_index": 0}}
    candidates = [
        {"title": "Baseline mismatch", "concern": "The baseline is inconsistent.", "evidence_ids": ["ev_a"]},
        {"title": "Imaginary result", "concern": "Another table contradicts it.", "evidence_ids": ["ev_missing"]},
    ]
    checks = [
        {"index": 0, "verdict": "supported", "support_assessment": "The located block gives the baseline, but further comparison is needed.", "counterargument": "No alternative in supplied text."},
        {"index": 1, "verdict": "supported", "support_assessment": "Unsupported because the ID is absent.", "counterargument": "None."},
    ]
    findings = screen_candidates(candidates, checks, available)
    assert findings[0]["evidence_level"] == "source_located"
    assert findings[0]["verification_status"] == "needs_independent_verification"
    assert findings[0]["reviewer_decision"] == "pending"
    assert "located block" in findings[0]["support_assessment"]
    assert findings[1]["verification_status"] == "rejected"
    assert findings[1]["evidence"] == []


def test_model_json_parser_accepts_fenced_json():
    assert parse_model_json('```json\n{"candidates": []}\n```') == {"candidates": []}


def test_review_sampling_keeps_a_short_author_reply_among_long_manuscripts():
    blocks = [{"id": f"ev_{document}_{page}", "document_id": document,
               "page_index": page, "excerpt": f"Results from page {page}"}
              for document, pages in (("original", 80), ("revision", 80), ("reply", 1))
              for page in range(pages)]
    selected = pick_review_evidence(blocks, limit=30)
    assert len(selected) <= 30
    assert {row["document_id"] for row in selected} == {"original", "revision", "reply"}


def test_deep_counterevidence_retrieval_preserves_citation_and_finds_other_context():
    blocks = [
        {"id": "ev_cited", "document_id": "paper", "page_index": 2,
         "excerpt": "Overall self-attention space complexity includes relative position representations."},
        {"id": "ev_elsewhere", "document_id": "paper", "page_index": 4,
         "excerpt": "Relative position representations are shared across attention heads and sequences."},
        {"id": "ev_noise", "document_id": "paper", "page_index": 0,
         "excerpt": "The author affiliation is listed here."},
    ]
    selected = retrieve_counterevidence(blocks, {"ev_cited"}, limit=2)
    assert [item["id"] for item in selected] == ["ev_cited", "ev_elsewhere"]


def test_deep_counterevidence_keeps_short_author_reply():
    blocks = [{"id": "ev_original", "document_id": "original", "page_index": 0,
               "excerpt": "Attention memory complexity requires clarification."},
              {"id": "ev_revision", "document_id": "revision", "page_index": 0,
               "excerpt": "Revised complexity statement with assumptions."},
              {"id": "ev_reply", "document_id": "reply", "page_index": 0,
               "excerpt": "We addressed the reviewer's concern."}]
    selected = retrieve_counterevidence(blocks, {"ev_original"}, limit=3)
    assert {row["document_id"] for row in selected} == {"original", "revision", "reply"}


def test_explicit_equation_rewrite_creates_traceable_link_only():
    blocks = [
        {"id": "ev_four", "document_id": "paper", "page_index": 1, "excerpt": "e = q(k+a) (4)"},
        {"id": "ev_context", "document_id": "paper", "page_index": 2,
         "excerpt": "We split the computation of eq. (4) into two terms:"},
        {"id": "ev_five", "document_id": "paper", "page_index": 2, "excerpt": "e = qk + qa (5)"},
    ]
    links = suggest_formula_links(blocks)
    assert [(link["from_equation"], link["to_equation"], link["relation"]["id"])
            for link in links] == [(4, 5, "ev_context")]
    assert suggest_formula_links([blocks[0], blocks[2]]) == []


@pytest.mark.parametrize("mode,need_reply", [("own_rereview", True), ("new_reviewer", False)])
def test_rereview_model_receives_named_material_roles(tmp_path, monkeypatch, mode, need_reply):
    client = TestClient(create_app(tmp_path))
    original = upload(client, "original.pdf")
    revision = upload(client, "revision.pdf")
    reply = upload(client, "reply.pdf")
    payload = {"mode": mode, "original_document_id": original["id"],
               "revised_document_id": revision["id"]}
    if need_reply:
        payload["response_document_id"] = reply["id"]
    case = client.post("/api/review-cases", json=payload).json()
    seen = []

    class FakeCompletions:
        def create(self, *, model, messages):
            seen.append(messages[1]["content"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"candidates": []}'))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    assert client.post(f'/api/review-cases/{case["id"]}/analyze').status_code == 202
    assert client.get(f'/api/review-cases/{case["id"]}').json()["latest_run"]["status"] == "completed"
    assert "原稿，PDF 第" in seen[0]
    assert "修订稿，PDF 第" in seen[0]
    assert ("作者回复，PDF 第" in seen[0]) == need_reply


def test_review_analysis_persists_screened_findings_and_usage(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    original = upload(client, "original.pdf")
    case = client.post("/api/review-cases", json={"mode": "first_review",
                                                   "original_document_id": original["id"]}).json()

    class FakeCompletions:
        def create(self, *, model, messages):
            if "独立的科研审稿复核员" in messages[0]["content"]:
                evidence_id = re.search(r"\[(ev_[^]]+)\]", messages[1]["content"]).group(1)
                value = {"verdict": "supported_clarification",
                         "support_assessment": "The source gives a baseline, but scope remains unclear.",
                         "rebuttal": "The paper may define a narrower baseline.",
                         "missing_checks": "Compare the full experimental setup.",
                         "evidence_ids": [evidence_id]}
            elif "反证员" in messages[0]["content"]:
                value = {"checks": [{"index": 0, "verdict": "supported",
                                     "support_assessment": "The cited block states the baseline, but the mismatch needs comparison.",
                                     "counterargument": "Only one source block; independent checking is still needed."}]}
            else:
                evidence_id = re.search(r"\[(ev_[^]]+)\]", messages[1]["content"]).group(1)
                value = {"candidates": [{"title": "Baseline needs checking",
                                         "concern": "Check whether the baseline is consistent.",
                                         "evidence_ids": [evidence_id]}]}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))],
                                   usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10))

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    result = client.post(f'/api/review-cases/{case["id"]}/analyze')
    assert result.status_code == 202
    detail = client.get(f'/api/review-cases/{case["id"]}').json()
    assert detail["latest_run"]["status"] == "completed"
    assert len(detail["model_calls"]) == 2
    assert all(call["input_tokens"] == 20 for call in detail["model_calls"])
    finding = detail["findings"][0]
    assert finding["verification_status"] == "needs_independent_verification"
    assert "cited block" in finding["support_assessment"]
    assert finding["evidence"][0]["document_id"] == original["id"]
    assert finding["reviewer_decision"] == "pending"
    anchor_id = finding["evidence"][0]["evidence_id"]
    checked = client.post(f'/api/review-findings/{finding["id"]}/equation-checks', json={
        "lhs": "x+x", "rhs": "3*x", "assumptions": {"x": "real"}, "evidence_ids": [anchor_id],
    })
    assert checked.status_code == 201
    assert checked.json()["result"]["status"] == "counterexample"
    assert checked.json()["transcription_status"] == "unconfirmed"
    assert client.get(f'/api/review-findings/{finding["id"]}/equation-checks').json()[0]["id"] == checked.json()["id"]
    assert client.get(f'/api/review-cases/{case["id"]}').json()["findings"][0]["verification_status"] == "needs_independent_verification"
    invalid = client.post(f'/api/review-findings/{finding["id"]}/equation-checks', json={
        "lhs": "x", "rhs": "0", "evidence_ids": ["ev_missing"],
    })
    assert invalid.status_code == 400
    deep = client.post(f'/api/review-findings/{finding["id"]}/deep-verifications')
    assert deep.status_code == 202
    verification = client.get(f'/api/review-findings/{finding["id"]}/deep-verifications').json()[0]
    assert verification["status"] == "completed"
    assert verification["result"]["verdict"] == "supported_clarification"
    assert verification["result"]["evidence_ids"] == [anchor_id]
    detail = client.get(f'/api/review-cases/{case["id"]}').json()
    assert [call["stage"] for call in detail["model_calls"]] == ["propose", "challenge", "deep_challenge"]
    assert detail["findings"][0]["verification_status"] == "needs_independent_verification"


def test_case_cross_formula_check_requires_two_sources_and_persists(tmp_path):
    client = TestClient(create_app(tmp_path))
    paper = upload(client, "formula.pdf")
    case = client.post("/api/review-cases", json={"mode": "first_review",
                                                   "original_document_id": paper["id"]}).json()
    source = client.get(f'/api/documents/{paper["id"]}/search', params={"q": "baseline"}).json()
    # The PDF has one baseline block, so add a second independently located source block.
    second = client.get(f'/api/documents/{paper["id"]}/search', params={"q": "Paper"}).json()[0]
    ids = [source[0]["evidence_id"], second["evidence_id"]]
    payload = {"lhs": "(x+y)**2", "rhs": "x**2+2*x*y+y**2",
               "assumptions": {}, "evidence_ids": ids}
    path = f'/api/review-cases/{case["id"]}/cross-formula-checks'
    assert client.post(path, json={**payload, "evidence_ids": ids[:1]}).status_code == 400
    assert client.post(path, json={**payload, "evidence_ids": [ids[0], "ev_missing"]}).status_code == 400
    created = client.post(path, json=payload)
    assert created.status_code == 201
    assert created.json()["result"]["status"] == "equivalent"
    assert created.json()["source_status"] == "unconfirmed"
    assert [item["evidence_id"] for item in created.json()["sources"]] == ids
    restored = TestClient(create_app(tmp_path)).get(path).json()
    assert restored[0]["id"] == created.json()["id"]
    assert restored[0]["sources"][0]["excerpt"]
