"""Exercise a local server with a real paper without calling an AI model."""

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
import httpx


def timed(call):
    started = time.perf_counter()
    result = call()
    return result, round((time.perf_counter() - started) * 1000, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8003")
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()
    raw = args.pdf.read_bytes()
    expected_hash = hashlib.sha256(raw).hexdigest()
    measurements = {}

    with httpx.Client(base_url=args.base, timeout=120) as client:
        auth = client.get("/api/auth/status").json()
        if auth["required"]:
            token = os.environ.get("PAPER_REVIEW_ACCESS_TOKEN")
            if not token:
                raise RuntimeError("Set PAPER_REVIEW_ACCESS_TOKEN in this terminal for the protected local server")
            client.post("/api/auth/login", json={"access_token": token}).raise_for_status()

        def upload():
            response = client.post("/api/documents", files={"file": (args.pdf.name, raw, "application/pdf")})
            response.raise_for_status()
            return response.json()

        paper, measurements["upload_first_ms"] = timed(upload)
        repeated, measurements["upload_repeated_ms"] = timed(upload)
        assert paper["id"] == repeated["id"] == expected_hash
        assert paper["snapshot_id"] == repeated["snapshot_id"]
        assert paper["page_count"] > 1
        assert any(not page["needs_ocr"] for page in paper["pages"])

        def search():
            response = client.get(f'/api/documents/{paper["id"]}/search', params={"q": "relative position"})
            response.raise_for_status()
            return response.json()

        matches, measurements["search_ms"] = timed(search)
        assert matches and matches[0]["evidence_id"].startswith("ev_")
        source = client.get(f'/api/documents/{paper["id"]}/evidence/{matches[0]["evidence_id"]}')
        source.raise_for_status()
        assert source.json()["page_index"] == matches[0]["page_index"]

        def read_pdf():
            response = client.get(f'/api/documents/{paper["id"]}/pdf')
            response.raise_for_status()
            return response.content

        original, measurements["pdf_fetch_ms"] = timed(read_pdf)
        assert hashlib.sha256(original).hexdigest() == expected_hash

        def export():
            case = client.post("/api/review-cases", json={"mode": "first_review", "original_document_id": paper["id"]})
            case.raise_for_status()
            draft = client.post(f'/api/review-cases/{case.json()["id"]}/drafts',
                                json={"kind": "report", "language": "zh"})
            draft.raise_for_status()
            response = client.get(f'/api/review-drafts/{draft.json()["id"]}/export', params={"format": "pdf"})
            response.raise_for_status()
            with fitz.open(stream=response.content, filetype="pdf") as rendered:
                assert rendered.page_count >= 1
                assert "内部完整审稿报告" in rendered[0].get_text()
                return rendered.page_count

        report_pages, measurements["draft_and_pdf_export_ms"] = timed(export)

        def concurrent_read(index):
            return len(search()) if index % 2 else hashlib.sha256(read_pdf()).hexdigest()

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            concurrent = list(pool.map(concurrent_read, range(16)))
        measurements["16_concurrent_reads_ms"] = round((time.perf_counter() - started) * 1000, 1)
        assert all(value == expected_hash if index % 2 == 0 else value > 0
                   for index, value in enumerate(concurrent))
        usage = client.get("/api/usage/summary")
        usage.raise_for_status()
        print(json.dumps({"paper": args.pdf.name, "pages": paper["page_count"], "evidence_hits": len(matches),
                          "report_pages": report_pages, "model_calls": usage.json()["total"]["calls"],
                          "measurements": measurements}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
