from backend.parse_quality import assess_parse_quality, assess_input_readiness


def test_quality_report_does_not_claim_accuracy_without_human_reference():
    bundle = {
        "schema_version": "1.0", "bundle_id": "bundle1",
        "pages": [
            {"page_index": 0, "status": "text_extracted", "width": 600,
             "evidence_ids": ["left1", "left2", "right1", "right2"]},
            {"page_index": 1, "status": "needs_ocr", "width": 600, "evidence_ids": []},
        ],
        "body": [
            {"evidence_id": "left1", "page_index": 0, "bbox": [40, 100, 250, 120], "text": "left one"},
            {"evidence_id": "right1", "page_index": 0, "bbox": [330, 100, 550, 120], "text": "right one"},
            {"evidence_id": "left2", "page_index": 0, "bbox": [40, 140, 250, 160], "text": "left two"},
            {"evidence_id": "right2", "page_index": 0, "bbox": [330, 140, 550, 160], "text": "right two"},
        ],
        "sections": [{"id": "s1", "title": "2 Background", "page_index": 0,
                      "evidence_id": "left1", "status": "candidate"},
                     {"id": "s2", "title": "3 Method", "page_index": 0,
                      "evidence_id": "left2", "status": "candidate"},
                     {"id": "s3", "title": "2.1 Prior Work", "page_index": 0,
                      "evidence_id": "right2", "status": "candidate"}],
        "tables": [{"id": "t1", "page_index": 0, "row_count": 2, "col_count": 3,
                    "cells": [["A", "B", "C"], ["yes\nno", "yes\nno", "25.8\n25.3"]],
                    "status": "candidate"}],
        "formulas": [{"id": "f1", "page_index": 0, "status": "transcription_unverified"}],
        "figures": [{"id": "g1", "page_index": 0, "status": "visual_unreviewed"}],
        "references": [], "limitations": [],
    }

    report = assess_parse_quality(bundle)

    assert report["source_bundle_id"] == "bundle1"
    assert report["accuracy"]["status"] == "not_measured"
    assert report["accuracy"]["requires_human_reference"] is True
    assert "reading_order_unverified" in report["pages"][0]["issues"]
    assert "text_missing_or_image_only" in report["pages"][1]["issues"]
    assert "row_alignment_unverified" in report["tables"][0]["issues"]
    assert report["formulas"][0]["status"] == "transcription_unverified"
    assert report["figures"][0]["status"] == "visual_unreviewed"
    assert "visual_region_unlocated" in report["figures"][0]["issues"]
    assert "section_order_unverified" in report["sections"][2]["issues"]
    assert "section_body_boundaries_unverified" in report["sections"][0]["issues"]
    assert report["inventory_completeness"] == "unverified"


def test_column_detection_handles_narrow_gutter():
    bundle = {"bundle_id": "bundle2", "pages": [{"page_index": 0, "status": "text_extracted",
               "width": 595, "evidence_ids": ["a", "b", "c", "d"]}],
              "body": [{"evidence_id": key, "page_index": 0, "bbox": box, "text": key}
                       for key, box in [("a", [72, 100, 290, 120]), ("b", [72, 140, 290, 160]),
                                        ("c", [307, 100, 526, 120]), ("d", [307, 140, 526, 160])]],
              "sections": [], "tables": [], "formulas": [], "figures": [],
              "references": [], "limitations": []}
    report = assess_parse_quality(bundle)
    assert "reading_order_unverified" in report["pages"][0]["issues"]


def test_wrapped_table_header_does_not_imply_collapsed_data_rows():
    bundle = {"bundle_id": "bundle3", "pages": [], "body": [], "sections": [],
              "tables": [{"id": "table1", "page_index": 0, "row_count": 3, "col_count": 2,
                          "cells": [["Model\nversion", "Score"], ["A", "1.0"], ["B", "2.0"]],
                          "caption_evidence_id": "cap", "status": "candidate"}],
              "formulas": [], "figures": [], "references": [], "limitations": []}
    report = assess_parse_quality(bundle)
    assert "row_alignment_unverified" not in report["tables"][0]["issues"]


def test_table_false_positive_and_missing_structure_are_explicit():
    bundle = {"bundle_id": "bundle4", "pages": [], "body": [], "reading_units": [], "sections": [],
              "tables": [{"id": "false", "page_index": 0, "row_count": 1, "col_count": 3,
                          "cells": [["x", "y", "z"]], "status": "candidate",
                          "structure_status": "possible_false_positive"},
                         {"id": "missing", "page_index": 0, "row_count": 0, "col_count": 0,
                          "cells": [], "status": "missing_structure",
                          "structure_status": "structure_incomplete"}],
              "formulas": [], "figures": [], "references": [], "limitations": []}
    report = assess_parse_quality(bundle)
    assert "possible_false_positive" in report["tables"][0]["issues"]
    assert "table_structure_missing" in report["tables"][1]["issues"]
    assert report["input_readiness"]["table_review"]["status"] == "blocked"


def test_scoped_objects_require_every_id_on_the_selected_document_page_and_section():
    bundle = {"document": {"id": "doc1"},
              "pages": [{"page_index": 0, "status": "text_extracted", "evidence_ids": ["e1"]},
                        {"page_index": 1, "status": "text_extracted", "evidence_ids": ["e2"]}],
              "reading_units": [{"id": "u1", "page_index": 0, "section_id": "s1",
                                 "evidence_ids": ["e1"], "evidence_mapping_status": "exact_block",
                                 "reading_order_status": "geometric_candidate", "text": "Table 1"},
                                {"id": "u2", "page_index": 1, "section_id": "s2",
                                 "evidence_ids": ["e2"], "evidence_mapping_status": "exact_block",
                                 "reading_order_status": "geometric_candidate", "text": "Table 2"}],
              "tables": [{"id": "t1", "document_id": "doc1", "page_index": 0,
                          "section_id": "s1", "caption_evidence_id": "e1",
                          "structure_status": "detected_unverified"},
                         {"id": "t2", "document_id": "doc1", "page_index": 1,
                          "section_id": "s2", "caption_evidence_id": "e2",
                          "structure_status": "detected_unverified"},
                         {"id": "foreign", "document_id": "doc2", "page_index": 0,
                          "section_id": "s1", "caption_evidence_id": "e1",
                          "structure_status": "detected_unverified"}],
              "formulas": [], "figures": []}
    reference = {"source": "human_pdf_review", "reference_id": "check1",
                 "verified_unit_ids": ["u1", "u2"],
                 "verified_table_ids": ["t1", "t2", "foreign"]}

    def status(ids, page_indices=(0,), section_ids=("s1",), document_id="doc1"):
        return assess_input_readiness(bundle, "table_review",
                                      {"object_ids": ids, "page_indices": page_indices,
                                       "section_ids": section_ids, "document_id": document_id},
                                      reference)["status"]

    assert status(["t1"]) == "ready"
    assert status(["t1", "missing"]) == "blocked"
    assert status(["t1", "t2"]) == "blocked"
    assert status(["t1", "foreign"]) == "blocked"
    assert status(["t1"], section_ids=("s2",)) == "blocked"
    assert status(["t1"], document_id="doc2") == "blocked"
    bundle["tables"][0]["reading_unit_status"] = "raw_evidence_fallback"
    assert status(["t1"]) == "blocked"
