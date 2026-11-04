"""Conservative quality report for a candidate paper Source Bundle."""

import re


def _has_two_columns(page, blocks):
    middle = page["width"] / 2
    left = [block for block in blocks if block["bbox"][2] <= middle - 4]
    right = [block for block in blocks if block["bbox"][0] >= middle]
    if len(left) < 2 or len(right) < 2:
        return False
    return max(item["bbox"][1] for item in left) > min(item["bbox"][1] for item in right) and \
        max(item["bbox"][1] for item in right) > min(item["bbox"][1] for item in left)


def assess_parse_quality(bundle: dict) -> dict:
    """Report observable extraction states, never an ungrounded correctness score."""
    by_page = {}
    for block in bundle["body"]:
        by_page.setdefault(block["page_index"], []).append(block)
    pages = []
    for page in bundle["pages"]:
        issues = []
        if page["status"] == "needs_ocr" or not page["evidence_ids"]:
            issues.append("text_missing_or_image_only")
        if _has_two_columns(page, by_page.get(page["page_index"], [])):
            issues.append("reading_order_unverified")
        pages.append({"page_index": page["page_index"], "status": page["status"],
                      "evidence_count": len(page["evidence_ids"]), "issues": issues})

    tables = []
    for table in bundle["tables"]:
        cells = table["cells"]
        has_stacked_values = any(isinstance(cell, str) and len([line for line in cell.splitlines() if line.strip()]) > 1
                                 for row in cells for cell in row)
        issues = ["row_alignment_unverified"] if has_stacked_values else []
        if table["status"] == "missing_structure":
            issues.append("table_structure_missing")
        if not table.get("caption_evidence_id"):
            issues.append("caption_link_unverified")
        tables.append({"id": table["id"], "page_index": table["page_index"],
                       "status": "missing_structure" if table["status"] == "missing_structure" else
                                 ("needs_review" if issues else "candidate"),
                       "caption_evidence_id": table.get("caption_evidence_id"),
                       "issues": issues})

    sections = []
    previous_number = None
    for item in bundle["sections"]:
        issues = ["section_body_boundaries_unverified"]
        if not item.get("evidence_id"):
            issues.append("heading_location_unverified")
        match = re.match(r"^(\d+(?:\.\d+)*)\s", item["title"])
        if match:
            number = tuple(int(part) for part in match.group(1).split("."))
            if previous_number is not None and number < previous_number:
                issues.append("section_order_unverified")
            previous_number = number
        sections.append({"id": item["id"], "title": item["title"],
                         "page_index": item["page_index"], "status": item["status"],
                         "issues": issues})

    return {
        "schema_version": "1.0", "source_bundle_id": bundle["bundle_id"],
        "accuracy": {"status": "not_measured", "requires_human_reference": True},
        "inventory_completeness": "unverified",
        "pages": pages,
        "sections": sections,
        "tables": tables,
        "formulas": [{"id": item["id"], "page_index": item["page_index"],
                      "status": item["status"], "issues": ["expression_and_context_unverified"]}
                     for item in bundle["formulas"]],
        "figures": [{"id": item["id"], "page_index": item["page_index"],
                     "status": item["status"], "issues": ["visual_content_unparsed"]}
                    for item in bundle["figures"]],
        "references": {"candidate_count": len(bundle["references"]), "status": "unverified"},
        "limitations": bundle["limitations"],
    }
