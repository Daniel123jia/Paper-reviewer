"""Versioned paper source bundle derived from the immutable legacy Evidence snapshot."""

import json
import re
from pathlib import Path

import fitz


SCHEMA_VERSION = "1.0"
EQUATION_NUMBER = re.compile(r"\(([1-9][0-9]?)\)\s*$")
FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.)\s*([0-9]+)\s*[:.]", re.IGNORECASE)
TABLE_CAPTION = re.compile(r"^Table\s*([0-9]+)\s*[:.]", re.IGNORECASE)


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


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
            formulas.append({"id": f"formula_{block['evidence_id']}", "number": match.group(1),
                             "text": block["text"], "page_index": block["page_index"],
                             "bbox": block["bbox"], "evidence_ids": [block["evidence_id"]],
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
                tables.append({"id": f"table_p{page_index}_{table_index}",
                               "number": linked_caption[1] if linked_caption else None,
                               "page_index": page_index, "bbox": box,
                               "row_count": table.row_count, "col_count": table.col_count,
                               "cells": table.extract(), "evidence_ids": intersecting,
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
                           "caption": block["text"], "caption_evidence_id": block["evidence_id"],
                           "status": "missing_structure"})

    references = []
    reference_heading = next((index for index, block in enumerate(body)
                              if block["text"].strip().casefold() == "references"), None)
    if reference_heading is not None:
        references = [{"id": f"reference_{block['evidence_id']}", "text": block["text"],
                       "evidence_id": block["evidence_id"], "page_index": block["page_index"],
                       "status": "candidate"} for block in body[reference_heading + 1:]]

    pages = [{"page_index": page["page_index"], "width": page["width"],
              "height": page["height"], "rotation": page["rotation"],
              "evidence_ids": [block["evidence_id"] for block in body_by_page.get(page["page_index"], [])],
              "status": "needs_ocr" if page["needs_ocr"] else "text_extracted"}
             for page in page_metadata]
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": f'sb_{doc["id"][:12]}_{doc["snapshot_id"]}_v1',
        "document": {"id": doc["id"], "filename": doc["filename"], "sha256": doc["sha256"],
                     "snapshot_id": doc["snapshot_id"], "page_count": doc["page_count"],
                     "pdf_metadata": metadata},
        "parser": {"name": "PyMuPDF legacy Evidence", "version": fitz.VersionBind},
        "pages": pages, "sections": sections, "body": body, "formulas": formulas,
        "tables": tables, "figures": figures, "references": references,
        "limitations": ["Legacy block order is not a verified reading order.",
                        "Formula transcription, table rows, figures, section boundaries and references require verification.",
                        "A missing object in this candidate inventory does not prove absence from the PDF."],
    }
