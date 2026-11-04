"""Conservative geometric ordering of existing Evidence blocks."""

import re
from collections import Counter


def _heading_key(value):
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", " ".join(value.split())).casefold()


def order_page_blocks(page, blocks):
    """Return (blocks, state); retain uncertainty when the column geometry is unclear."""
    middle = page["width"] / 2
    left = [item for item in blocks if item["bbox"][2] <= middle - 4]
    right = [item for item in blocks if item["bbox"][0] >= middle + 4]
    overlap = (left and right and
               max(item["bbox"][1] for item in left) > min(item["bbox"][1] for item in right) and
               max(item["bbox"][1] for item in right) > min(item["bbox"][1] for item in left))
    if len(left) < 2 or len(right) < 2 or not overlap:
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])), "geometric_candidate"

    left_ids = {item["evidence_id"] for item in left}
    right_ids = {item["evidence_id"] for item in right}
    spanning = [item for item in blocks if item["evidence_id"] not in left_ids | right_ids]
    if any(item["bbox"][2] - item["bbox"][0] < page["width"] * .55 for item in spanning):
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])), "unverified"

    ordered = []
    remaining = list(blocks)
    for span in sorted(spanning, key=lambda item: item["bbox"][1]):
        before = [item for item in remaining if item is not span and item["bbox"][1] < span["bbox"][1]]
        ordered.extend(sorted((item for item in before if item["evidence_id"] in left_ids),
                              key=lambda item: (item["bbox"][1], item["bbox"][0])))
        ordered.extend(sorted((item for item in before if item["evidence_id"] in right_ids),
                              key=lambda item: (item["bbox"][1], item["bbox"][0])))
        ordered.append(span)
        used = {item["evidence_id"] for item in before} | {span["evidence_id"]}
        remaining = [item for item in remaining if item["evidence_id"] not in used]
    ordered.extend(sorted((item for item in remaining if item["evidence_id"] in left_ids),
                          key=lambda item: (item["bbox"][1], item["bbox"][0])))
    ordered.extend(sorted((item for item in remaining if item["evidence_id"] in right_ids),
                          key=lambda item: (item["bbox"][1], item["bbox"][0])))
    return ordered, "geometric_candidate"


def build_reading_units(pages, body, sections, tables, parser_version):
    """Preserve source blocks and mark table-row derivations as partial mappings."""
    by_page = {}
    for block in body:
        by_page.setdefault(block["page_index"], []).append(block)
    ordered = []
    page_states = {}
    for page in pages:
        blocks, state = order_page_blocks(page, by_page.get(page["page_index"], []))
        ordered.extend(blocks)
        page_states[page["page_index"]] = state

    blocks_by_id = {block["evidence_id"]: block for block in body}
    table_for_evidence = {}
    for table in tables:
        if table.get("structure_status") == "aligned_text_candidate" and table.get("caption_evidence_id"):
            table_tokens = Counter(token for row in table["structured_rows"]
                                   for cell in row if isinstance(cell, str) for token in cell.split())
            source_tokens = Counter(token for evidence_id in table["evidence_ids"]
                                    if evidence_id in blocks_by_id
                                    for token in blocks_by_id[evidence_id]["text"].split())
            remaining = table_tokens.copy()
            uncovered = []
            for evidence_id in table["evidence_ids"]:
                block = blocks_by_id.get(evidence_id)
                block_tokens = Counter(block["text"].split()) if block is not None else Counter()
                if block is None or block_tokens - remaining:
                    uncovered.append(evidence_id)
                remaining -= block_tokens
            if source_tokens == table_tokens and not uncovered:
                table["reading_unit_status"] = "structured_rows"
                table["uncovered_evidence_ids"] = []
                for evidence_id in table["evidence_ids"]:
                    table_for_evidence[evidence_id] = table
            else:
                table["reading_unit_status"] = "raw_evidence_fallback"
                table["uncovered_evidence_ids"] = uncovered or list(table["evidence_ids"])
    units = []
    emitted_tables = set()
    for block in ordered:
        table = table_for_evidence.get(block["evidence_id"])
        if table:
            if table["id"] in emitted_tables:
                continue
            emitted_tables.add(table["id"])
            for row_index, row in enumerate(table["structured_rows"]):
                units.append({"id": f'ru_{table["id"]}_row_{row_index}',
                              "text": " | ".join(cell or "" for cell in row),
                              "page_index": table["page_index"], "bbox": table["bbox"],
                              "evidence_ids": table["evidence_ids"], "table_id": table["id"],
                              "table_row_index": row_index, "evidence_mapping_status": "source_block_partial",
                              "reading_order_status": page_states[table["page_index"]]})
            continue
        units.append({"id": f'ru_{block["evidence_id"]}', "text": block["text"],
                      "page_index": block["page_index"], "bbox": block["bbox"],
                      "evidence_ids": [block["evidence_id"]],
                      "evidence_mapping_status": "exact_block",
                      "reading_order_status": page_states[block["page_index"]]})

    for section in sections:
        if section.get("evidence_id"):
            continue
        matches = [unit for unit in units if unit["page_index"] == section["page_index"] and
                   _heading_key(unit["text"]) == _heading_key(section["title"])]
        if len(matches) == 1 and len(matches[0]["evidence_ids"]) == 1:
            section["evidence_id"] = matches[0]["evidence_ids"][0]
            section["heading_mapping_status"] = "exact_canonical_title_candidate"
    heading_ids = {section.get("evidence_id"): section for section in sections if section.get("evidence_id")}
    active = None
    section_orders = {}
    for index, unit in enumerate(units):
        heading = next((heading_ids[evidence_id] for evidence_id in unit["evidence_ids"]
                        if evidence_id in heading_ids), None)
        if heading:
            active = heading
            heading.setdefault("reading_unit_ids", [])
            heading["reading_order"] = index
        unit["section_id"] = active["id"] if active else None
        unit["section_order"] = section_orders.get(unit["section_id"], 0) if active else None
        if active:
            section_orders[active["id"]] = unit["section_order"] + 1
            active.setdefault("reading_unit_ids", []).append(unit["id"])
        unit["parser"] = {"name": "PyMuPDF geometric blocks", "version": parser_version}
    for section in sections:
        section.setdefault("reading_unit_ids", [])
        section.setdefault("reading_order", None)
        section["body_mapping_status"] = "candidate" if section["reading_unit_ids"] else "unverified"
    sections.sort(key=lambda section: (section["reading_order"] is None,
                                       section["reading_order"] if section["reading_order"] is not None else 0))
    return units
