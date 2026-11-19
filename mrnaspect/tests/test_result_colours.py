"""A colour per result type (2026-11-12; option A, 2026-11-12).

Every figure on the Overview and in the report was the same blue. Each panel's figures
now wear their result type's colour, from `figspec.RESULT_COLOURS`: six types in four
families, all CATEGORICAL slots, because no fifth colour clears the palette's gates
beside the four on a dark ground. The poly(A) panel's second series (corrected) takes
the poly(A) family's other lightness step, so green keeps one meaning.

What must NOT move: the printed page. Print draws a figure's primary series in slot 1's
grey, unhatched, whichever colour it wears on screen (the `meta.result` tag), and the
corrected series in the grey and hatch it had as slot 2.
"""

from __future__ import annotations

import itertools
import json

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting import figrender, figspec, reporter
from mrnaspect.reporting.reporter import CoAReporter

from .test_flag_emphasis_renders import _paints
from .test_palette_separation import _CVD, delta_e
from .test_report_first_page import POLYA, SAMPLE

pytest.importorskip("matplotlib")

RC = figspec.RESULT_COLOURS


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- the code itself ------------------------------------------------------------------------

def test_every_result_panel_has_a_colour_and_the_flow_cell_none() -> None:
    """The flow cell's colours are channel states, categories of their own."""
    assert set(RC) == set(reporter.FIGURE_PANELS) - {"flow-cell"}


def test_four_families_all_from_the_validated_slots() -> None:
    assert set(RC.values()) <= set(figspec.CATEGORICAL)
    assert RC["read-length"] == RC["full-length"], "length"
    assert RC["quality"] == RC["identity"], "accuracy"
    families = {RC["read-length"], RC["polya"], RC["quality"], RC["constructs"]}
    assert len(families) == 4, "two families share a colour"


@pytest.mark.parametrize("main, step, grounds", [
    (figspec.CATEGORICAL[2], figspec.POLYA_CORRECTED,
     (figspec.SURFACE, figspec.SURFACE_PANEL)),
    (figspec.CATEGORICAL_DARK[2], figspec.POLYA_CORRECTED_DARK,
     tuple(figspec.DARK[k] for k in ("surface-menu", "surface-page", "surface-panel",
                                     "surface-raised"))),
], ids=["light", "dark"])
def test_the_corrected_step_clears_the_gates_beside_the_poly_a_colour(main, step, grounds):
    """They share one chart, so the pair is held to the palette's own floors."""
    assert delta_e(main, step) >= 15
    assert min(delta_e(main, step, k) for k in _CVD) >= 8
    assert min(_contrast(step, g) for g in grounds) >= 3.0


def test_the_corrected_step_is_no_status_colour() -> None:
    for status in (figspec.STATUS_SERIOUS, figspec.STATUS_WARNING, figspec.STATUS_GOOD,
                   figspec.EMPHASIS):
        assert delta_e(figspec.POLYA_CORRECTED, status) >= 15, status
    for k in ("status-serious", "status-warning", "status-good", "emphasis"):
        assert delta_e(figspec.POLYA_CORRECTED_DARK, figspec.DARK[k]) >= 15, k


def test_the_dark_map_carries_the_corrected_step() -> None:
    cmap = figspec.dark_colour_map()
    assert cmap[figspec.POLYA_CORRECTED.lower()] == figspec.POLYA_CORRECTED_DARK


def test_a_tag_goes_on_the_traces_drawn_in_the_colour_only() -> None:
    plum = figspec.CATEGORICAL[2]
    spec = {"data": [
        {"type": "scatter", "line": {"color": plum}},
        {"type": "scatter", "fillcolor": figspec.fill_of(plum)},           # by its rgb
        {"type": "bar", "marker": figspec._bar_marker(plum)},
        {"type": "scatter", "line": {"color": figspec.POLYA_CORRECTED}},
        {"type": "scatter", "line": {"color": plum}, "meta": {"emphasis": True}},
    ]}
    figspec.tag_result(spec, plum)
    tags = [(t.get("meta") or {}).get("result") for t in spec["data"]]
    assert tags == [plum, plum, plum, None, plum]
    assert spec["data"][4]["meta"]["emphasis"] is True, "a tag replaced another"


# --- a real run's figures ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def figures(tmp_path_factory, variant_bam, qk_ha_ref) -> dict[str, dict]:
    out = tmp_path_factory.mktemp("result_colours")
    rep = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref, sample=SAMPLE, module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    # As figures.json carries them (the GUI's copy and the HTML report's).
    return {f["id"]: f for f in json.loads(reporter._figures_json(rep._interactive_figures()))}


def _colour(trace) -> str:
    marker = trace.get("marker") or {}
    return ((trace.get("line") or {}).get("color")
            or (marker.get("line") or {}).get("color") or marker.get("color")).lower()


#: Figures whose series are categories, not one result: they keep the categorical order.
_CATEGORIES = {"per-position", "shortfall"}


def test_each_figure_s_primary_series_wears_its_panel_s_colour(figures) -> None:
    checked = 0
    for fid, f in figures.items():
        want = RC.get(f["panel"])
        if want is None or fid in _CATEGORIES:
            continue
        first = f["spec"]["data"][0]
        assert _colour(first) == want.lower(), (fid, _colour(first), want)
        assert (first.get("meta") or {}).get("result") == want.lower(), fid
        checked += 1
    assert checked >= 8, f"only {checked} figures were checked: {sorted(figures)}"


def test_categories_keep_the_categorical_order_and_no_tag(figures) -> None:
    for fid in _CATEGORIES & set(figures):
        assert not any((t.get("meta") or {}).get("result")
                       for t in figures[fid]["spec"]["data"]), fid
    if "per-position" in figures:
        used = {_colour(t) for t in figures["per-position"]["spec"]["data"]
                if t.get("type") == "bar"}
        assert used <= {c.lower() for c in figspec.CATEGORICAL[:3]}, used


def test_the_corrected_series_is_the_poly_a_colour_s_second_step(figures) -> None:
    for fid in ("polya", "polya-by-construct"):
        if fid not in figures:
            continue
        names = {(t.get("name"), _colour(t)) for t in figures[fid]["spec"]["data"]}
        # "corrected", or on the whole run "corrected (assigned reads)" (2026-11-13).
        assert any(str(n).startswith("corrected") and c == figspec.POLYA_CORRECTED.lower()
                   for n, c in names), (fid, names)
        assert not any(c == figspec.CATEGORICAL[1].lower() for _, c in names), (
            f"{fid}: slot 2 is the composition colour, not poly(A)'s")


# --- the printed page does not move ------------------------------------------------------------

def _primary(colour: str) -> dict:
    hist = {"bin_counts": [1, 4, 2], "bin_min": 0.0, "bin_width": 10.0, "n_bins": 3,
            "bin_max": 30.0, "n": 7}
    return figspec.tag_result(figspec.histogram_spec([("s", hist, colour)], x_title="x"),
                              colour)


@pytest.mark.parametrize("panel", sorted(RC))
def test_print_draws_every_result_colour_in_slot_1_s_grey_unhatched(panel) -> None:
    paints = set(_paints(_primary(RC[panel])))
    assert "#3f3f3f" in paints, paints
    slot_greys = {"#6b6b6b", "#949494", "#545454"}
    assert not paints & slot_greys, f"{panel} prints as its slot, not as the primary"
    svg = figrender.render_spec(_primary(RC[panel]), theme=figrender.GRAYSCALE)[0].svg
    assert b"<pattern" not in svg, f"{panel}'s primary series printed hatched"


def test_print_draws_the_corrected_series_as_it_did_as_slot_2() -> None:
    hist = {"bin_counts": [1, 4, 2], "bin_min": 0.0, "bin_width": 10.0, "n_bins": 3,
            "bin_max": 30.0, "n": 7}
    spec = figspec.tag_result(figspec.histogram_spec(
        [("uncorrected", hist, RC["polya"]), ("corrected", hist, figspec.POLYA_CORRECTED)],
        x_title="x"), RC["polya"])
    assert "#6b6b6b" in set(_paints(spec))
    svg = figrender.render_spec(spec, theme=figrender.GRAYSCALE)[0].svg
    assert b"<pattern" in svg, "the corrected series lost its print hatch"
    assert figrender.GRAYSCALE.hatch(figspec.POLYA_CORRECTED) == figrender.GRAYSCALE.hatch(
        figspec.CATEGORICAL[1])


@pytest.mark.parametrize("pair", list(itertools.combinations(sorted(set(RC.values())), 2)))
def test_untagged_slots_still_print_apart(pair) -> None:
    """The tag changes only tagged traces: categories keep their own greys and hatches."""
    a, b = pair
    assert figrender.GRAYSCALE(a) != figrender.GRAYSCALE(b) or (
        figrender.GRAYSCALE.hatch(a) != figrender.GRAYSCALE.hatch(b))
