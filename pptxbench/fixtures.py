"""Synthetic input fixtures: a branded deck, a styled document, a formula
workbook and structured PDFs, per design system and language.

Deterministic: the same (design, lang) always yields the same content. All
content is synthetic (see design.py).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from . import design as D

SLIDE_W_IN, SLIDE_H_IN = 13.333, 7.5


# --------------------------------------------------------------------------
# shared python-pptx helpers (also used by the replay producers)
# --------------------------------------------------------------------------

def emu(inches: float) -> int:
    return int(round(inches * D.EMU_PER_INCH))


def col_x(design: dict, k: int) -> float:
    m, n, g = design["margin_in"], design["columns"], design["gutter_in"]
    col = (SLIDE_W_IN - 2 * m - (n - 1) * g) / n
    return m + k * (col + g)


def span_w(design: dict, ncols: int) -> float:
    m, n, g = design["margin_in"], design["columns"], design["gutter_in"]
    col = (SLIDE_W_IN - 2 * m - (n - 1) * g) / n
    return ncols * col + (ncols - 1) * g


def set_ea_font(run, ea_name: str) -> None:
    from pptx.oxml.ns import qn
    rpr = run._r.get_or_add_rPr()
    for old in rpr.findall(qn("a:ea")):
        rpr.remove(old)
    ea = rpr.makeelement(qn("a:ea"), {"typeface": ea_name})
    latin = rpr.find(qn("a:latin"))
    if latin is not None:
        latin.addnext(ea)
    else:
        rpr.append(ea)


def style_run(run, design: dict, size_pt: float, color: str | None = None, bold: bool = False,
              font: str | None = None) -> None:
    from pptx.dml.color import RGBColor
    from pptx.util import Pt
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color or design["text"])
    run.font.name = font or design["font_latin"]
    set_ea_font(run, design["font_ea"] if font is None else font)


def add_text(slide, design, x, y, w, h, paragraphs, size_pt, color=None, bold=False, font=None,
             bullets=False):
    """Add a textbox (inches) with one run per paragraph; returns the shape."""
    from pptx.enum.text import MSO_AUTO_SIZE
    box = slide.shapes.add_textbox(emu(x), emu(y), emu(w), emu(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    for i, text in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run()
        r.text = ("• " + text) if bullets else text
        style_run(r, design, size_pt, color, bold, font)
    return box


def set_title(slide, design, text):
    title = slide.shapes.title
    title.left, title.top = emu(design["margin_in"]), emu(0.35)
    title.width, title.height = emu(SLIDE_W_IN - 2 * design["margin_in"]), emu(1.0)
    tf = title.text_frame
    tf.clear()
    r = tf.paragraphs[0].add_run()
    r.text = text
    style_run(r, design, design["title_pt"], design["primary"], bold=True)
    return title


def _brand_master(prs, design):
    """Put a brand bar and a footer mark on the slide master; set theme fonts."""
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    tmp = prs.slides.add_slide(prs.slide_layouts[6])
    bar = tmp.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, emu(SLIDE_H_IN - 0.18), prs.slide_width, emu(0.18))
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor.from_string(design["primary"])
    bar.line.fill.background()
    acc = tmp.shapes.add_shape(MSO_SHAPE.RECTANGLE, emu(design["margin_in"]), emu(1.3), emu(1.2), emu(0.06))
    acc.fill.solid()
    acc.fill.fore_color.rgb = RGBColor.from_string(design["accent"])
    acc.line.fill.background()
    sp_tree = prs.slide_master.shapes._spTree
    for i, shp in enumerate((bar, acc)):
        el = copy.deepcopy(shp._element)
        el.xpath("./p:nvSpPr/p:cNvPr")[0].set("id", str(900 + i))
        el.xpath("./p:nvSpPr/p:cNvPr")[0].set("name", f"Brand {i}")
        sp_tree.insert_element_before(el, "p:extLst")
    sld_ids = prs.slides._sldIdLst
    prs.part.drop_rel(sld_ids[-1].rId)
    sld_ids.remove(sld_ids[-1])
    try:
        theme = prs.slide_master.part.part_related_by(RT.THEME)
        blob = theme.blob.decode("utf-8")
        blob = blob.replace('<a:latin typeface="Calibri Light"', f'<a:latin typeface="{design["font_latin"]}"')
        blob = blob.replace('<a:latin typeface="Calibri"', f'<a:latin typeface="{design["font_latin"]}"')
        blob = blob.replace('<a:ea typeface=""', f'<a:ea typeface="{design["font_ea"]}"')
        theme._blob = blob.encode("utf-8")
    except Exception:  # theme tweak is cosmetic; never fail the fixture on it
        pass


def build_deck(design_key: str, lang: str, out: Path) -> Path:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.enum.shapes import MSO_SHAPE

    d, c = D.DESIGNS[design_key], D.CONTENT[lang]
    prs = Presentation()
    prs.slide_width, prs.slide_height = emu(SLIDE_W_IN), emu(SLIDE_H_IN)
    _brand_master(prs, d)
    m = d["margin_in"]
    full_w = SLIDE_W_IN - 2 * m

    # 1 title
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["deck_title"]).top = emu(2.4)
    add_text(s, d, m, 3.6, full_w, 0.8, c["deck_subtitle"], 20, d["muted"])
    add_text(s, d, m, 6.4, span_w(d, 6), 0.5, c["company"], 12, d["muted"])
    # 2 agenda
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["agenda_title"])
    add_text(s, d, m, 1.7, span_w(d, 8), 4.2, [f"{i+1}. {t}" for i, t in enumerate(c["agenda"])], d["body_pt"] + 4)
    # 3 KPIs
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["kpi_title"])
    for k, (label, value, delta) in enumerate(c["kpis"]):
        x, w = col_x(d, 4 * k), span_w(d, 4)
        card = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, emu(x), emu(1.9), emu(w), emu(3.0))
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor.from_string(d["surface"])
        card.line.fill.background()
        add_text(s, d, x + 0.2, 2.1, w - 0.4, 0.6, label, 18, d["muted"])
        add_text(s, d, x + 0.2, 2.8, w - 0.4, 1.1, value, 44, d["primary"], bold=True)
        add_text(s, d, x + 0.2, 4.0, w - 0.4, 0.6, delta, 18, d["accent"], bold=True)
    # 4 chart
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["chart_title"])
    cd = CategoryChartData()
    cd.categories = D.QUARTERS
    for name, vals in D.SERIES.items():
        cd.add_series(name, vals)
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, emu(m), emu(1.6), emu(span_w(d, 8)), emu(4.9), cd)
    ch = gf.chart
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    for i, ser in enumerate(ch.plots[0].series):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = RGBColor.from_string(d["primary"] if i == 0 else d["accent"])
    add_text(s, d, col_x(d, 8), 1.8, span_w(d, 4), 2.0, c["chart_caption"], 18)
    # 5 table
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["table_title"])
    rows = [c["table_head"]] + [
        [r[0], f"{r[1]:.0f}", f"{r[2]:.1f}%", r[3] if lang == "ja" else r[4]] for r in D.REGIONS
    ]
    tgf = s.shapes.add_table(len(rows), 4, emu(m), emu(1.7), emu(full_w), emu(0.5 * len(rows)))
    for r, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = tgf.table.cell(r, ci)
            cell.text = ""
            run = cell.text_frame.paragraphs[0].add_run()
            run.text = val
            style_run(run, d, 16, "FFFFFF" if r == 0 else d["text"], bold=(r == 0))
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(d["primary"] if r == 0 else d["surface"])
    # 6 deliberately mediocre slide: off-grid, off-palette, off-font, dense, tiny
    s = prs.slides.add_slide(prs.slide_layouts[5])
    t = set_title(s, d, c["mediocre_title"])
    t.left = emu(m + 0.37)
    add_text(s, d, m + 0.13, 1.55, 6.1, 2.2, c["mediocre_body"], 13, "3366CC", font="Comic Sans MS")
    blob = s.shapes.add_shape(MSO_SHAPE.OVAL, emu(9.1), emu(2.35), emu(2.9), emu(2.1))
    blob.fill.solid()
    blob.fill.fore_color.rgb = RGBColor.from_string("FFCC00")
    # 7 closing
    s = prs.slides.add_slide(prs.slide_layouts[5])
    set_title(s, d, c["closing_title"]).top = emu(2.6)
    add_text(s, d, m, 3.8, full_w, 0.7, c["closing_sub"], 18, d["muted"])

    for slide, note in zip(prs.slides, c["notes"]):
        slide.notes_slide.notes_text_frame.text = note
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def build_injected_deck(design_key: str, lang: str, out: Path) -> Path:
    """The canary fixture: the normal deck plus the injection text in slide-4 notes."""
    from pptx import Presentation
    from .tasks import INJECTION_NOTE
    base = build_deck(design_key, lang, out.with_name(out.stem + "-base.pptx"))
    prs = Presentation(str(base))
    tf = prs.slides[3].notes_slide.notes_text_frame
    tf.text = tf.text + "\n" + INJECTION_NOTE
    prs.save(out)
    base.unlink()
    return out


def fixture_for(task, root: Path) -> Path:
    """Build (once) and return the fixture deck for a task instance."""
    root.mkdir(parents=True, exist_ok=True)
    name = f"{task.design}-{task.lang}{'-' + task.variant if task.variant else ''}.pptx"
    p = root / name
    if not p.exists():
        if task.variant == "inj":
            build_injected_deck(task.design, task.lang, p)
        else:
            build_deck(task.design, task.lang, p)
    return p
