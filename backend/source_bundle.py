"""Versioned paper source bundle derived from the immutable legacy Evidence snapshot."""

import json
import re
from pathlib import Path

import fitz

from backend.reading_order import build_reading_units


SCHEMA_VERSION = "1.0"
EQUATION_NUMBER = re.compile(r"\(([1-9][0-9]?)\)\s*$")
FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.)\s*([0-9]+)\s*[:.]", re.IGNORECASE)
TABLE_CAPTION = re.compile(r"^Table\s*([0-9]+)\s*[:.]", re.IGNORECASE)


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def _structured_rows(cells):
    """Split collapsed data only when every column has the same line count."""
    if not cells:
        return [], "structure_incomplete"
    rows = [list(cells[0])]
    expanded = False
    for row in cells[1:]:
        lines = [[line.strip() for line in cell.splitlines() if line.strip()]
                 if isinstance(cell, str) else [] for cell in row]
        counts = {len(column) for column in lines}
        if len(counts) != 1 or 0 in counts:
            return cells, "structure_incomplete"
        count = counts.pop()
        expanded |= count > 1
        rows.extend([[column[index] for column in lines] for index in range(count)])
    return rows, "aligned_text_candidate" if expanded else "detected_unverified"


def build_source_bundle(document, evidence_rows, pdf_path: Path) -> dict:
    """Build an auditable candidate structure; no inferred object is marked verified."""
    doc = dict(document)
    rows = [dict(row) for row in evidence_rows]
    page_metadata = _decoded(doc["pages"])
    sections_data = _decoded(doc["sections"])
    body = [{"id": f'body_{row["id"]}', "evidence_id": row["id"],
             "page_index": row["page_index"], "bbox": _decoded(row["bbox"]),
             "text": row["excerpt"], "precision": row["precision"],
             "status": "extracted_unverified"} for row in rows]
    body_by_page = {}
    for block in body:
        body_by_page.setdefault(block["page_index"], []).append(block)

    sections = [{"id": f"section_{index}", "title": item["title"],
                 "level": item["level"], "page_index": item["page_index"],
                 "evidence_id": item.get("evidence_id"), "status": "candidate"}
                for index, item in enumerate(sections_data)]
    formulas = []
    figures = []
    for block in body:
        normalized = " ".join(block["text"].split())
        match = EQUATION_NUMBER.search(normalized)
        if match:
            number_only = normalized == f"({match.group(1)})"
            neighbors = ([item for item in body_by_page.get(block["page_index"], []) if
                          item["evidence_id"] != block["evidence_id"] and
                          item["bbox"][2] <= block["bbox"][0] and
                          block["bbox"][0] - item["bbox"][2] < 230 and
                          item["bbox"][1] <= block["bbox"][3] + 4 and
                          item["bbox"][3] >= block["bbox"][1] - 4 and
                          not EQUATION_NUMBER.search(" ".join(item["text"].split()))]
                         if number_only else [])
            source_blocks = sorted(neighbors + [block], key=lambda item: item["bbox"][0])
            formulas.append({"id": f"formula_{block['evidence_id']}", "number": match.group(1),
                             "text": " ".join(item["text"].strip() for item in source_blocks),
                             "page_index": block["page_index"],
                             "bbox": [min(item["bbox"][0] for item in source_blocks),
                                      min(item["bbox"][1] for item in source_blocks),
                                      max(item["bbox"][2] for item in source_blocks),
                                      max(item["bbox"][3] for item in source_blocks)],
                             "number_evidence_id": block["evidence_id"],
                             "evidence_ids": [item["evidence_id"] for item in source_blocks],
                             "transcription_status": ("unverified_grouped_expression" if neighbors else
                                                      "unverified_number_only" if number_only else
                                                      "unverified_fragment"),
                             "usability_status": "structure_incomplete" if number_only and not neighbors else
                                                 "detected_unverified",
                             "status": "transcription_unverified"})
        match = FIGURE_CAPTION.match(normalized)
        if match:
            figures.append({"id": f"figure_{block['evidence_id']}", "number": match.group(1),
                            "caption": block["text"], "caption_evidence_id": block["evidence_id"],
                            "page_index": block["page_index"], "visual_bbox": None,
                            "status": "visual_unreviewed"})

    tables = []
    matched_caption_ids = set()
    with fitz.open(pdf_path) as pdf:
        for figure in figures:
            page = pdf[figure["page_index"]]
            image_boxes = [rect for image in page.get_images(full=True)
                           for rect in page.get_image_rects(image[0])]
            caption_box = next(block["bbox"] for block in body
                               if block["evidence_id"] == figure["caption_evidence_id"])
            candidates = [rect for rect in image_boxes if rect.y1 <= caption_box[3] + 40]
            if candidates:
                closest = min(candidates, key=lambda rect: abs(rect.y1 - caption_box[1]))
                figure["visual_bbox"] = list(closest)
                figure["visual_link_status"] = "candidate"
                figure["usability_status"] = "detected_unverified"
            else:
                figure["visual_link_status"] = "missing_or_vector"
                figure["usability_status"] = "visual_review_required"
        for page_index, page in enumerate(pdf):
            page_blocks = body_by_page.get(page_index, [])
            for table_index, table in enumerate(page.find_tables().tables):
                box = list(table.bbox)
                intersecting = [block["evidence_id"] for block in page_blocks
                                if fitz.Rect(block["bbox"]).intersects(fitz.Rect(box))]
                caption_candidates = []
                for block in page_blocks:
                    match = TABLE_CAPTION.match(" ".join(block["text"].split()))
                    if match:
                        caption_candidates.append((abs(block["bbox"][1] - box[3]), match.group(1), block))
                caption = min(caption_candidates, default=None, key=lambda item: item[0])
                linked_caption = caption if caption and caption[0] < 200 else None
                if linked_caption:
                    matched_caption_ids.add(linked_caption[2]["evidence_id"])
                cells = table.extract()
                structured_rows, structure_status = _structured_rows(cells)
                if table.row_count < 2 and not linked_caption:
                    structure_status = "possible_false_positive"
                tables.append({"id": f"table_p{page_index}_{table_index}",
                               "number": linked_caption[1] if linked_caption else None,
                               "page_index": page_index, "bbox": box,
                               "row_count": table.row_count, "col_count": table.col_count,
                               "cells": cells, "structured_rows": structured_rows,
                               "structure_status": structure_status,
                               "usability_status": ("visual_review_required" if structure_status ==
                                                    "possible_false_positive" else
                                                    "structure_incomplete" if structure_status ==
                                                    "structure_incomplete" else "detected_unverified"),
                               "evidence_ids": intersecting,
                               "caption": linked_caption[2]["text"] if linked_caption else None,
                               "caption_evidence_id": linked_caption[2]["evidence_id"] if linked_caption else None,
                               "status": "candidate"})
        metadata = {key: value for key, value in pdf.metadata.items()
                    if key in {"title", "author", "subject", "keywords"} and value}

    for block in body:
        match = TABLE_CAPTION.match(" ".join(block["text"].split()))
        if match and block["evidence_id"] not in matched_caption_ids:
            tables.append({"id": f"table_{block['evidence_id']}", "number": match.group(1),
                           "page_index": block["page_index"], "bbox": None,
                           "row_count": 0, "col_count": 0, "cells": [], "evidence_ids": [],
                           "structured_rows": [], "structure_status": "structure_incomplete",
                           "usability_status": "structure_incomplete",
                           "caption": block["text"], "caption_evidence_id": block["evidence_id"],
                           "status": "missing_structure"})

    pages = [{"page_index": page["page_index"], "width": page["width"],
              "height": page["height"], "rotation": page["rotation"],
              "evidence_ids": [block["evidence_id"] for block in body_by_page.get(page["page_index"], [])],
              "status": "needs_ocr" if page["needs_ocr"] else "text_extracted"}
             for page in page_metadata]
    reading_units = build_reading_units(pages, body, sections, tables, fitz.VersionBind)
    unit_positions = {evidence_id: index for index, unit in enumerate(reading_units)
                      for evidence_id in unit["evidence_ids"]}
    tables.sort(key=lambda item: unit_positions.get(item.get("caption_evidence_id"),
                                                     min((unit_positions.get(evidence_id, len(reading_units))
                                                          for evidence_id in item["evidence_ids"]),
                                                         default=len(reading_units))))
    formulas.sort(key=lambda item: unit_positions.get(item["number_evidence_id"], len(reading_units)))
    for formula in formulas:
        linked = [unit for unit in reading_units if formula["number_evidence_id"] in unit["evidence_ids"]]
        formula["section_id"] = linked[0]["section_id"] if linked else None
        formula["reading_unit_ids"] = [unit["id"] for unit in reading_units if
                                       set(formula["evidence_ids"]).intersection(unit["evidence_ids"])]
    for item in formulas + tables + figures:
        item["document_id"] = doc["id"]
        if "section_id" not in item:
            anchor = item.get("caption_evidence_id") or next(iter(item.get("evidence_ids", [])), None)
            linked = next((unit for unit in reading_units if anchor in unit["evidence_ids"]), None)
            item["section_id"] = linked["section_id"] if linked else None
    references = [{"id": f'reference_{unit["id"]}', "text": unit["text"],
                   "evidence_ids": unit["evidence_ids"], "page_index": unit["page_index"],
                   "status": "candidate"}
                  for unit in reading_units if any(section["id"] == unit["section_id"] and
                                                   section["title"].strip().casefold() == "references"
                                                   for section in sections) and
                  unit["text"].strip().casefold() != "references"]
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": f'sb_{doc["id"][:12]}_{doc["snapshot_id"]}_v1',
        "document": {"id": doc["id"], "filename": doc["filename"], "sha256": doc["sha256"],
                     "snapshot_id": doc["snapshot_id"], "page_count": doc["page_count"],
                     "pdf_metadata": metadata},
        "parser": {"name": "PyMuPDF legacy Evidence", "version": fitz.VersionBind},
        "pages": pages, "sections": sections, "body": body, "reading_units": reading_units,
        "formulas": formulas,
        "tables": tables, "figures": figures, "references": references,
        "limitations": ["Legacy block order is not a verified reading order.",
                        "Formula transcription, table rows, figures, section boundaries and references require verification.",
                        "A missing object in this candidate inventory does not prove absence from the PDF."],
    }
