"""Seed a local public synthetic P4 project for the real browser regression."""

import os
import sys
from pathlib import Path

import backend.app as app_module
from backend.tests.test_p4_api import seeded_p4_client
from backend.tests.test_p4_drafts_api import FakeOpenAI


def main(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["OPENAI_API_KEY"] = "browser-mock-only"
    app_module.OpenAI = FakeOpenAI
    client, _ = seeded_p4_client(root, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects")
    if project.status_code != 201:
        raise RuntimeError(project.text)
    draft = client.post(f'/api/review-report-projects/{project.json()["id"]}/drafts',
                        json={"kind": "author", "language": "en"})
    if draft.status_code != 201:
        raise RuntimeError(draft.text)
    print(f'Seeded synthetic P4 document doc1, project {project.json()["id"]}')


if __name__ == "__main__":
    main(sys.argv[1])
