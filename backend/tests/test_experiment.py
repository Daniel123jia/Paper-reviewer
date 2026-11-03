from pathlib import Path
import os

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.experiment import audit_paired_bleu_table


TABLE = """Model
Position Information
EN-DE BLEU
EN-FR BLEU
Transformer (base)
Absolute Position Representations
26.5
38.2
Transformer (base)
Relative Position Representations
26.8
38.7
Transformer (big)
Absolute Position Representations
27.9
41.2
Transformer (big)
Relative Position Representations
29.2
41.5"""
CLAIM = """For English-to-German our approach improved performance over our baseline by 0.3 and 1.3 BLEU for the base and big configurations, respectively. For English-to-French it improved by 0.5 and 0.3 BLEU for the base and big configurations, respectively."""


def test_paired_table_checks_all_four_claims_without_float_rounding():
    result = audit_paired_bleu_table(TABLE, CLAIM)
    assert result["status"] == "consistent"
    assert [(item["metric"], item["group"], item["calculated"], item["claimed"], item["matches"])
            for item in result["checks"]] == [
                ("EN-DE BLEU", "base", "0.3", "0.3", True),
                ("EN-DE BLEU", "big", "1.3", "1.3", True),
                ("EN-FR BLEU", "base", "0.5", "0.5", True),
                ("EN-FR BLEU", "big", "0.3", "0.3", True),
            ]


def test_paired_table_detects_claim_mismatch_but_rejects_ambiguous_layout():
    wrong = CLAIM.replace("by 0.3 and 1.3 BLEU", "by 0.4 and 1.3 BLEU")
    result = audit_paired_bleu_table(TABLE, wrong)
    assert result["status"] == "numeric_mismatch"
    assert result["checks"][0]["matches"] is False
    assert audit_paired_bleu_table(TABLE.replace("EN-FR BLEU", "Unknown metric"), CLAIM)["status"] == "inconclusive"
    assert audit_paired_bleu_table(TABLE, "The method improved BLEU.")["status"] == "inconclusive"


def test_real_pdf_experiment_audit_is_evidence_backed_and_persistent(tmp_path):
    path = Path(os.environ.get("REAL_PDF_PATH", "tests/fixtures/Self-Attention with Relative Position Representations.pdf"))
    if not path.exists():
        pytest.skip("real PDF not available")
    client = TestClient(create_app(tmp_path))
    with path.open("rb") as stream:
        document = client.post("/api/documents", files={"file": (path.name, stream, "application/pdf")}).json()
    case = client.post("/api/review-cases", json={"mode": "first_review", "original_document_id": document["id"]}).json()
    links = client.get(f'/api/review-cases/{case["id"]}/formula-links').json()
    assert any(item["from_equation"] == 4 and item["to_equation"] == 5
               and item["left"]["page_index"] == 1 and item["right"]["page_index"] == 2
               for item in links)
    created = client.post(f'/api/review-cases/{case["id"]}/experiment-audits')
    assert created.status_code == 201
    audit = created.json()
    assert audit["result"]["status"] == "consistent"
    assert len(audit["result"]["checks"]) == 4
    assert audit["table_source"]["page_index"] == 3
    assert audit["claim_source"]["page_index"] == 3
    assert audit["source_status"] == "extracted_text_only"
    restored = TestClient(create_app(tmp_path)).get(f'/api/review-cases/{case["id"]}/experiment-audits').json()
    assert restored[0]["id"] == audit["id"]
