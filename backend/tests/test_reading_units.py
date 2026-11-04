import json

import fitz

from backend.source_bundle import build_source_bundle
from backend import parse_quality


def _document(path, sections, rows, page_count=1):
    return ({"id": "doc", "filename": "paper.pdf", "sha256": "hash", "snapshot_id": "snap",
             "page_count": page_count, "sections": json.dumps(sections),
             "pages": json.dumps([{"page_index": index, "width": 600, "height": 800,
                                    "rotation": 0, "needs_ocr": not bool(rows)}
                                   for index in range(page_count)])},
            [{"id": key, "document_id": "doc", "page_index": page,
              "excerpt": text, "bbox": json.dumps(box), "precision": "block"}
             for key, page, text, box in rows])


def test_two_columns_order_sections_and_exact_evidence_mapping(tmp_path):
    path = tmp_path / "columns.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    for x, y, text in [(50, 80, "1 Introduction"), (50, 120, "Left first."),
                       (50, 160, "Left second."), (320, 80, "2 Results"),
                       (320, 120, "Right first."), (320, 160, "Right second.")]:
        page.insert_text((x, y), text)
    pdf.save(path)
    pdf.close()
    rows = [("l1", 0, "1 Introduction", [50, 65, 200, 82]),
            ("r1", 0, "2 Results", [320, 65, 470, 82]),
            ("l2", 0, "Left first.", [50, 105, 200, 122]),
            ("r2", 0, "Right first.", [320, 105, 470, 122]),
            ("l3", 0, "Left second.", [50, 145, 200, 162]),
            ("r3", 0, "Right second.", [320, 145, 470, 162])]
    sections = [{"title": "1 Introduction", "level": 1, "page_index": 0, "evidence_id": "l1"},
                {"title": "2 Results", "level": 1, "page_index": 0, "evidence_id": "r1"}]
    doc, evidence = _document(path, sections, rows)

    bundle = build_source_bundle(doc, evidence, path)
    units = bundle["reading_units"]

    assert [unit["text"] for unit in units] == [row[2] for row in (rows[0], rows[2], rows[4], rows[1], rows[3], rows[5])]
    assert [unit["section_id"] for unit in units] == ["section_0"] * 3 + ["section_1"] * 3
    assert [unit["section_order"] for unit in units] == [0, 1, 2, 0, 1, 2]
    assert all(unit["evidence_mapping_status"] == "exact_block" for unit in units)
    assert all(unit["reading_order_status"] == "geometric_candidate" for unit in units)
    assert units[0]["evidence_ids"] == ["l1"]
    assert units[0]["bbox"] == [50, 65, 200, 82]
    assert [unit["id"] for unit in units] == [f"ru_{key}" for key in ("l1", "l2", "l3", "r1", "r2", "r3")]
    assert bundle["body"][1]["evidence_id"] == "r1"
    assert bundle["sections"][0]["reading_unit_ids"] == ["ru_l1", "ru_l2", "ru_l3"]
    quality = parse_quality.assess_parse_quality(bundle)
    assert quality["pages"][0]["reading_order_status"] == "geometric_candidate"
    assert quality["pages"][0]["mapped_evidence_count"] == 6


def test_collapsed_table_rows_are_split_only_with_aligned_columns(tmp_path):
    path = tmp_path / "table.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    for x in (50, 180, 310, 480):
        page.draw_line((x, 100), (x, 260))
    for y in (100, 130, 260):
        page.draw_line((50, y), (480, y))
    for x, text in ((60, "A"), (190, "B"), (320, "Score")):
        page.insert_text((x, 120), text)
    for y, values in ((150, ("Yes", "Yes", "25.8")),
                      (180, ("No", "Yes", "25.8")),
                      (210, ("Yes", "No", "25.3")),
                      (240, ("No", "No", "12.5"))):
        for x, text in zip((60, 190, 320), values):
            page.insert_text((x, y), text)
    page.insert_text((50, 285), "Table 1: Ablation")
    pdf.save(path)
    pdf.close()
    doc, evidence = _document(path, [], [("values", 0, "A B Score Yes No Yes No Yes Yes No No 25.8 25.8 25.3 12.5", [50, 100, 480, 260]),
                                         ("caption", 0, "Table 1: Ablation", [50, 270, 250, 290])])
    bundle = build_source_bundle(doc, evidence, path)
    table = next(item for item in bundle["tables"] if item["caption_evidence_id"] == "caption")

    assert table["structured_rows"][1:] == [["Yes", "Yes", "25.8"], ["No", "Yes", "25.8"],
                                             ["Yes", "No", "25.3"], ["No", "No", "12.5"]]
    assert table["structure_status"] == "aligned_text_candidate"
    row_units = [unit for unit in bundle["reading_units"] if unit.get("table_id") == table["id"]]
    assert len(row_units) == 5
    assert row_units[1]["evidence_ids"] == ["values"]
    assert row_units[1]["evidence_mapping_status"] == "source_block_partial"
    assert row_units[1]["bbox"] == table["bbox"]


def test_unmatched_or_scanned_content_does_not_become_ready(tmp_path):
    path = tmp_path / "blank.pdf"
    pdf = fitz.open()
    pdf.new_page(width=600, height=800)
    pdf.save(path)
    pdf.close()
    doc, evidence = _document(path, [], [], page_count=1)
    bundle = build_source_bundle(doc, evidence, path)

    assert bundle["reading_units"] == []
    assert parse_quality.assess_input_readiness(bundle, "section_summary")["status"] == "blocked"
    assert parse_quality.assess_input_readiness(bundle, "formula_check")["status"] == "blocked"


def test_reference_is_required_for_ready_and_node_requirements_differ(tmp_path):
    path = tmp_path / "paper.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((50, 80), "1 Method")
    page.insert_text((50, 120), "A short method description.")
    page.insert_text((50, 160), "E = mc2 (1)")
    pdf.save(path)
    pdf.close()
    rows = [("h", 0, "1 Method", [50, 65, 200, 82]),
            ("b", 0, "A short method description.", [50, 105, 280, 122]),
            ("f", 0, "E = mc2 (1)", [50, 145, 180, 162])]
    doc, evidence = _document(path, [{"title": "1 Method", "level": 1, "page_index": 0,
                                      "evidence_id": "h"}], rows)
    bundle = build_source_bundle(doc, evidence, path)
    assert parse_quality.assess_input_readiness(bundle, "section_summary")["status"] == "needs_review"
    assert parse_quality.assess_input_readiness(bundle, "formula_check")["status"] == "blocked"
    checked = {"source": "human_pdf_review", "reference_id": "review-1",
               "verified_unit_ids": [unit["id"] for unit in bundle["reading_units"]],
               "verified_formula_ids": [formula["id"] for formula in bundle["formulas"]]}
    assert parse_quality.assess_input_readiness(bundle, "section_summary", reference=checked)["status"] == "needs_review"
    assert parse_quality.assess_input_readiness(bundle, "formula_check", reference=checked)["status"] == "blocked"
    checked["verified_page_indices"] = [0]
    checked["verified_inventory_nodes"] = ["formula_check"]
    assert parse_quality.assess_input_readiness(bundle, "section_summary", reference=checked)["status"] == "ready"
    assert parse_quality.assess_input_readiness(bundle, "formula_check", reference=checked)["status"] == "ready"


def test_split_equation_keeps_number_and_nearby_expression_as_unverified(tmp_path):
    path = tmp_path / "equation.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((320, 120), "x + y = z")
    page.insert_text((510, 120), "(2)")
    pdf.save(path)
    pdf.close()
    doc, evidence = _document(path, [], [("expr", 0, "x + y = z", [320, 105, 430, 122]),
                                         ("number", 0, "(2)", [510, 105, 530, 122])])
    formula = build_source_bundle(doc, evidence, path)["formulas"][0]
    assert formula["number"] == "2"
    assert formula["number_evidence_id"] == "number"
    assert formula["evidence_ids"] == ["expr", "number"]
    assert formula["bbox"] == [320, 105, 530, 122]
    assert formula["transcription_status"] == "unverified_grouped_expression"


def test_ambiguous_cross_gutter_block_downgrades_reading_order(tmp_path):
    path = tmp_path / "ambiguous.pdf"
    pdf = fitz.open()
    pdf.new_page(width=600, height=800)
    pdf.save(path)
    pdf.close()
    rows = [("h", 0, "1 Method", [50, 50, 200, 70]),
            ("l", 0, "Left body", [50, 100, 240, 120]),
            ("l2", 0, "Left tail", [50, 150, 240, 170]),
            ("r", 0, "Right body", [330, 100, 520, 120]),
            ("r2", 0, "Right tail", [330, 150, 520, 170]),
            ("cross", 0, "Unclear floating fragment", [280, 180, 330, 200])]
    doc, evidence = _document(path, [{"title": "1 Method", "level": 1,
                                      "page_index": 0, "evidence_id": "h"}], rows)
    bundle = build_source_bundle(doc, evidence, path)
    assert {unit["reading_order_status"] for unit in bundle["reading_units"]} == {"unverified"}
    assert parse_quality.assess_input_readiness(bundle, "section_summary")["status"] == "needs_review"
