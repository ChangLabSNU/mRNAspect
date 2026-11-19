"""The report's screen view in direction C, and a printed heading that stays with its figures.

Decided 2026-10-28 (J6): ink on paper, one warm grey family, hue only for statuses -- the
window took it in J6a, the report's screen view in J6c. Measured on the 12-construct report
before J6c, as WeasyPrint's cascade computes the screen view: eight text colours, six
backgrounds and five border colours, among them the print grid (#888 and #ccc, 1,816 cell
sides) showing through under a screen rule that replaced only each cell's bottom edge, and
a blue bar (#3b6ea5) on every construct heading -- the only chrome hue left.

The guard reads the COMPUTED colours, not the stylesheet. Text in the stylesheet could be
all tokens while a print rule the screen never restated still painted the page, which is
exactly how the grid leaked. Layout under WeasyPrint is not the browser's; the cascade is.

In print, three pages ended on a heading whose figures began overleaf ("Across constructs",
"QK-09", "QK-07"): a figure avoids breaking inside, and nothing tied the heading to it.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting import figspec
from mrnaspect.reporting.reporter import CoAReporter

weasyprint = pytest.importorskip("weasyprint")
from weasyprint.formatting_structure import boxes  # noqa: E402

_TEMPLATE = (Path(__file__).resolve().parents[1] / "reporting" / "templates"
             / "sample_coa.html").read_text()


def _hex(c) -> str | None:
    if c.alpha == 0:
        return None
    r, g, b = c.to("srgb").coordinates
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _walk(box, in_margin=False):
    """Every box with whether it sits in a page MARGIN box. Those are print furniture
    (the page counter) that WeasyPrint also lays out for a screen render; a browser shows
    none of them."""
    in_margin = in_margin or isinstance(box, boxes.MarginBox)
    yield box, in_margin
    for child in getattr(box, "children", []):
        yield from _walk(child, in_margin)


def _paint(doc, *, skip_margins: bool) -> tuple[dict, set[str]]:
    """Colour -> count of boxes painting it, as text, background or border; and every
    class the render reached."""
    painted = {"text": collections.Counter(), "background": collections.Counter(),
               "border": collections.Counter()}
    classes: set[str] = set()
    for page in doc.pages:
        for box, in_margin in _walk(page._page_box):
            if in_margin and skip_margins:
                continue
            el = getattr(box, "element", None)
            if el is not None and el.get("class"):
                classes.update(el.get("class").split())
            s = box.style
            if isinstance(box, boxes.TextBox) and box.text.strip():
                h = _hex(s["color"])
                if h:
                    painted["text"][h] += 1
            h = _hex(s["background_color"])
            if h:
                painted["background"][h] += 1
            for side in ("top", "right", "bottom", "left"):
                if (s[f"border_{side}_width"]
                        and s[f"border_{side}_style"] not in ("none", "hidden")):
                    h = _hex(s[f"border_{side}_color"])
                    if h:
                        painted["border"][h] += 1
    return painted, classes


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, fixture_bam: Path, curlcake_ref: Path):
    """One run, both media: the screen HTML written to disk, and the string WeasyPrint is
    given for the PDF (not the same document -- print gets the static figures)."""
    out = tmp_path_factory.mktemp("screen_theme")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    captured = {}
    real = weasyprint.HTML

    class Spy(real):
        def __init__(self, *a, **k):
            if "string" in k:
                captured["print"] = k["string"]
            super().__init__(*a, **k)

    weasyprint.HTML = Spy
    try:
        reporter.finalize("run_end", force=True)
    finally:
        weasyprint.HTML = real
    html = out / "report_run_end.html"
    assert html.exists() and "print" in captured, sorted(p.name for p in out.iterdir())
    screen = _paint(weasyprint.HTML(filename=str(html), media_type="screen").render(),
                    skip_margins=True)
    printed = _paint(weasyprint.HTML(string=captured["print"], base_url=str(out)).render(),
                     skip_margins=False)
    return screen, printed


@pytest.fixture(scope="module")
def screen(rendered):
    return rendered[0]


_ALLOWED = {c.lower() for c in (
    figspec.INK_PRIMARY, figspec.INK_SECONDARY, figspec.INK_MUTED,
    figspec.GRIDLINE, figspec.AXIS_LINE,
    figspec.SURFACE, figspec.SURFACE_PANEL, figspec.SURFACE_SUNKEN,
    *(getattr(figspec, n) for n in dir(figspec) if n.startswith("STATUS_")),
)}


def test_the_render_reaches_what_the_guard_is_about(screen) -> None:
    """A fixture that stopped drawing tables or construct blocks would leave the guard
    below checking a page with nothing on it."""
    painted, classes = screen
    # construct-notice is the status hue on the page since the Status column's chips went
    # (2026-11-09): it paints with the warning tokens.
    assert {"results", "meta-block", "construct-title", "construct-notice"} <= classes, (
        classes)
    assert all(painted.values()), painted


def test_every_colour_on_screen_is_a_token(screen) -> None:
    painted, _ = screen
    stray = {kind: {c: n for c, n in counts.items() if c not in _ALLOWED}
             for kind, counts in painted.items()}
    stray = {k: v for k, v in stray.items() if v}
    assert not stray, f"painted on screen but not a figspec token: {stray}"


def test_the_screen_is_hue_free_but_for_statuses(screen) -> None:
    """Ink, rules and surfaces are greys; any hue must be a status."""
    painted, _ = screen
    statuses = {getattr(figspec, n).lower() for n in dir(figspec) if n.startswith("STATUS_")}
    for counts in painted.values():
        for c in counts:
            r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
            if max(r, g, b) - min(r, g, b) > 16:
                assert c in statuses, f"{c} is a hue and not a status"


# --- print: monochrome chrome ----------------------------------------------------------------

def test_the_pdf_chrome_is_monochrome_but_for_statuses(rendered) -> None:
    """The printed CoA was set as monochrome. Its greys are its own (print typography, not
    tokens), so this asks only that nothing but a status is a hue -- the construct bar was
    blue (#3b6ea5 on #f0f4f8) until it was made to match the screen, 2026-10-28. Figures
    are images and paint no box colour, so they are outside this by construction."""
    painted, classes = rendered[1]
    assert "construct-title" in classes, "the print render reached no construct heading"
    statuses = {getattr(figspec, n).lower() for n in dir(figspec) if n.startswith("STATUS_")}
    hues = {c for counts in painted.values() for c in counts
            if max(int(c[i:i + 2], 16) for i in (1, 3, 5))
            - min(int(c[i:i + 2], 16) for i in (1, 3, 5)) > 16}
    assert hues <= statuses, f"chrome hues in the PDF: {sorted(hues - statuses)}"


# --- the stylesheet ------------------------------------------------------------------------

def _screen_block() -> str:
    start = _TEMPLATE.index("@media screen {")
    depth, i = 0, _TEMPLATE.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(_TEMPLATE[i], 0)
        if depth == 0:
            break
        i += 1
    # The style's comments are template comments since 2026-11-16 (test_report_words.py).
    return re.sub(r"/\*.*?\*/|\{#.*?#\}", " ", _TEMPLATE[start:i + 1], flags=re.S)


def test_the_screen_block_names_no_colour_of_its_own() -> None:
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", _screen_block())


def test_the_screen_tokens_come_from_figspec() -> None:
    tokens = str(CoAReporter._design_tokens())
    for name, value in (("ink-primary", figspec.INK_PRIMARY), ("rule", figspec.GRIDLINE),
                        ("surface", figspec.SURFACE),
                        ("surface-panel", figspec.SURFACE_PANEL),
                        ("status-good", figspec.STATUS_GOOD)):
        assert f"--{name}: {value};" in tokens, name


# --- print: a heading stays with what it heads ----------------------------------------------

def _stylesheet() -> str:
    css = _TEMPLATE[_TEMPLATE.index("<style>") + len("<style>"):_TEMPLATE.index("</style>")]
    css = re.sub(r"\{#.*?#\}", " ", css, flags=re.S)     # as a render leaves them out
    return css.replace("{{ design_tokens }}", str(CoAReporter._design_tokens()))


def _pages_of(css: str, heading_class: str) -> tuple[int, int]:
    """(page of the heading, page of the first figure under it), for a heading that lands
    just above the foot of a page with a figure too tall to follow it there."""
    heading = f'<div class="{heading_class}">Across constructs</div>'
    figure = ('<div class="printed-figure"><div class="figure-caption"><strong>First '
              'figure.</strong></div><div style="height: 90mm"></div></div>')
    block = (f'<div class="figures-section">{heading}<div class="figure-grid">'
             f'{figure}{figure}</div></div>')
    if heading_class == "construct-figures-title":
        block = (f'<div class="figures-section"><div class="construct-figures">{heading}'
                 f'<div class="figure-grid">{figure}{figure}</div></div></div>')
    html = (f"<html><head><style>{css}</style></head><body>"
            f'<div style="height: 228mm"></div>{block}</body></html>')
    doc = weasyprint.HTML(string=html).render()
    found = {}
    for n, page in enumerate(doc.pages):
        for box, in_margin in _walk(page._page_box):
            if not in_margin and isinstance(box, boxes.TextBox):
                for word in ("Across constructs", "First figure."):
                    if word in box.text and word not in found:
                        found[word] = n
    return found["Across constructs"], found["First figure."]


@pytest.mark.parametrize("heading_class", ["section-title", "construct-figures-title"])
def test_a_printed_heading_stays_with_what_it_heads(heading_class) -> None:
    css = _stylesheet()
    # The control: without the rule this layout DOES orphan the heading. Otherwise a
    # layout that never put the heading at a page foot would pass for nothing.
    bare = re.sub(r"\.section-title, \.construct-figures-title \{ break-after: avoid; "
                  r"page-break-after: avoid; \}", "", css)
    assert bare != css, "the keep-with-next rule moved; this test is checking nothing"
    alone, overleaf = _pages_of(bare, heading_class)
    assert alone < overleaf, "the control layout no longer orphans the heading"
    head, first = _pages_of(css, heading_class)
    assert head == first, f".{heading_class} ends a page; its figure starts the next"
