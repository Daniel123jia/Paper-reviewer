"""Selected human landmarks; no claim of whole-paper parsing accuracy."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


REFERENCE = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/p1_parse_reference.json").read_text())


def _bundle(tmp_path, env_name, reference_name):
    source = os.environ.get(env_name)
    if not source:
        pytest.skip(f"{env_name} is not set")
    raw = Path(source).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REFERENCE[reference_name]["sha256"]
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/documents", files={"file": ("reference.pdf", raw, "application/pdf")})
    assert response.status_code == 201
    document_id = response.json()["id"]
    bundle = client.get(f"/api/documents/{document_id}/source-bundle").json()
    quality = client.get(f"/api/documents/{document_id}/parse-quality").json()
    assert client.get(f"/api/documents/{document_id}/pdf").content == raw
    return bundle, quality


def test_shaw_section_order_table_and_source_mapping(tmp_path):
    bundle, quality = _bundle(tmp_path, "P11_SHAW_PDF", "shaw2018")
    titles = [section["title"].replace("ﬁ", "fi") for section in bundle["sections"]]
    selected = [title for title in titles if title.startswith(("3.2 ", "3.3 ", "4.1 ", "4.2 ", "4.3 "))]
    assert [title.split()[0] for title in selected] == ["3.2", "3.3", "4.1", "4.2", "4.3"]
    assert all(section["reading_unit_ids"] for section in bundle["sections"] if section["title"] in selected)
    table3 = next(table for table in bundle["tables"] if table["number"] == "3")
    assert [" ".join(row) for row in table3["structured_rows"][1:]] == REFERENCE["shaw2018"]["table_rows"]
    assert table3["structure_status"] == "aligned_text_candidate"
    assert table3["usability_status"] == "detected_unverified"
    assert all(unit["evidence_ids"] and unit["bbox"] for unit in bundle["reading_units"])
    assert len([unit for unit in bundle["reading_units"] if unit.get("table_id") == table3["id"]]) == 5
    assert any(table["number"] == "1" and table["structure_status"] == "structure_incomplete"
               for table in bundle["tables"])
    assert [table["number"] for table in bundle["tables"] if table["number"]] == ["1", "2", "3"]
    assert any(table["structure_status"] == "possible_false_positive" for table in bundle["tables"])
    assert {formula["number"] for formula in bundle["formulas"]} == {"1", "2", "3", "4", "5"}
    assert [formula["number"] for formula in bundle["formulas"]] == ["1", "2", "3", "4", "5"]
    assert all(formula["bbox"] and formula["evidence_ids"] for formula in bundle["formulas"])
    assert bundle["figures"][0]["visual_link_status"] == "missing_or_vector"
    assert bundle["figures"][0]["usability_status"] == "visual_review_required"
    assert all(formula["usability_status"] in {"detected_unverified", "structure_incomplete"}
               for formula in bundle["formulas"])
    assert len(bundle["references"]) > 20
    assert quality["input_readiness"]["section_summary"]["status"] == "needs_review"
    assert quality["input_readiness"]["formula_check"]["status"] == "blocked"
    table_units = [unit for unit in bundle["reading_units"] if unit.get("table_id") == table3["id"] or
                   table3["caption_evidence_id"] in unit["evidence_ids"]]
    human_check = {"source": "human_pdf_review", "reference_id": "shaw-table3-visible-pages",
                   "verified_unit_ids": [unit["id"] for unit in table_units],
                   "verified_table_ids": [table3["id"]]}
    from backend.parse_quality import assess_input_readiness
    targeted = assess_input_readiness(bundle, "table_review",
                                      scope={"page_indices": [table3["page_index"]],
                                             "object_ids": [table3["id"]]}, reference=human_check)
    assert targeted["status"] == "ready"
    missing = assess_input_readiness(bundle, "table_review",
                                     scope={"page_indices": [table3["page_index"]],
                                            "object_ids": ["missing-table"]}, reference=human_check)
    assert missing["status"] == "blocked"


def test_jmlr_one_column_remains_ordered_and_evidence_backed(tmp_path):
    bundle, quality = _bundle(tmp_path, "P11_JMLR_PDF", "jmlr2024")
    assert len(bundle["pages"]) == REFERENCE["jmlr2024"]["page_count"]
    assert all(unit["evidence_ids"] and unit["bbox"] for unit in bundle["reading_units"])
    assert all(section["reading_order"] is not None for section in bundle["sections"])
    assert any(table["structure_status"] == "detected_unverified" for table in bundle["tables"])
    assert quality["input_readiness"]["section_summary"]["status"] == "needs_review"
