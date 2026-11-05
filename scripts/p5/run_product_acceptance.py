from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

import fitz
from docx import Document
import pypandoc

import backend.app as app_module
from backend.tests.test_p4_api import seeded_p4_client
from backend.tests.test_p4_drafts_api import FakeOpenAI


def run(output_dir: Path) -> dict:
    """Exercise P4 review/edit/approval/export with deterministic public fixtures.

    This is a product-path regression, not a model-quality run. The fake model is used
    only to produce a stable report body after the fixture has passed P3/P4 admission.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    app_data = output_dir / "app-data"
    app_data.mkdir(parents=True, exist_ok=True)
    pandoc = Path(pypandoc.get_pandoc_path())
    os.environ["PATH"] = f"{pandoc.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["OPENAI_API_KEY"] = "p5-deterministic-mock-only"
    app_module.OpenAI = FakeOpenAI
    client, _ = seeded_p4_client(app_data, reportable=True)

    project = client.post("/api/evidence-verification-runs/v1/report-projects")
    project.raise_for_status()
    project_data = project.json()
    draft_response = client.post(
        f"/api/review-report-projects/{project_data['id']}/drafts",
        json={"kind": "author", "language": "en"},
    )
    draft_response.raise_for_status()
    draft = draft_response.json()
    draft_id = draft["id"]

    body = draft["body"] + "\nThe reviewer requests a bounded clarification.\n"
    saved_response = client.put(
        f"/api/review-report-drafts/{draft_id}",
        json={"body": body, "expected_revision": draft["revision_number"]},
    )
    saved_response.raise_for_status()
    saved = saved_response.json()
    preapproval_status = client.get(
        f"/api/review-report-drafts/{draft_id}/export?format=md&final=true"
    ).status_code

    issue = project_data["issues"][0]
    decision_response = client.patch(
        f"/api/review-report-projects/{project_data['id']}/issues/"
        f"{issue['consolidated_issue_id']}/decision",
        json={
            "decision": "approve_for_final",
            "reviewer_rationale": "The clarification is source-grounded and bounded.",
            "selected_source_refs": issue["source_refs"][:1],
        },
    )
    decision_response.raise_for_status()
    approval_response = client.post(
        f"/api/review-report-drafts/{draft_id}/approve",
        json={"expected_revision": saved["revision_number"], "confirm_content": True},
    )
    approval_response.raise_for_status()

    exports: dict[str, bytes] = {}
    for extension in ("md", "docx", "pdf"):
        response = client.get(
            f"/api/review-report-drafts/{draft_id}/export?format={extension}&final=true"
        )
        response.raise_for_status()
        exports[extension] = response.content
        (output_dir / f"approved-report.{extension}").write_bytes(response.content)

    word = Document(io.BytesIO(exports["docx"]))
    with fitz.open(stream=exports["pdf"], filetype="pdf") as rendered:
        pdf_text = "\n".join(page.get_text() for page in rendered)
        page_sizes = [[round(page.rect.width, 2), round(page.rect.height, 2)]
                      for page in rendered]
        for index, page in enumerate(rendered):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            pixmap.save(output_dir / f"approved-report-page-{index + 1}.png")
        pdf_pages = rendered.page_count

    changed_response = client.put(
        f"/api/review-report-drafts/{draft_id}",
        json={"body": body + "\nA post-approval edit.\n",
              "expected_revision": saved["revision_number"]},
    )
    changed_response.raise_for_status()
    changed = changed_response.json()
    stale_final_status = client.get(
        f"/api/review-report-drafts/{draft_id}/export?format=md&final=true"
    ).status_code

    zh_response = client.post(
        f"/api/review-report-projects/{project_data['id']}/drafts",
        json={"kind": "internal", "language": "zh"},
    )
    zh_response.raise_for_status()
    zh_draft = zh_response.json()
    zh_body = (zh_draft["body"] +
               "\n## 中文排版检查\n\n此段用于检查中文字体、数学符号与表格版式。\n\n"
               "$$x^2+y^2=z^2$$\n\n| 项目 | 数值 |\n|:---|---:|\n| 示例 | 1.3 |\n")
    zh_saved_response = client.put(
        f"/api/review-report-drafts/{zh_draft['id']}",
        json={"body": zh_body, "expected_revision": zh_draft["revision_number"]},
    )
    zh_saved_response.raise_for_status()
    zh_exports: dict[str, bytes] = {}
    for extension in ("docx", "pdf"):
        response = client.get(
            f"/api/review-report-drafts/{zh_draft['id']}/export?format={extension}"
        )
        response.raise_for_status()
        zh_exports[extension] = response.content
        (output_dir / f"draft-zh-internal.{extension}").write_bytes(response.content)
    zh_word = Document(io.BytesIO(zh_exports["docx"]))
    with fitz.open(stream=zh_exports["pdf"], filetype="pdf") as rendered:
        zh_pdf_text = "\n".join(page.get_text() for page in rendered)
        for index, page in enumerate(rendered):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            pixmap.save(output_dir / f"draft-zh-internal-page-{index + 1}.png")

    summary = {
        "execution_mode": "deterministic_regression",
        "fixture_scope": "synthetic public P3/P4 fixture; not a real model-quality run",
        "preapproval_final_export_status": preapproval_status,
        "approved_revision": saved["revision_number"],
        "exports": {
            "markdown_bytes": len(exports["md"]),
            "docx_bytes": len(exports["docx"]),
            "pdf_bytes": len(exports["pdf"]),
            "docx_paragraphs": len(word.paragraphs),
            "docx_tables": len(word.tables),
            "pdf_pages": pdf_pages,
            "pdf_page_sizes_points": page_sizes,
            "pdf_contains_report_title": "Scope of the method claim" in pdf_text,
            "pdf_contains_internal_evidence_id": "ru_ev_" in pdf_text,
        },
        "post_edit_approval_status": changed["approval_status"],
        "post_edit_final_export_status": stale_final_status,
        "chinese_draft_check": {
            "docx_contains_chinese": any("中文排版检查" in paragraph.text
                                         for paragraph in zh_word.paragraphs),
            "docx_has_editable_table": bool(zh_word.tables),
            "pdf_contains_chinese": "中文排版检查" in zh_pdf_text,
            "pdf_contains_table_value": "1.3" in zh_pdf_text,
            "draft_marker_present": "DRAFT" in zh_pdf_text,
        },
    }
    (output_dir / "product-check-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic P5 P4 product checks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
