"""Seed a local public synthetic P4 project for the real browser regression."""

import json
import os
import sqlite3
import sys
from pathlib import Path

import backend.app as app_module
from backend.tests.test_p4_api import seeded_p4_client
from backend.tests.test_p4_drafts_api import FakeOpenAI


def main(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["OPENAI_API_KEY"] = "browser-mock-only"
    os.environ["OPENAI_BASE_URL"] = "https://provider.example/v1"
    os.environ["OPENAI_MODEL"] = "browser-mock-model"
    os.environ.update({
        "P4_MAX_PROVIDER_REQUESTS": "20",
        "P4_MAX_INPUT_TOKENS": "200000",
        "P4_MAX_OUTPUT_TOKENS": "20000",
        "P4_MAX_OUTPUT_TOKENS_PER_REQUEST": "1000",
        "P4_MAX_DURATION_SECONDS": "180",
        "P4_MAX_PLANNED_COST_USD": "10",
        "P4_INPUT_USD_PER_MILLION": "0.80",
        "P4_OUTPUT_USD_PER_MILLION": "4.00",
        "P4_PRICING_STATUS": "estimated_unverified",
    })
    app_module.OpenAI = FakeOpenAI
    client, _ = seeded_p4_client(root, reportable=True)
    project_ids = []
    for _ in range(2):
        project = client.post("/api/evidence-verification-runs/v1/report-projects")
        if project.status_code != 201:
            raise RuntimeError(project.text)
        project_id = project.json()["id"]
        draft = client.post(f'/api/review-report-projects/{project_id}/drafts',
                            json={"kind": "author", "language": "en"})
        if draft.status_code != 201:
            raise RuntimeError(draft.text)
        project_ids.append(project_id)
    # Two frozen projects share an issue ID. Give their synthetic source a visual
    # review field so the browser can check that unsaved forms never cross projects.
    with sqlite3.connect(root / "review.db") as db:
        for project_id in project_ids:
            snapshot = json.loads(db.execute(
                "SELECT source_snapshot_json FROM p4_report_projects WHERE id = ?",
                (project_id,)).fetchone()[0])
            for source in snapshot["p3"]["sources"]:
                if source["source_id"] == "ru1":
                    source["source_verification_status"] = "visual_check_required"
            db.execute("UPDATE p4_report_projects SET source_snapshot_json = ? WHERE id = ?",
                       (json.dumps(snapshot), project_id))
    print("Seeded synthetic P4 document doc1 with two report projects")


if __name__ == "__main__":
    main(sys.argv[1])
