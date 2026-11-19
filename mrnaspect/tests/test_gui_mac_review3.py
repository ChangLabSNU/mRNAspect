"""The third Mac review of the redesigned GUI (2026-11-13, a live run of QK-10).

One test per note, each named for what the GUI now does. Read as text, like the other
GUI tests: no TypeScript compiler or browser runs on the suite host (the session checks
the pages in headless Chromium and on the Mac).

Notes, in the order given:
- the read-length, poly(A) and quality panels drew charts before the Overview's 1,000
  mapped reads: they now wait as the Overview does;
- Find MinKNOW runs named a run by its folder, two levels under the name typed into
  MinKNOW: a run now leads with its experiment and sample;
- a reference can be pasted instead of picked;
- the Uridine step is Modification;
- before the first result one translucent black box covers the whole Overview; a card
  waiting for reads says so in words alone;
- with one construct, a bar, a lollipop and a box said less than their numbers: the
  Overview shows the numbers, and by-construct figures are left out of the panels;
- View interim report was too loud a blue;
- the poly(A) card's line wrapped;
- the poly(A) whole run read "correction withheld" beside a corrected curve: the note is
  on the page, under the table (the curve stays, 2026-11-13);
- "As called" is "Uncorrected" everywhere, and a legend entry shows or hides its curve;
- the flow cell fits one window: no strand sparkline (Core's occupancy figure below draws
  the same), summaries above their figures, roomier rows;
- Settings › Appearance is Display, without the manuscript sentence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HTML = _REPO / "gui" / "index.html"
_TS = _REPO / "gui" / "src" / "main.ts"
_RS = _REPO / "gui" / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _fn(ts: str, head: str) -> str:
    body = ts[ts.index(head):]
    return body[:body.index("\n}\n")]


def _ts() -> str:
    return _code(_TS.read_text())


def _section(page: str) -> str:
    html = _HTML.read_text()
    body = html[html.index(f'id="page-{page}"'):]
    return body[:body.index("</section>")]


def _css_rule(selector: str) -> str:
    html = _HTML.read_text()
    css = html[html.index("<style>"):html.index("</style>")]
    m = re.search(r"(?:^|[}\s/])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


# --- the panels wait for the Overview's reads ---------------------------------------------

def test_the_panels_the_top_row_opens_wait_for_its_reads() -> None:
    ts = _ts()
    gate = _fn(ts, "function readsWait(")
    assert "minReads()" in gate and "isActive(lastPoll)" in gate
    assert "`Waiting for ${num(minReads())} mapped reads`" in gate
    assert "`Fewer than ${num(minReads())} mapped reads`" in gate, "an ended run waits for nothing"
    why = _fn(ts, "function panelReason(")
    # Which panels wait, and that the wait comes first, is test_gui_mac_review4.py's
    # (2026-11-14: every panel of reads, not only these three).
    for key in ("read-length", "polya", "quality"):
        assert f'"{key}"' in ts[ts.index("const MIN_GATED"):ts.index("]);", ts.index("const MIN_GATED"))]
    # The gate comes before the live histograms that drew too early.
    assert why.index("readsWait(p)") < why.index("distributions?.read_length")
    ov = _fn(ts, "function renderOverview(")
    assert "readsWait(p)" in ov, "the Overview and its panels must say the same thing"


# --- input: the run's MinKNOW names; a pasted reference; Modification ----------------------

def test_a_minknow_run_leads_with_its_experiment_and_sample() -> None:
    ts = _ts()
    title = ts[ts.index("const runTitle ="):]
    title = title[:title.index(";\n")]
    assert "r.experiment && r.sample" in title and "r.name" in title
    info = _fn(ts, "function renderInputInfo(")
    assert info.count("runTitle(") >= 2, "the run picked and the runs listed both use it"


def test_the_paste_rule_is_core_s_reference_rule() -> None:
    """One rule, Core's (runtime/reference_check._BASES): A, C, G, T or U, any case."""
    from mrnaspect.runtime.reference_check import _BASES

    m = re.search(r"const FASTA_BASES = /\^\[([A-Za-z]+)\]\*\$/;", _TS.read_text())
    assert m, "main.ts no longer states the bases a pasted sequence may hold"
    assert set(m.group(1)) == set(_BASES)


def test_a_pasted_reference_is_checked_named_and_written_as_dna() -> None:
    ts = _ts()
    fn = _fn(ts, "function pastedFasta(")
    for said in ("is not A, C, G, T or U", "Name this sequence", "named twice"):
        assert said in fn, said
    assert '.replace(/U/g, "T")' in fn, "U is written as T, as a DNA reference is"
    html = _HTML.read_text()
    step = html[html.index('<div data-step="reference"'):]
    step = step[:step.index('<div data-step="arm"')]
    assert 'data-refmode="file"' in step and 'data-refmode="paste"' in step
    assert '<textarea id="reftext"' in step and 'id="refname"' in step
    miss = _fn(ts, "function missingOnStep(")
    assert 'refMode === "paste"' in miss


def test_a_pasted_reference_reaches_core_as_a_file_beside_the_run_s_logs() -> None:
    """Core reads files, so the shell writes the text where it writes the run's other
    record of what it was given ({stem}_realtime.yaml), and passes that path."""
    ts = _ts()
    go = ts[ts.index('$("go").addEventListener("click"'):]
    go = go[:go.index("\n});\n")]
    assert "referenceText:" in go
    rs = _RS.read_text()
    body = rs[rs.index("fn start_qc("):]
    body = body[:body.index("\n}\n")]
    assert "reference_text: String" in body
    assert 'out.join(format!("{stem}_reference.fa"))' in body
    assert "both a reference file and a pasted reference" in body
    write = body.index("std::fs::write(&pasted_ref")
    assert body.index("std::fs::create_dir_all(&out)") < write < body.index('("--reference",')


def test_the_arm_step_is_named_modification() -> None:
    ts = _ts()
    names = ts[ts.index("const STEP_NAMES"):]
    names = names[:names.index("};")]
    assert 'arm: "Modification"' in names and "Uridine" not in names
    assert '<label id="armlabel">Modification</label>' in _HTML.read_text()


# --- the Overview -----------------------------------------------------------------------

def test_the_box_covers_the_whole_overview_and_says_its_line_under_its_title() -> None:
    rule = _css_rule(".ovwait")
    assert re.search(r"inset:\s*0\s*;", rule), "the box covers the whole Overview"
    ts = _ts()
    ov = _fn(ts, "function renderOverview(")
    assert "<small>${esc(waitSub)}</small>" in ov


def test_a_card_s_wait_is_text_alone() -> None:
    for sel in (".plotbox .waittext", ".cwait", ".tile .waittext"):
        rule = _css_rule(sel)
        assert "background" not in rule and "border" not in rule, sel


def test_one_construct_is_said_in_numbers_not_drawn() -> None:
    ts = _ts()
    ov = _fn(ts, "function renderOverview(")
    assert "const single = keep.length === 1;" in ov
    for card in ('tileCard("Construct share"', 'tileCard("Per-base identity"',
                 'tileCard("Full length by construct"'):
        assert card in ov, card
    tile = _fn(ts, "function tileCard(")
    assert 'class="tile"' in tile and 'class="tv"' in tile and 'class="ts"' in tile


def test_the_poly_a_card_s_line_fits_one_line() -> None:
    ts = _ts()
    ov = _fn(ts, "function renderOverview(")
    assert "tail called ${pct(pa.pt_coverage)}" not in ov
    # A whole percentage, before the words. Written through tw() since R5 (the number
    # counts up); "p0" is pct(x, 0).
    assert '${tw("top.polya.called", pa.pt_coverage, "p0")} tail called' in ov
    top = _fn(ts, "function topCardHtml(")
    # "+N beyond" moved into the plot's corner: on the line it pushed the line to wrap.
    start = top.index('<div class="ovsub">')
    sub = top[start:top.index('<div class="plotbox">', start)]
    assert "outsideNote(" not in sub
    assert "white-space:nowrap" in _css_rule(".ovline").replace(" ", "")


def test_the_report_button_is_a_tint_not_a_fill() -> None:
    html = _HTML.read_text()
    assert '<button class="btn soft" id="openrep"' in html
    assert '<button class="btn primary" id="openrep"' not in html


# --- one construct: no by-construct figure --------------------------------------------------

def test_a_by_construct_figure_is_left_out_when_there_is_one_construct() -> None:
    ts = _ts()
    assert 'const BY_CONSTRUCT = new Set(["median-length", "identity-by-construct", "share"]);' in ts
    one = ts[ts.index("const oneConstruct ="):]
    assert "=== 1" in one[:one.index(";\n")]
    figs = _fn(ts, "function panelFigsOf(")
    assert "BY_CONSTRUCT.has(pf.whole)" in figs, "identity opens on per-position"
    table = _fn(ts, "function renderTablePanel(")
    assert "panelFigsOf(key, p)" in table and "singleSkip(p)" in table
    assert "panelFigsOf(key, p)" in _fn(ts, "function rowFor(")
    cur = _fn(ts, "function renderCurrentPanel(")
    assert 'panelFigures("read-length", singleSkip(p)' in cur
    assert "singleSkip(p)" in _fn(ts, "function renderConstructs(")


def test_the_read_length_figures_share_one_window() -> None:
    ts = _ts()
    cur = _fn(ts, "function renderCurrentPanel(")
    assert re.search(r'panelFigures\("read-length", singleSkip\(p\), \[[^\]]*\], false, true\)', cur)
    pf = _fn(ts, "function panelFigures(")
    assert 'class="charts${fit ? " fit" : ""}"' in pf and "--cols:" in pf
    rule = _css_rule(".charts.fit .fig")
    assert "calc(100vh" in rule


# --- poly(A) -----------------------------------------------------------------------------

def test_uncorrected_is_the_word_in_the_gui() -> None:
    ts = _TS.read_text()
    assert not re.search(r"\bas[ -]called\b", _code(ts), re.I), "the GUI still says as called"
    assert 'head: "Uncorrected"' in ts
    assert "ascalled" not in ts and "ascalled" not in _HTML.read_text()


def test_the_whole_run_row_says_why_beside_the_corrected_curve() -> None:
    """The run-level correction is withheld (it is per construct) while the whole-run
    chart draws the constructs' corrected reads pooled; the table says both, on the page."""
    ts = _ts()
    note = _fn(ts, "function wholeRunNote(")
    assert 'correction_status === "withheld"' in note and 'class="tablenote"' in note
    assert "the chart's corrected curve" in note
    assert "wholeRunNote(key, p)" in _fn(ts, "function renderTablePanel(")


def test_a_legend_entry_shows_or_hides_its_curve() -> None:
    ts = _ts()
    draw = _fn(ts, "function drawFig(")
    assert '.on("plotly_legendclick"' in draw and "return false;" in draw
    vis = _fn(ts, "function applyVisibility(")
    assert "st.hidden.has(" in vis and '"legendonly"' in vis
    # A view button is a preset: it clears what the legend hid.
    assert "hidden.clear()" in draw


# --- the flow cell ------------------------------------------------------------------------

def test_the_flow_cell_draws_no_sparkline_and_fits_one_window() -> None:
    ts = _ts()
    fc = _fn(ts, "function renderFlowCell(")
    assert "spark(" not in ts, "the strand sparkline is back; Core's occupancy figure draws it"
    assert 'const FLOW_ORDER = ["pore", "end-reason", "voltage"];' in ts
    assert 'panelFigures("flow-cell", new Set(), FLOW_ORDER, false, true)' in fc
    rule = _css_rule(".inst table td")
    pad = re.search(r"padding:\s*var\(--space-(\w+)\)", rule).group(1)
    assert pad in ("s", "m"), "the reason rows sat 2 px apart"


# --- Settings -----------------------------------------------------------------------------

def test_display_says_what_the_switch_does_and_nothing_about_manuscripts() -> None:
    page = _section("display")
    assert "<h1>Display</h1>" in page
    assert "manuscript" not in page.lower() and "poster" not in page.lower()
    nav = _HTML.read_text()
    assert re.search(r'data-page="display">\s*<svg[^>]*>.*?</svg>\s*<span class="lbl">Display</span>',
                     nav, re.S)
