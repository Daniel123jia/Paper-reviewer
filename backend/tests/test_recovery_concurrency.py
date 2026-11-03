import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import fitz
from fastapi.testclient import TestClient

from backend.app import create_app


def valid_pdf():
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 90), "Parallel upload evidence")
    data = pdf.tobytes()
    pdf.close()
    return data


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    create_app(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO review_runs VALUES ('run1', 'case1', 'running', NULL, datetime('now'), NULL)")
        db.execute("INSERT INTO finding_verifications VALUES ('v1', 'f1', 'queued', NULL, NULL, datetime('now'), NULL)")
    create_app(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        run = db.execute("SELECT status, error, finished_at FROM review_runs WHERE id = 'run1'").fetchone()
        verification = db.execute("SELECT status, error, finished_at FROM finding_verifications WHERE id = 'v1'").fetchone()
    assert run[0] == verification[0] == "failed"
    assert run[1] and verification[1] and run[2] and verification[2]


def test_parallel_same_pdf_upload_keeps_one_snapshot_and_original_file(tmp_path):
    client = TestClient(create_app(tmp_path))
    data = valid_pdf()

    def upload(_):
        response = client.post("/api/documents", files={"file": ("same.pdf", data, "application/pdf")})
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(upload, range(16)))
    assert all(status == 201 for status, _ in results)
    ids = {body["id"] for _, body in results}
    snapshots = {body["snapshot_id"] for _, body in results}
    assert ids == {hashlib.sha256(data).hexdigest()}
    assert len(snapshots) == 1
    document_id = next(iter(ids))
    assert (tmp_path / "files" / f"{document_id}.pdf").read_bytes() == data
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evidence WHERE document_id = ?", (document_id,)).fetchone()[0] >= 1
