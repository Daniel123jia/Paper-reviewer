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
from lxml import etree


EAST_ASIA_FONT = "Source Han Sans SC"
EAST_ASIA_LANGUAGE = "zh-CN"
CJK_COMPATIBILITY_FONT = "Arial Unicode MS"


def _get_or_add(parent, tag):
    child = parent.find(qn(tag))
    if child is None:
        child = OxmlElement(tag)
        parent.append(child)
    return child


def _set_east_asian_properties(rpr, *, include_hint=True, direct_cjk_run=False):
    fonts = _get_or_add(rpr, "w:rFonts")
    fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)
    fonts.set(qn("w:eastAsiaTheme"), "minorEastAsia")
    if direct_cjk_run:
        # LibreOffice does not consistently honor only w:eastAsia on mixed-script runs.
        # Its generic Unicode family also gives readers a system-font fallback when
        # Source Han Sans SC is not installed, without embedding a font file.
        fonts.set(qn("w:ascii"), CJK_COMPATIBILITY_FONT)
        fonts.set(qn("w:hAnsi"), CJK_COMPATIBILITY_FONT)
    if include_hint:
        fonts.set(qn("w:hint"), "eastAsia")
    lang = _get_or_add(rpr, "w:lang")
    lang.set(qn("w:eastAsia"), EAST_ASIA_LANGUAGE)


def _contains_cjk(text):
    return any("\u3400" <= character <= "\u9fff" for character in text)


def _register_east_asian_font(doc):
    font_table = next(
        (part for part in doc.part.package.parts if str(part.partname) == "/word/fontTable.xml"),
        None,
    )
    if font_table is None:
        return
    root = etree.fromstring(font_table.blob)
    selector = f'./w:font[@w:name="{EAST_ASIA_FONT}"]'
    if root.xpath(selector, namespaces={"w": qn("w:fonts").split("}")[0][1:]}):
        return
    font = OxmlElement("w:font")
    font.set(qn("w:name"), EAST_ASIA_FONT)
    alt_name = OxmlElement("w:altName")
    alt_name.set(qn("w:val"), "思源黑体")
    family = OxmlElement("w:family")
    family.set(qn("w:val"), "swiss")
    pitch = OxmlElement("w:pitch")
    pitch.set(qn("w:val"), "variable")
    font.extend((alt_name, family, pitch))
    root.append(font)
    font_table._blob = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _postprocess_docx(path: Path):
    """Add portable CJK metadata and row-safe table pagination in place."""
    doc = Document(path)

    for style in doc.styles:
        rpr = style.element.get_or_add_rPr()
        _set_east_asian_properties(rpr, include_hint=False)

    roots = [doc.element]
    for section in doc.sections:
        roots.extend((section.header._element, section.footer._element))
    for root in roots:
        for run in root.iter(qn("w:r")):
            text = "".join(node.text or "" for node in run.iter(qn("w:t")))
            if not _contains_cjk(text):
                continue
            rpr = run.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                run.insert(0, rpr)
            _set_east_asian_properties(rpr, direct_cjk_run=True)

    settings = doc.settings._element
    theme_lang = _get_or_add(settings, "w:themeFontLang")
    theme_lang.set(qn("w:eastAsia"), EAST_ASIA_LANGUAGE)
    _register_east_asian_font(doc)

    for table in doc.tables:
        for index, row in enumerate(table.rows):
            trpr = row._tr.get_or_add_trPr()
            if index == 0:
                header = _get_or_add(trpr, "w:tblHeader")
                header.set(qn("w:val"), "true")
            cant_split = _get_or_add(trpr, "w:cantSplit")
            cant_split.set(qn("w:val"), "true")

    appendix_index = next(
        (index for index, paragraph in enumerate(doc.paragraphs)
         if "技术核验附录" in paragraph.text
         or "Technical Verification Appendix" in paragraph.text),
        None,
    )
    if appendix_index is not None:
        appendix_paragraphs = doc.paragraphs[appendix_index + 1:]
        subsection_indices = [
            index for index, paragraph in enumerate(appendix_paragraphs)
            if paragraph.style.name == "Heading 3"
        ]
        if len(subsection_indices) >= 2:
            tail = appendix_paragraphs[subsection_indices[-2]:]
            # Keep only a compact appendix tail together. Longer material must remain
            # free to paginate naturally instead of creating a new global constraint.
            if len(tail) <= 14 and sum(len(paragraph.text) for paragraph in tail) <= 1200:
                for paragraph in tail[:-1]:
                    paragraph.paragraph_format.keep_with_next = True

    doc.save(path)


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
    fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)
    fonts.set(qn("w:eastAsiaTheme"), "minorEastAsia")
    lang = _get_or_add(rpr, "w:lang")
    lang.set(qn("w:eastAsia"), EAST_ASIA_LANGUAGE)
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
        _postprocess_docx(word)
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
