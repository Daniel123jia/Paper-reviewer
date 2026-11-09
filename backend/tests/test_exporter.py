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


def balanced_appendix_fixture() -> str:
    parts = ["# DRAFT", ""]
    paragraph = (
        "正文用于验证技术报告分页。相对位置表示、实验设置、证据限制与人工复核状态必须完整保留。"
        * 3
    )
    for index in range(22):
        if index % 5 == 0:
            parts.extend([f"## {index + 1}. 审稿内容", ""])
        parts.extend([paragraph, ""])
    parts.extend([
        "## 技术核验附录", "", "### 原始意见与调整映射", "",
        "| 原始Issue | 原P3状态 | 本候选版处理 | 反证/限定 |",
        "|---|---|---|---|",
    ])
    parts.extend(
        f"| `consolidated_{index:02d}_abcdefghijklmnopqrstuvwxyz` | "
        "inconclusive / internal_only / requires_human_review / pending | "
        "保留证据与限制 | 反证与待核验说明 |"
        for index in range(5)
    )
    parts.extend([
        "", "### AI辅助视觉检查（不是人工确认）", "",
        "- Table 1：数值与配置仍待用户确认。",
        "- Table 3：Key/Value消融仍待用户确认。",
        "- 边界：未改变P3裁定。", "", "### 尚未完成的检查", "",
    ])
    parts.extend(f"{index}. 尚未完成的检查项目，需要人工复核。" for index in range(1, 6))
    return "\n".join(parts)


def long_appendix_fixture() -> str:
    parts = [
        "# DRAFT", "", "## 技术核验附录", "", "### 超长附录小节", "",
    ]
    parts.extend(
        f"{index}. 很长附录项目 {index}：保留来源、反证、限制和人工复核状态，允许自然跨页。"
        for index in range(1, 55)
    )
    return "\n".join(parts)


def multiple_long_tables_fixture() -> str:
    parts = ["# DRAFT", "", "## 技术核验附录", ""]
    for table_index in range(1, 3):
        parts.extend([
            f"### 长技术表 {table_index}", "",
            "| Issue ID | Evidence ID | 说明 |",
            "|---|---|---|",
        ])
        parts.extend(
            f"| `consolidated_{table_index}_{row:02d}_abcdefghijklmnopqrstuvwxyz` | "
            f"`ru_ev_{table_index}_{row:02d}_abcdefghijklmnopqrstuvwxyz` | "
            f"中文与 English mixed evidence {row} |"
            for row in range(1, 19)
        )
        parts.append("")
    return "\n".join(parts)


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
    headings = [paragraph for paragraph in appendix_tail if paragraph.style.name == "Heading 3"]
    body_paragraphs = [paragraph for paragraph in appendix_tail if paragraph.style.name != "Heading 3"]
    assert len(headings) == 2 and body_paragraphs
    assert all(paragraph.style.paragraph_format.keep_with_next for paragraph in headings)
    assert all(not paragraph.paragraph_format.keep_with_next for paragraph in body_paragraphs)


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


def test_pdf_balances_short_appendix_without_forcing_two_sections_together():
    pdf = render_export(balanced_appendix_fixture(), "report", "zh", "pdf")
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        pages = [page.get_text() for page in rendered]
    # Page count depends on the installed CJK font metrics. Verify the actual
    # pagination contract instead: the appendix tail follows its final table
    # row rather than being forced onto an otherwise isolated page.
    assert "consolidated_04_abcdefghijklmnopqrstuvwxyz" in pages[-1].replace("\n", "")
    assert "AI辅助视觉检查" in pages[-1]
    assert "尚未完成的检查" in pages[-1]


def test_pdf_long_appendix_naturally_spans_pages_with_heading_and_first_item_together():
    pdf = render_export(long_appendix_fixture(), "report", "zh", "pdf")
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        pages = [page.get_text() for page in rendered]
    matching_pages = [page for page in pages if "很长附录项目" in page]
    assert len(matching_pages) >= 2
    heading_page = next(page for page in pages if "超长附录小节" in page)
    assert "很长附录项目 1" in heading_page
    assert "很长附录项目 54" in "".join(pages)


def test_pdf_multiple_long_tables_repeat_headers_and_preserve_long_ids():
    pdf = render_export(multiple_long_tables_fixture(), "report", "zh", "pdf")
    with fitz.open(stream=pdf, filetype="pdf") as rendered:
        pages = [page.get_text() for page in rendered]
    joined = "".join(pages).replace("\n", "")
    assert sum(page.count("Evidence ID") for page in pages) >= 4
    assert "consolidated_1_01_abcdefghijklmnopqrstuvwxyz" in joined
    assert "ru_ev_2_18_abcdefghijklmnopqrstuvwxyz" in joined


def test_markdown_export_is_byte_for_byte_unchanged():
    body = export_fixture()
    assert render_export(body, "report", "zh", "md") == body.encode("utf-8")
