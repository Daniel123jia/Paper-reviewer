"""Conservative quality report for a candidate paper Source Bundle."""

import re


def assess_input_readiness(bundle: dict, node: str, scope: dict | None = None,
                           reference: dict | None = None) -> dict:
    """Gate a specified task; only an explicit human PDF reference can grant ready."""
    if node not in {"section_summary", "formula_check", "table_review", "figure_review"}:
        raise ValueError("Unknown review input requirement")
    scope = scope or {}
    selected_pages = set(scope.get("page_indices", [page["page_index"] for page in bundle["pages"]]))
    selected_sections = set(scope.get("section_ids", []))
    document_id = bundle.get("document", {}).get("id")
    units = [unit for unit in bundle.get("reading_units", [])
             if unit["page_index"] in selected_pages and
             (not selected_sections or unit["section_id"] in selected_sections)]
    pages = [page for page in bundle["pages"] if page["page_index"] in selected_pages]
    reasons = []
    if (scope.get("document_id", document_id) != document_id or
        not selected_pages.issubset({page["page_index"] for page in bundle["pages"]})):
        reasons.append("requested_scope_invalid")
    all_page_units = [unit for unit in bundle.get("reading_units", []) if unit["page_index"] in selected_pages]
    mapped_ids = {evidence_id for unit in all_page_units for evidence_id in unit["evidence_ids"]}
    if any(evidence_id not in mapped_ids for page in pages for evidence_id in page["evidence_ids"]):
        reasons.append("source_evidence_unmapped")
    if node == "formula_check":
        objects = [item for item in bundle["formulas"] if item["page_index"] in selected_pages]
        verified_key = "verified_formula_ids"
    elif node == "table_review":
        objects = [item for item in bundle["tables"] if item["page_index"] in selected_pages]
        verified_key = "verified_table_ids"
    elif node == "figure_review":
        objects = [item for item in bundle["figures"] if item["page_index"] in selected_pages]
        verified_key = "verified_figure_ids"
    else:
        objects = []
        verified_key = None
    selected_objects = set(scope.get("object_ids", []))
    if selected_objects:
        objects = [item for item in objects if item["id"] in selected_objects]
    objects = [item for item in objects if
               (document_id is None or item.get("document_id", document_id) == document_id) and
               (not selected_sections or item.get("section_id") in selected_sections)]
    if selected_objects and (len(objects) != len(selected_objects) or
                             {item["id"] for item in objects} != selected_objects):
        reasons.append("required_object_not_in_scope")
    if selected_objects:
        if node == "table_review":
            relevant_ids = {item.get("caption_evidence_id") for item in objects}
            units = [unit for unit in units if unit.get("table_id") in selected_objects or
                     relevant_ids.intersection(unit["evidence_ids"])]
        elif node == "formula_check":
            relevant_ids = {source_id for item in objects for source_id in item["evidence_ids"]}
            units = [unit for unit in units if relevant_ids.intersection(unit["evidence_ids"])]
        elif node == "figure_review":
            relevant_ids = {item["caption_evidence_id"] for item in objects}
            units = [unit for unit in units if relevant_ids.intersection(unit["evidence_ids"])]
    if not units or any(page["status"] == "needs_ocr" for page in pages):
        reasons.append("required_text_unreadable")
    if node != "section_summary" and not objects:
        reasons.append("required_object_not_detected")
    if any(unit["reading_order_status"] == "unverified" for unit in units):
        reasons.append("reading_order_ambiguous")
    if any(unit["section_id"] is None for unit in units):
        reasons.append("section_mapping_incomplete")
    if any(item.get("structure_status") in {"structure_incomplete", "possible_false_positive"}
           for item in objects):
        reasons.append("required_structure_incomplete")
    if node == "table_review" and any(item.get("reading_unit_status") == "raw_evidence_fallback"
                                      for item in objects):
        reasons.append("table_source_text_uncovered")
    valid_reference = bool(reference and reference.get("source") == "human_pdf_review" and
                           reference.get("reference_id"))
    verified_units = set(reference.get("verified_unit_ids", [])) if valid_reference else set()
    if any(unit["evidence_mapping_status"] != "exact_block" and unit["id"] not in verified_units
           for unit in units):
        reasons.append("source_mapping_partial")
    if not valid_reference or any(unit["id"] not in verified_units for unit in units):
        reasons.append("human_reading_reference_missing")
    if node == "section_summary" and (not valid_reference or
                                      not selected_pages.issubset(set(reference.get("verified_page_indices", [])))):
        reasons.append("human_page_reference_missing")
    if verified_key and (not valid_reference or any(item["id"] not in set(reference.get(verified_key, []))
                                              for item in objects)):
        reasons.append("human_object_reference_missing")
    if verified_key and not selected_objects and (not valid_reference or
        node not in reference.get("verified_inventory_nodes", [])):
        reasons.append("human_inventory_reference_missing")
    blocking = {"required_text_unreadable", "required_object_not_detected", "source_evidence_unmapped",
                "requested_scope_invalid", "required_object_not_in_scope"}
    if node in {"formula_check", "table_review", "figure_review"}:
        blocking |= {"required_structure_incomplete", "human_object_reference_missing",
                     "human_inventory_reference_missing", "table_source_text_uncovered"}
    status = "blocked" if blocking.intersection(reasons) else "needs_review" if reasons else "ready"
    return {"node": node, "scope": scope, "status": status, "reasons": reasons,
            "reference_id": reference["reference_id"] if valid_reference else None}


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
        reading = [unit for unit in bundle.get("reading_units", [])
                   if unit["page_index"] == page["page_index"]]
        mapped_ids = {evidence_id for unit in reading for evidence_id in unit["evidence_ids"]}
        reading_state = ("not_available" if not reading else "unverified" if any(
            unit["reading_order_status"] == "unverified" for unit in reading) else "geometric_candidate")
        issues = []
        if page["status"] == "needs_ocr" or not page["evidence_ids"]:
            issues.append("text_missing_or_image_only")
        if _has_two_columns(page, by_page.get(page["page_index"], [])):
            issues.append("reading_order_unverified")
        if any(evidence_id not in mapped_ids for evidence_id in page["evidence_ids"]):
            issues.append("source_evidence_unmapped")
        pages.append({"page_index": page["page_index"], "status": page["status"],
                      "evidence_count": len(page["evidence_ids"]),
                      "reading_unit_count": len(reading), "mapped_evidence_count": len(mapped_ids),
                      "reading_order_status": reading_state, "issues": issues})

    tables = []
    for table in bundle["tables"]:
        cells = table["cells"]
        data_rows = cells if table["row_count"] <= 2 else cells[1:]
        has_stacked_values = any(isinstance(cell, str) and len([line for line in cell.splitlines() if line.strip()]) > 1
                                 for row in data_rows for cell in row)
        issues = ["row_alignment_unverified"] if has_stacked_values else []
        if table["status"] == "missing_structure":
            issues.append("table_structure_missing")
        if table.get("structure_status") == "possible_false_positive":
            issues.append("possible_false_positive")
        if table.get("structure_status") == "aligned_text_candidate":
            issues.append("split_rows_require_pdf_check")
        if table.get("reading_unit_status") == "raw_evidence_fallback":
            issues.append("table_source_text_uncovered")
        if not table.get("caption_evidence_id"):
            issues.append("caption_link_unverified")
        tables.append({"id": table["id"], "page_index": table["page_index"],
                       "status": "missing_structure" if table["status"] == "missing_structure" else
                                 ("needs_review" if issues else "candidate"),
                       "caption_evidence_id": table.get("caption_evidence_id"),
                       "structure_status": table.get("structure_status", "unverified"),
                       "reading_unit_status": table.get("reading_unit_status", "not_applicable"),
                       "usability_status": table.get("usability_status", "detected_unverified"),
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
                      "status": item["status"],
                      "transcription_status": item.get("transcription_status", "unverified"),
                      "usability_status": item.get("usability_status", "detected_unverified"),
                      "issues": ["expression_and_context_unverified"]}
                     for item in bundle["formulas"]],
        "figures": [{"id": item["id"], "page_index": item["page_index"],
                     "status": item["status"],
                     "usability_status": item.get("usability_status", "visual_review_required"),
                     "issues": ["visual_content_unparsed"] +
                               ([] if item.get("visual_bbox") else ["visual_region_unlocated"])}
                    for item in bundle["figures"]],
        "references": {"candidate_count": len(bundle["references"]), "status": "unverified"},
        "limitations": bundle["limitations"],
        "input_readiness": {node: assess_input_readiness(bundle, node)
                            for node in ("section_summary", "formula_check", "table_review", "figure_review")},
    }
