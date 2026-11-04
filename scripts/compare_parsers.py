"""Offline parser comparison; emits only landmark metrics, never paper text."""

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import pymupdf as fitz


REFERENCE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "p1_parse_reference.json"


def _normalized(value):
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() or char == "." else " " for char in text).split())


def evaluate_landmarks(text, reference, structured_rows=()):
    normalized = _normalized(text)
    heading_positions = [normalized.find(_normalized(item)) for item in reference["headings"]]
    present = [position for position in heading_positions if position >= 0]
    heading_order = ("missing_headings" if len(present) != len(heading_positions) else
                     "observed_in_order" if present == sorted(present) else "out_of_order")
    return {
        "headings_found": len(present), "headings_expected": len(heading_positions),
        "heading_order": heading_order,
        "table_row_token_sequences_found": sum(_normalized(row) in normalized
                                                for row in reference["table_rows"]),
        "structured_table_rows_found": sum(any(_normalized(row) in _normalized(observed)
                                                for observed in structured_rows)
                                           for row in reference["table_rows"]),
        "table_rows_expected": len(reference["table_rows"]),
        "equation_markers_found": sum(marker in text for marker in reference["equations"]),
        "equation_markers_expected": len(reference["equations"]),
        "figure_captions_found": sum(_normalized(caption) in normalized
                                     for caption in reference["figure_captions"]),
        "figure_captions_expected": len(reference["figure_captions"]),
    }


def _markdown_rows(text):
    rows = []
    for line in text.splitlines():
        if line.strip().startswith("|") and line.count("|") >= 3:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if any(char.isalnum() for cell in cells for char in cell):
                rows.append(" ".join(cells))
    return rows


def make_single_column_pdf(path: Path):
    """Generate a second, synthetic academic layout with known visible content."""
    pdf = fitz.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 70), "Single Column Methods Note", fontsize=16)
    page.insert_text((72, 110), "1 Introduction", fontsize=13)
    page.insert_text((72, 135), "This generated paper tests a simple linear reading order.")
    page.insert_text((72, 185), "2 Method", fontsize=13)
    page.insert_text((72, 215), "E = mc2 (1)")
    page.insert_text((72, 260), "3 Results", fontsize=13)
    page.insert_text((72, 285), "Table 1: Synthetic measurements")
    for x in (72, 300, 470):
        page.draw_line((x, 310), (x, 400))
    for y in (310, 340, 370, 400):
        page.draw_line((72, y), (470, y))
    for y, label, value in ((330, "Sample", "Value"), (360, "Alpha", "1.0"), (390, "Beta", "2.0")):
        page.insert_text((82, y), label)
        page.insert_text((315, y), value)
    page.draw_rect(fitz.Rect(72, 440, 300, 540))
    page.insert_text((72, 565), "Figure 1: Synthetic diagram")
    second = pdf.new_page(width=612, height=792)
    second.insert_text((72, 70), "References", fontsize=13)
    second.insert_text((72, 100), "Example, A. 2026. A generated reference.")
    pdf.save(path)
    pdf.close()


def _extract(parser, pdf_path):
    if parser == "pymupdf_blocks":
        with fitz.open(pdf_path) as pdf:
            text = "\n".join(str(block[4]) for page in pdf
                             for block in page.get_text("blocks", sort=True) if block[6] == 0)
            pages = len(pdf)
            tables = [table for page in pdf for table in page.find_tables().tables]
            structured_rows = [" ".join(cell.strip() for cell in row if isinstance(cell, str))
                               for table in tables for row in table.extract()
                               if all(not isinstance(cell, str) or "\n" not in cell for cell in row)]
        return text, pages, len(tables), structured_rows, "legacy_block_bbox_and_evidence_id", fitz.VersionBind
    if parser == "pymupdf4llm":
        import pymupdf4llm
        chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True, use_ocr=False)
        if not isinstance(chunks, list):
            raise TypeError("page_chunks did not return a list")
        text = "\n".join(chunk.get("text", "") for chunk in chunks)
        has_boxes = any(chunk.get("page_boxes") for chunk in chunks)
        coordinates = "layout_bbox_without_legacy_evidence_id" if has_boxes else "not_observed"
        version = getattr(pymupdf4llm, "version", "unknown")
        return text, len(chunks), None, _markdown_rows(text), coordinates, str(version)
    if parser == "docling":
        from docling.document_converter import DocumentConverter
        converted = DocumentConverter().convert(str(pdf_path))
        document = converted.document
        text = document.export_to_markdown()
        coordinates = ("document_provenance_without_legacy_evidence_id"
                       if any(getattr(item, "prov", None) for item in document.texts) else "not_observed")
        from importlib.metadata import version
        return text, len(document.pages), len(document.tables), _markdown_rows(text), coordinates, version("docling")
    raise ValueError("Unknown parser")


def _peak_rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def run_single(parser, pdf_path, reference):
    start = time.perf_counter()
    try:
        text, pages, tables, structured_rows, coordinates, version = _extract(parser, pdf_path)
        return {"parser": parser, "status": "completed", "version": version,
                "seconds": round(time.perf_counter() - start, 3), "peak_rss_mb": _peak_rss_mb(),
                "page_count": pages, "detected_tables": tables, "extracted_characters": len(text),
                "coordinate_traceability": coordinates,
                "landmarks": evaluate_landmarks(text, reference, structured_rows)}
    except Exception as exc:
        return {"parser": parser, "status": "unavailable_or_failed", "error_type": type(exc).__name__,
                "seconds": round(time.perf_counter() - start, 3), "peak_rss_mb": _peak_rss_mb()}


def compare_one(parser, pdf_path, reference, timeout=300):
    with tempfile.TemporaryDirectory(prefix="etpaper-parser-result-") as temporary:
        result_path = Path(temporary) / "result.json"
        command = [sys.executable, str(Path(__file__).resolve()), "--single-parser", parser,
                   "--pdf", str(pdf_path), "--reference-json", json.dumps(reference),
                   "--result", str(result_path)]
        try:
            process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"parser": parser, "status": "timeout", "timeout_seconds": timeout}
        if process.returncode != 0 or not result_path.exists():
            return {"parser": parser, "status": "process_failed", "exit_code": process.returncode}
        return json.loads(result_path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shaw", type=Path, help="Private local copy of the public Shaw 2018 PDF")
    parser.add_argument("--jmlr", type=Path, help="Local copy of the public single-column JMLR 2024 PDF")
    parser.add_argument("--single-parser", choices=("pymupdf_blocks", "pymupdf4llm", "docling"))
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--reference-json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.single_parser:
        args.result.write_text(json.dumps(run_single(args.single_parser, args.pdf,
                                                     json.loads(args.reference_json))), encoding="utf-8")
        return
    references = json.loads(REFERENCE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="etpaper-p1-fixture-") as temporary:
        generated = Path(temporary) / "single-column.pdf"
        make_single_column_pdf(generated)
        materials = {"single_column": generated}
        if args.shaw:
            digest = hashlib.sha256(args.shaw.read_bytes()).hexdigest()
            if digest != references["shaw2018"]["sha256"]:
                raise SystemExit("Shaw PDF SHA-256 does not match the fixed reference")
            materials["shaw2018"] = args.shaw
        if args.jmlr:
            digest = hashlib.sha256(args.jmlr.read_bytes()).hexdigest()
            if digest != references["jmlr2024"]["sha256"]:
                raise SystemExit("JMLR PDF SHA-256 does not match the fixed reference")
            materials["jmlr2024"] = args.jmlr
        results = {name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "parsers": [compare_one(candidate, path, references[name], args.timeout)
                                      for candidate in ("pymupdf_blocks", "pymupdf4llm", "docling")]}
                   for name, path in materials.items()}
        print(json.dumps({"reference_version": references["reference_version"],
                          "materials": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
