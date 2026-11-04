import json

import fitz

from backend.source_bundle import build_source_bundle


def test_bundle_keeps_legacy_evidence_and_marks_unverified_structure(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((50, 70), "1 Introduction")
    page.insert_text((50, 110), "A left-column method statement.")
    page.insert_text((320, 110), "A right-column result statement.")
    page.insert_text((50, 160), "E = mc2 (1)")
    page.insert_text((50, 210), "Figure 1: Example diagram")
    page.insert_text((50, 250), "Table 1: Results without machine-readable rows")
    pdf.new_page(width=600, height=800)
    pdf.save(pdf_path)
    pdf.close()
    document = {
        "id": "doc1", "filename": "paper.pdf", "sha256": "hash1", "snapshot_id": "snap1",
        "page_count": 2, "sections": json.dumps([{"title": "1 Introduction", "page_index": 0,
                                                   "level": 1, "evidence_id": "ev1"}]),
        "pages": json.dumps([{"page_index": 0, "width": 600, "height": 800, "rotation": 0,
                              "needs_ocr": False},
                             {"page_index": 1, "width": 600, "height": 800, "rotation": 0,
                              "needs_ocr": True}]),
    }
    evidence = [
        {"id": "ev1", "document_id": "doc1", "page_index": 0, "excerpt": "1 Introduction",
         "bbox": json.dumps([50, 55, 170, 75]), "precision": "block"},
        {"id": "ev2", "document_id": "doc1", "page_index": 0,
         "excerpt": "A left-column method statement.", "bbox": json.dumps([50, 95, 260, 115]),
         "precision": "block"},
        {"id": "ev3", "document_id": "doc1", "page_index": 0,
         "excerpt": "A right-column result statement.", "bbox": json.dumps([320, 95, 540, 115]),
         "precision": "block"},
        {"id": "ev4", "document_id": "doc1", "page_index": 0, "excerpt": "E = mc2 (1)",
         "bbox": json.dumps([50, 145, 170, 165]), "precision": "block"},
        {"id": "ev5", "document_id": "doc1", "page_index": 0,
         "excerpt": "Figure 1: Example diagram", "bbox": json.dumps([50, 195, 260, 215]),
         "precision": "block"},
        {"id": "ev6", "document_id": "doc1", "page_index": 0,
         "excerpt": "Table 1: Results without machine-readable rows",
         "bbox": json.dumps([50, 235, 330, 255]), "precision": "block"},
    ]

    bundle = build_source_bundle(document, evidence, pdf_path)

    assert bundle["schema_version"] == "1.0"
    assert bundle["document"]["snapshot_id"] == "snap1"
    assert bundle["document"]["sha256"] == "hash1"
    assert bundle["pages"][1]["status"] == "needs_ocr"
    assert bundle["pages"][1]["evidence_ids"] == []
    assert bundle["body"][1]["evidence_id"] == "ev2"
    assert bundle["body"][1]["bbox"] == [50, 95, 260, 115]
    assert bundle["sections"][0]["evidence_id"] == "ev1"
    assert bundle["formulas"][0]["number"] == "1"
    assert bundle["formulas"][0]["status"] == "transcription_unverified"
    assert bundle["figures"][0]["caption_evidence_id"] == "ev5"
    assert bundle["figures"][0]["status"] == "visual_unreviewed"
    assert bundle["tables"][0]["status"] == "missing_structure"
    assert bundle["tables"][0]["caption_evidence_id"] == "ev6"
    assert all(key in bundle for key in ("tables", "references", "limitations"))
