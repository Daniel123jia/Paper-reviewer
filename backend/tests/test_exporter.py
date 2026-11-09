import io
import re
import zipfile

import fitz
from docx import Document
from lxml import etree

from backend.exporter import render_export


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def export_fixture() -> str:
    long_id = "consolidated_5d02ca178fa369bd6ad7_evidence_source_table_3_row_key_value"
    rows = [
        f"| Issue {index} | `{long_id}_{index}` | 中文证据说明 {index}，保留数值 1.{index}。 |"
        for index in range(1, 15)
    ]
    return "\n".join([
        "# 待审稿人复核的内部报告候选版 / DRAFT",
        "",
        "## 短表格",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        "| Table 1 | 0.3 |",
        "| Table 3 | 1.3 |",
        "",
        "## 技术核验附录",
        "",
        "公式保持为 $x^2+y^2=z^2$，以下长表允许在行边界跨页。",
        "",
        "| 原始Issue | Evidence ID | 反证与限制 |",
        "|---|---|---|",
        *rows,
        "",
        "### AI辅助视觉检查（不是人工确认）",
        "",
        "Table 1 与 Table 3 的视觉结果保持 pending。",
        "",
        "### 尚未完成的检查",
        "",
        "1. 核查历史实现与计分管线。",
        "2. 保持人工确认状态为 pending。",
    ])


def xml_parts(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {
            name: etree.fromstring(archive.read(name))
            for name in archive.namelist()
            if name in {"word/document.xml", "word/styles.xml", "word/settings.xml", "word/fontTable.xml"}
            or name.startswith("word/header") or name.startswith("word/footer")
        }


def test_docx_applies_east_asian_font_to_styles_and_concrete_runs():
    word = render_export(export_fixture(), "report", "zh", "docx")
    parts = xml_parts(word)

    chinese_runs = []
    for name, root in parts.items():
        for run in root.xpath(".//w:r", namespaces=NS):
            text = "".join(run.xpath(".//w:t/text()", namespaces=NS))
            if re.search(r"[\u3400-\u9fff]", text):
                chinese_runs.append((name, text, run))
    assert chinese_runs
    for name, text, run in chinese_runs:
        fonts = run.find("w:rPr/w:rFonts", NS)
        lang = run.find("w:rPr/w:lang", NS)
        assert fonts is not None, (name, text)
        assert fonts.get(f"{{{NS['w']}}}eastAsia") == "Source Han Sans SC", (name, text)
        assert fonts.get(f"{{{NS['w']}}}eastAsiaTheme") == "minorEastAsia", (name, text)
        assert fonts.get(f"{{{NS['w']}}}ascii") == "Arial Unicode MS", (name, text)
        assert fonts.get(f"{{{NS['w']}}}hAnsi") == "Arial Unicode MS", (name, text)
        assert fonts.get(f"{{{NS['w']}}}hint") == "eastAsia", (name, text)
        assert lang is not None and lang.get(f"{{{NS['w']}}}eastAsia") == "zh-CN", (name, text)

    settings = parts["word/settings.xml"]
    theme_lang = settings.find("w:themeFontLang", NS)
    assert theme_lang is not None
    assert theme_lang.get(f"{{{NS['w']}}}eastAsia") == "zh-CN"
    registered = parts["word/fontTable.xml"].xpath(
        './w:font[@w:name="Source Han Sans SC"]/w:altName[@w:val="思源黑体"]', namespaces=NS)
    assert registered


def test_docx_tables_repeat_headers_and_split_only_between_rows():
    word = render_export(export_fixture(), "report", "zh", "docx")
    root = xml_parts(word)["word/document.xml"]
    tables = root.xpath(".//w:tbl", namespaces=NS)
    assert len(tables) == 2
    for table in tables:
        rows = table.xpath("./w:tr", namespaces=NS)
        assert rows
        assert rows[0].find("w:trPr/w:tblHeader", NS) is not None
        for row in rows:
            assert row.find("w:trPr/w:cantSplit", NS) is not None

    doc = Document(io.BytesIO(word))
    all_text = "\n".join(
        cell.text
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
    )
    assert "consolidated_5d02ca178fa369bd6ad7_evidence_source_table_3_row_key_value_14" in all_text
    assert "中文证据说明 14" in all_text

    tail_start = next(
        index for index, paragraph in enumerate(doc.paragraphs)
        if paragraph.text.startswith("AI辅助视觉检查")
    )
    appendix_tail = doc.paragraphs[tail_start:]
    assert len(appendix_tail) >= 5
    assert all(paragraph.paragraph_format.keep_with_next for paragraph in appendix_tail[:-1])


def test_pdf_keeps_short_table_together_and_splits_long_table_with_repeated_header():
    pdf = render_export(export_fixture(), "report", "zh", "pdf")
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        pages = [page.get_text() for page in rendered]
    joined = "".join(pages).replace("\n", "")
    assert "Table 1" in joined and "Table 3" in joined
    assert any("Table 1" in page and "Table 3" in page for page in pages)
    assert "consolidated_5d02ca178fa369bd6ad7" in joined
    assert "row_key_value_14" in joined
    assert sum("原始Issue" in page for page in pages) >= 2
    assert len(pages[-1].replace("内部完整审稿报告", "").strip()) >= 180


def test_markdown_export_is_byte_for_byte_unchanged():
    body = export_fixture()
    assert render_export(body, "report", "zh", "md") == body.encode("utf-8")
