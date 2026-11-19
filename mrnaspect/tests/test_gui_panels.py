"""The run view: one subject per panel, the Overview's gates, and dark charts only.

The redesign of 2026-11-06. Which panel a figure is on is decided in
Core (`reporter._FIGURE_PANEL`, carried in figures.json as `panel`); the GUI lists the
same panels in the same order and must never derive the grouping itself. The Overview's
two gates are the resolved config's, and its 25-construct cap is a drawing cap only: Core's
`analysis.max_constructs` is a memory ceiling that stops a run, so it must not move.

Read as text: no TypeScript compiler or browser runs on the suite host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mrnaspect.reporting import reporter

_REPO = Path(__file__).resolve().parents[2]
_HTML = _REPO / "gui" / "index.html"
_TS = _REPO / "gui" / "src" / "main.ts"
_RS = _REPO / "gui" / "src-tauri" / "src" / "lib.rs"
_CFG = _REPO / "mrnaspect" / "config" / "run_config.yaml"
_INTEGRATOR = _REPO / "mrnaspect" / "analysis" / "modules" / "qc_integrator.py"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists()), reason="GUI sources are not in this checkout")


def _code(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _panels() -> list[str]:
    block = re.search(r"const PANELS: \[string, string\]\[\] = \[(.*?)\];", _TS.read_text(), re.S)
    assert block, "main.ts no longer declares PANELS"
    return re.findall(r'\["([a-z-]+)",\s*"[^"]+"\]', block.group(1))


# --- the panels are Core's ---------------------------------------------------------------

def test_the_panels_after_the_overview_are_core_s_in_core_s_order() -> None:
    keys = _panels()
    assert keys[0] == "overview"
    assert keys[1:] == list(reporter.FIGURE_PANELS)


def test_every_figure_the_gui_names_is_on_the_panel_it_is_drawn_on() -> None:
    block = re.search(r"const PANEL_FIGS[^=]*= \{(.*?)\n\};", _TS.read_text(), re.S).group(1)
    for panel, body in re.findall(r'"([a-z-]+)":\s*\{([^}]*)\}', block):
        for fig in re.findall(r'"([a-z-]+)"', body):
            assert reporter._FIGURE_PANEL[fig] == panel, (fig, panel)


def test_the_gui_groups_figures_by_core_s_panel_key_only() -> None:
    ts = _code(_TS.read_text())
    assert "f.panel === key" in ts
    assert "FIGURE_SECTIONS" not in ts, "the report's grouping is back in the GUI"


# --- the tab row (WAI-ARIA tabs) -----------------------------------------------------------

def test_the_tab_row_is_a_tablist_of_tabs_controlling_one_panel() -> None:
    assert re.search(r'class="tabs" role="tablist" aria-label="Panels"', _HTML.read_text())
    fn = _TS.read_text()
    fn = fn[fn.index("function renderTabs("):]
    fn = fn[:fn.index("\n}\n")]
    for needed in ('role="tab"', "aria-selected=", 'aria-controls="panels"',
                   'tabindex="${sel ? 0 : -1}"', '"tabpanel"', "aria-labelledby"):
        assert needed in fn, needed


def test_an_empty_panel_stays_in_the_row_with_its_reason() -> None:
    ts = _code(_TS.read_text())
    assert 'const NEEDS_MINKNOW = "Needs MinKNOW files";' in _TS.read_text()
    assert 'class="empty" title="${esc(why)}"' in ts


def test_a_missing_chart_says_when_it_comes_not_after_the_run() -> None:
    """Core writes the charts on every interim since 2026-11-09, so "After the run" was
    wrong for a run in progress and for a run that failed (it never comes)."""
    ts = _code(_TS.read_text())
    assert "After the run" not in ts
    fn = ts[ts.index("function noChartsYet("):]
    fn = fn[:fn.index("\n}\n")]
    assert "isActive(lastPoll)" in fn and "minReads()" in fn


def test_arrow_keys_step_the_panels_but_not_inside_a_field() -> None:
    ts = _code(_TS.read_text())
    keys = ts[ts.index('document.addEventListener("keydown"'):]
    keys = keys[:keys.index("\n});\n")]
    assert '"ArrowLeft"' in keys and '"ArrowRight"' in keys
    assert '"INPUT"' in keys and '"SELECT"' in keys


# --- the Overview's gates and cap ------------------------------------------------------------

def test_the_gates_are_the_config_s_keys_with_the_shipped_values_as_fallback() -> None:
    shipped = yaml.safe_load(_CFG.read_text())
    ts = _code(_TS.read_text())
    assert 'dig(resolvedCfg, "min_reads")' in ts
    assert 'dig(resolvedCfg, "min_reads_per_construct")' in ts
    assert re.search(rf"const SHIPPED_MIN_READS = {shipped['min_reads']};", ts)
    assert re.search(rf"const SHIPPED_MIN_READS_PER_CONSTRUCT = "
                     rf"{shipped['min_reads_per_construct']};", ts)


def test_the_cap_is_25_and_core_s_ceiling_is_not_it() -> None:
    """25 in Core would stop a 30-construct run with an error instead of hiding five."""
    assert re.search(r"const OVERVIEW_CAP = 25;", _TS.read_text())
    ceiling = yaml.safe_load(_CFG.read_text()).get("analysis", {}).get("max_constructs")
    assert ceiling is None or ceiling > 25, ceiling


def test_the_overview_reads_only_keys_core_writes() -> None:
    """A per-construct ratio is a metric, so Core supplies it (C3); the shell only draws."""
    integ = _INTEGRATOR.read_text()
    ts = _TS.read_text()
    for key in ("share_of_reads", "full_length_fraction_lower", "n_reads",
                "consensus_identity_pct", "mismatch_site_count", "full_length_fraction_upper",
                "pt_coverage", "correction_applied", "q1_length", "q3_length"):
        assert key in ts, f"the Overview no longer reads {key}"
        assert re.search(rf"\b{key}\b", integ), f"{key} is not a key the integrator writes"


def test_the_rust_panel_forwards_what_the_overview_reads() -> None:
    rs = _RS.read_text()
    for needed in ('"inputs_processed":  v["inputs_processed"]',
                   '"inputs_total":      v["inputs_total"]',
                   '"metrics":           v["qc_metrics"]["metrics"]',
                   '"quality_score":     v["snapshots"]["run_summary"]["quality_score"]',
                   '"quality_score": v["snapshots"]["run_summary"]["quality_score_distribution"]'):
        assert needed in rs, needed


def test_the_run_card_counts_inputs_from_the_run_s_first_moment() -> None:
    """latest_snapshot.json comes when an input FINISHES; run_status.json from the start
    (runtime/run_status.py). Rust forwards it as Poll.status, and the card and the
    waiting box read it (2026-11-12: whether the input was recognised was not clear
    until the first result)."""
    from mrnaspect.runtime.run_status import RUN_STATUS_NAME

    rs = _RS.read_text()
    poll = rs[rs.index("fn poll_run()"):]
    poll = poll[:poll.index("\n}\n")]
    assert f'st.run_dir.join("{RUN_STATUS_NAME}")' in poll
    assert "status: st.last_status.clone()," in poll
    ts = _code(_TS.read_text())
    card = ts[ts.index("function renderRunCard("):]
    card = card[:card.index("\n}\n")]
    assert "const st = r.status ?? {};" in card and "st.inputs_found" in card
    # Until the run's own file arrives (a POD5 run starts its server first): Core's count
    # from before the run, taken at Start.
    assert "const inputs = done == null ? early" in card and "runInputsFound" in card
    go = ts[ts.index('$("go").addEventListener("click"'):]
    assert "runInputsFound = inputInfo?.use ? inputInfo.inputs : null;" in go[:go.index("\n});\n")]


def test_minknow_keeps_its_capitals_in_the_health_line() -> None:
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderOverview("):]
    fn = fn[:fn.index("\n}\n")]
    assert "NEEDS_MINKNOW.toLowerCase()" not in fn, '"needs minknow files" on screen'


def test_the_poly_a_bars_span_the_rows_own_range() -> None:
    """From zero, every bar sat in the last fifth of its cell (Chromium check, 2026-11-12)."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function polyaScale("):]
    fn = fn[:fn.index("\n}\n")]
    assert "Math.min(...all.map(t => t.q1" in fn and "return [lo - pad, hi + pad];" in fn


def test_a_narrow_window_steps_the_overview_s_headlines_down() -> None:
    """At the default 1180 x 760 every top card's header broke in two."""
    html = _HTML.read_text()
    block = html[html.index("@media (max-width: 1360px)"):]
    block = block[:block.index("\n      }\n")]
    assert ".ovhead .h, .reads .v { font-size:var(--type-heading) }" in block


# --- dark charts, every time -------------------------------------------------------------------

def test_every_chart_is_themed_before_plotly_draws_it() -> None:
    """A spec drawn as written is a light chart on a dark panel, with no error; on the
    light theme it would keep the faint grid (2026-11-12)."""
    ts = _code(_TS.read_text())
    for m in re.finditer(r"Plotly\.newPlot\(", ts):
        fn_start = ts.rfind("\nfunction ", 0, m.start())
        body = ts[fn_start:m.start()]
        assert "themeSpec(" in body, ts[fn_start:fn_start + 80]


# --- poly(A) in the tables: corrected where it applied (2026-11-12) ---------------------

def test_a_poly_a_row_reports_the_corrected_values_where_its_correction_applied() -> None:
    """Both tables showed the as-called median, unlabelled, though every construct's
    correction had applied (15 of 15 on the 2026-11-09 timing run)."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function shownTail("):]
    fn = fn[:fn.index("\n}\n")]
    assert 't.correction_applied === true && typeof t.median_length_corrected === "number"' in fn
    for k in ("median_length_corrected", "q1_length_corrected", "q3_length_corrected"):
        assert k in fn, k
    cols = ts[ts.index("function tableCols("):]
    cols = cols[:cols.index("\n}\n")]
    assert 'head: "Median", n: true, cell: c => polyaMedian(' in cols
    assert 'head: "Uncorrected"' in cols
    med = ts[ts.index("function polyaMedian("):]
    med = med[:med.index("\n}\n")]
    assert 'class="uncorr"' in med, "an uncorrected value is not said to be one"
    cons = ts[ts.index("function renderConstructs("):]
    cons = cons[:cons.index("\n}\n")]
    assert "polyaMedian(measured(c.polya_tail))" in cons
    integ = _INTEGRATOR.read_text()
    for k in ("median_length_corrected", "q1_length_corrected", "q3_length_corrected",
              "correction_applied", "correction_status"):
        assert re.search(rf"\b{k}\b", integ), f"{k} is not a key the integrator writes"


def test_the_overview_s_poly_a_stays_uncorrected_and_says_why() -> None:
    """Core withholds a whole-run correction (it is per construct); the card keeps the
    as-called median and its tag says where the corrected values are (2026-11-12)."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderOverview("):]
    fn = fn[:fn.index("\n}\n")]
    assert 'pa?.correction_status === "withheld"' in fn
    assert "median_length_corrected" not in fn


def test_a_card_and_a_panel_carry_their_result_type() -> None:
    """So the page's own marks take the colour code (--mark, set by [data-result])."""
    ts = _code(_TS.read_text())
    card = ts[ts.index("const cardOpen ="):]
    assert 'data-result="${panel}"' in card[:card.index(";")]
    fn = ts[ts.index("function renderCurrentPanel("):]
    fn = fn[:fn.index("\n}\n")]
    assert "el.dataset.result = current;" in fn


def test_a_per_construct_figure_is_driven_by_its_own_dropdown_buttons() -> None:
    """The shell hides Core's construct dropdown and applies the chosen button's args:
    it draws nothing of its own. Core's other control (Both / Corrected / As called) is
    redrawn as page buttons whose visibility is combined with the construct's, so the two
    no longer undo each other."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function drawFig("):]
    fn = fn[:fn.index("\n}\n")]
    assert "visible: false" in fn
    assert "b.label === row" in fn
    assert "btn.args" in fn and "figState.get(el)!.construct = traces?.visible" in fn
    vis = ts[ts.index("function applyVisibility("):]
    vis = vis[:vis.index("\n}\n")]
    assert "st.construct" in vis and "st.mode?.opacity" in vis


def test_every_per_construct_figure_on_a_table_panel_follows_the_row() -> None:
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderTablePanel("):]
    fn = fn[:fn.index("\n}\n")]
    assert "panelFigures(key, skip, [], true)" in fn
    assert 'data-drive="1"' in fn


def _panel_figs() -> dict[str, str]:
    block = re.search(r"const PANEL_FIGS[^=]*= \{(.*?)\n\};", _TS.read_text(), re.S).group(1)
    return dict(re.findall(r'"([a-z-]+)":\s*\{([^}]*)\}', block))


def test_full_length_shows_its_three_figures_in_one_card_switched_by_buttons() -> None:
    """Read start and read end were drawn below the shortfall card, so the panel ran past
    one window (2026-11-12). Now the three share one card beside the table, with a
    button each in its header, and none is drawn again below it."""
    body = _panel_figs()["full-length"]
    assert re.search(r'per:\s*"shortfall"', body)
    assert re.search(r'alts:\s*\[\s*"read-start",\s*"read-end"\s*\]', body)
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderTablePanel("):]
    fn = fn[:fn.index("\n}\n")]
    assert "[pf.whole, ...choices].map(idx)" in fn, "an alternative is drawn again below"
    assert "figSwitch(choices" in fn
    sw = ts[ts.index("function figSwitch("):]
    sw = sw[:sw.index("\n}\n")]
    assert 'class="seg" data-figswitch=' in sw and "aria-pressed=" in sw
    wire = ts[ts.index("function wireTablePanel("):]
    wire = wire[:wire.index("\n}\n")]
    assert "chosenFig[key] = b.dataset.figswitch!" in wire
    clear = ts[ts.index("function clearRun("):]
    assert "chosenFig = {}" in clear[:clear.index("\n}\n")], "a new run keeps the last switch"


# --- the Overview before and between results (2026-11-12) ---------------------------------

def test_before_the_first_result_the_overview_is_drawn_empty_under_one_box() -> None:
    """It was one line of text until the first result; the charts' frames were asked for
    from the start, with a tinted box of white text over them."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderOverview("):]
    fn = fn[:fn.index("\n}\n")]
    assert "const waiting = !panel;" in fn
    assert '<p class="empty">' not in fn, "the bare line is back"
    # One translucent black box over the whole Overview, white text (2026-11-13).
    assert 'class="ovwait" role="status"><b>${esc(wait)}</b>' in fn
    assert "const [wait, waitSub] = waitingFor(r);" in fn
    why = ts[ts.index("function waitingFor("):]
    why = why[:why.index("\n}\n")]
    assert '"Waiting for the first result"' in why and "r.note" in why
    # How far the run has got, from run_status.json, under the line (2026-11-12).
    assert "st.inputs_total" in why and "st.inputs_found" in why


def test_a_card_waiting_for_reads_says_so_in_words_alone() -> None:
    """In text alone, without the box (2026-11-13): one box over the Overview says
    the run has nothing yet; a card's own wait is a line of text in its frame."""
    ts = _code(_TS.read_text())
    assert "waitPill" not in ts and "waitpill" not in _HTML.read_text()
    fn = ts[ts.index("function renderOverview("):]
    fn = fn[:fn.index("\n}\n")]
    # The top row's words are readsWait's, shared with the panels it opens (2026-11-13).
    assert "readsWait(p)" in fn
    assert "`Waiting for a construct to reach ${num(PER)} reads`" in fn


def test_the_box_is_white_on_translucent_black_over_the_whole_overview() -> None:
    """The box (2026-11-13): a rounded, translucent black rectangle over the whole
    Overview, white text as before."""
    html = _HTML.read_text()
    rule = re.search(r"\.ovwait \{([^}]*)\}", html).group(1)
    assert "inset:0;" in rule.replace(" ", "") or "inset:0 " in rule
    assert "background:var(--scrim)" in rule and "color:var(--on-scrim)" in rule
    assert "border-radius" in rule


def test_the_overview_draws_a_gridline_at_every_quarter_and_tick() -> None:
    """Three faint lines (0, 50, 100%) were too few and hard to see (2026-11-12)."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function renderOverview("):]
    fn = fn[:fn.index("\n}\n")]
    assert "[0, 0.25, 0.5, 0.75, 1]" in fn
    assert fn.count("gridLines(quarters(") == 3
    top = ts[ts.index("function topCardHtml("):]
    top = top[:top.index("\n}\n")]
    assert 'class="hx-grid"' in top
    html = _HTML.read_text()
    assert re.search(r"\.cplot \.gl \{[^}]*background:var\(--grid\)", html)
    assert re.search(r"\.hx-grid \{[^}]*stroke:var\(--grid\)", html)


def test_core_s_title_is_page_text_not_plotly_text() -> None:
    """The construct name and Core's detail line are drawn by the page; Plotly's title is
    emptied, so it neither takes a two-line margin nor collides with the legend."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function drawFig("):]
    fn = fn[:fn.index("\n}\n")]
    assert 's.layout.title = { text: "" }' in fn and "setSub(i, titleText)" in fn


# --- charts during the run ----------------------------------------------------------------

def test_figures_are_read_again_whenever_core_rewrites_them() -> None:
    """Core writes figures.json on every render, interims included (reporter._emit_report);
    the shell reads it on Rust's `figures_changed`, not once after the run."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("async function tick("):]
    fn = fn[:fn.index("\n}\n")]
    assert "r.figures_changed ? await loadFigures()" in fn
    assert "!active && !figures.length" not in fn
    rs = _RS.read_text()
    assert 'mtime_ns(&st.run_dir.join("figures.json"))' in rs
    assert "last_mtime: Option<u128>" in rs, "a whole-second stamp misses a bam-mode input"


def test_a_construct_without_its_own_chart_gets_a_note_not_the_last_chart() -> None:
    """Core draws per-construct charts only for constructs with min_reads_per_construct
    reads; picking a smaller one in the table left the previous construct's chart up
    (Mac, 2026-11-09)."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function drawFig("):]
    fn = fn[:fn.index("\n}\n")]
    note = fn.index("if (drive && ci >= 0 && row && !btn) { noFigure(el, i, row); return; }")
    assert note < fn.index("Plotly.newPlot("), "the note must come before anything is drawn"
    body = ts[ts.index("function noFigure("):]
    assert "minPerConstruct()" in body[:body.index("\n}\n")]


def test_the_cards_open_on_core_s_window() -> None:
    """Read length and poly(A) cards open where their panels' charts do."""
    from mrnaspect.reporting import figspec

    m = re.search(r"const WINDOW_QUANTILES: \[number, number\] = \[([\d.]+), ([\d.]+)\];",
                  _TS.read_text())
    assert m and (float(m.group(1)), float(m.group(2))) == figspec.WINDOW_QUANTILES
