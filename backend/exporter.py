"""Render edited Markdown to academic DOCX and PDF without model calls."""

from pathlib import Path
import shutil
import subprocess
import tempfile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


def configure_style(style, size, bold=False, color="233944", spacing=1.4, latin_font="Times New Roman"):
    style.font.name = latin_font
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor.from_string(color)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), latin_font)
    fonts.set(qn("w:hAnsi"), latin_font)
    fonts.set(qn("w:eastAsia"), "Source Han Sans SC")
    style.paragraph_format.line_spacing = spacing
    style.paragraph_format.space_after = Pt(6)


def reference_docx(kind: str, language: str, path: Path):
    doc = Document()
    font = "Times New Roman"
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    margin = 2.3 if kind == "report" else 2.6
    section.left_margin = section.right_margin = Cm(margin)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.3)
    configure_style(doc.styles["Normal"], 10.5 if kind == "report" else 11, latin_font=font)
    configure_style(doc.styles["Title"], 18 if kind == "report" else 17, True, "233E54", 1.2, font)
    configure_style(doc.styles["Heading 1"], 14 if kind == "report" else 13, True, "284B62", 1.2, font)
    configure_style(doc.styles["Heading 2"], 11.5, True, "35546A", 1.2, font)
    for name in ("Heading 1", "Heading 2"):
        doc.styles[name].paragraph_format.keep_with_next = True
        doc.styles[name].paragraph_format.space_before = Pt(13)
    header = section.header.paragraphs[0]
    header.text = ("内部审稿报告" if kind == "report" else "正式审稿意见") if language == "zh" else (
        "Internal Peer Review Report" if kind == "report" else "Formal Review Comments")
    header.style = doc.styles["Normal"]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("— ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    footer.add_run(" —")
    doc.save(path)


def render_export(body: str, kind: str, language: str, output_format: str) -> bytes:
    if output_format == "md":
        return body.encode("utf-8")
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("本机未安装 Pandoc，无法导出 Word/PDF")
    with tempfile.TemporaryDirectory(prefix="paper-review-export-") as temporary:
        root = Path(temporary)
        source = root / "draft.md"
        reference = root / "reference.docx"
        word = root / "review.docx"
        source.write_text(body, encoding="utf-8")
        reference_docx(kind, language, reference)
        subprocess.run([pandoc, "--from=markdown+pipe_tables+tex_math_dollars", "--to=docx",
                        "--standalone", f"--reference-doc={reference}", "--output", str(word), str(source)],
                       check=True, timeout=60, capture_output=True)
        if output_format == "docx":
            return word.read_bytes()
        html = root / "review.html"
        pdf = root / "review.pdf"
        subprocess.run([pandoc, "--from=markdown+pipe_tables+tex_math_dollars-raw_html", "--to=html5",
                        "--mathml", "--standalone", "--output", str(html), str(source)],
                       check=True, timeout=60, capture_output=True)
        script = Path(__file__).resolve().parents[1] / "frontend" / "scripts" / "render_pdf.mjs"
        subprocess.run(["node", str(script), str(html), str(pdf), kind, language],
                       check=True, timeout=90, capture_output=True)
        if not pdf.exists():
            raise RuntimeError("浏览器排版器未生成 PDF")
        return pdf.read_bytes()
