from pathlib import Path
import os

import pytest
from fastapi.testclient import TestClient

from backend.algorithmcheck import check_matmul_description
from backend.app import create_app


def test_two_attention_computation_routes_have_same_output_size():
    baseline = check_matmul_description("bh parallel multiplications of n×dz and dz ×n matrices.")
    relative = check_matmul_description("n parallel multiplications of bh×dz and dz×n matrices.")
    assert baseline["status"] == relative["status"] == "shape_consistent"
    assert baseline["output_shape"] == ["n", "n"]
    assert relative["output_shape"] == ["bh", "n"]
    assert baseline["total_output_elements"] == relative["total_output_elements"] == "b*h*n**2"


def test_matrix_inner_dimension_mismatch_is_flagged():
    result = check_matmul_description("n parallel multiplications of bh×dz and dk×n matrices.")
    assert result["status"] == "shape_mismatch"
    assert result["total_output_elements"] is None
    assert check_matmul_description("The model uses attention.") is None


def test_real_pdf_algorithm_audit_is_linked_to_paper_and_persistent(tmp_path):
    path = Path(os.environ.get("REAL_PDF_PATH", "tests/fixtures/Self-Attention with Relative Position Representations.pdf"))
    if not path.exists():
        pytest.skip("real PDF not available")
    client = TestClient(create_app(tmp_path))
    with path.open("rb") as stream:
        paper = client.post("/api/documents", files={"file": (path.name, stream, "application/pdf")}).json()
    case = client.post("/api/review-cases", json={"mode": "first_review", "original_document_id": paper["id"]}).json()
    created = client.post(f'/api/review-cases/{case["id"]}/algorithm-audits')
    assert created.status_code == 201
    audit = created.json()
    assert audit["result"]["status"] == "shape_consistent"
    assert len(audit["result"]["checks"]) == 2
    assert all(item["source"]["page_index"] == 2 for item in audit["result"]["checks"])
    assert audit["result"]["checks"][0]["total_output_elements"] == "b*h*n**2"
    restored = TestClient(create_app(tmp_path)).get(f'/api/review-cases/{case["id"]}/algorithm-audits').json()
    assert restored[0]["id"] == audit["id"]
