"""Every construct really reaches every per-construct figure, from a real run.

Checked end to end over a real engine and a real render, because the defect this exists
to stop is invisible to a fixture-based test: a construct track registers its read-length
module under `read_stats`, not under the run-level module name, so reading the wrong key
returns {} and that construct's figure vanishes with no error and no failing test. The
construct fixtures in `test_live_html.py` carry no such block at all, so they agreed
either way. It was found by counting figures in a rendered report.

The figures are drawn from specs now rather than one static image per construct, so the
shape of the check changed: what used to be "n images per construct" is "every selector
option of every per-construct figure produces a panel". The failure it catches is the
same one, and it is still silent — a selector missing an option looks exactly like a
construct that had nothing to show.
"""

from __future__ import annotations

import base64 as _base64
from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("matplotlib")
pytest.importorskip("jinja2")

#: Figures that MUST exist and must offer a construct selector. Membership is otherwise
#: derived from the spec — a figure has a selector iff it has updatemenus — so a new
#: per-construct figure is covered the day it is added rather than the day someone
#: remembers to list it here.
REQUIRED_SELECTOR_FIGURES = {
    "per-position", "read-end", "read-start", "shortfall",
    "read-length-by-construct", "polya-by-construct",
}


def _has_selector(fig) -> bool:
    return bool(fig["spec"]["layout"].get("updatemenus"))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, fixture_bam: Path, curlcake_ref: Path):
    out = tmp_path_factory.mktemp("rep")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    reporter.finalize("run_end", force=True)
    constructs = [name for name, _snap in reporter._construct_snapshots()]
    assert len(constructs) > 1, "fixture must be multi-construct for these to mean anything"
    interactive = reporter._interactive_figures()
    printed = reporter._printed_figures(_base64, interactive)
    return reporter, constructs, interactive, printed


def test_every_per_construct_figure_offers_every_construct(rendered):
    _reporter, constructs, interactive, _printed = rendered
    seen = {f["id"] for f in interactive}
    assert REQUIRED_SELECTOR_FIGURES <= seen, (
        f"missing entirely: {sorted(REQUIRED_SELECTOR_FIGURES - seen)}")
    assert REQUIRED_SELECTOR_FIGURES <= {f["id"] for f in interactive if _has_selector(f)}
    for fig in interactive:
        if not _has_selector(fig):
            continue
        buttons = fig["spec"]["layout"]["updatemenus"][0]["buttons"]
        labels = [b["label"] for b in buttons]
        assert labels == constructs, (
            f"{fig['id']} offers {labels}, the run has {constructs} — a construct that "
            f"silently resolves to nothing leaves the selector looking complete")


def test_the_print_render_turns_every_option_into_its_own_panel(rendered):
    _reporter, constructs, interactive, printed = rendered
    by_id = {f["id"]: f for f in printed}
    assert REQUIRED_SELECTOR_FIGURES <= set(by_id), (
        f"not drawn for paper: {sorted(REQUIRED_SELECTOR_FIGURES - set(by_id))}")
    for fig in interactive:
        if not _has_selector(fig):
            continue
        labels = [p["label"] for p in by_id[fig["id"]]["panels"]]
        assert labels == constructs, f"{fig['id']} printed {labels}"


def test_a_run_level_figure_prints_as_a_single_panel(rendered):
    """Only a figure whose subject changes needs more than one."""
    _reporter, _constructs, interactive, printed = rendered
    by_id = {f["id"]: f for f in printed}
    for fig in interactive:
        if _has_selector(fig):
            continue
        panels = by_id[fig["id"]]["panels"]
        assert len(panels) == 1, (fig["id"], len(panels))
        assert panels[0]["label"] is None


def _data_marks(svg: bytes) -> int:
    """Elements drawn in the plot area. matplotlib clips what it plots to the axes; the
    only other clipped elements are the grid lines, inside the axis groups."""
    import xml.etree.ElementTree as ET

    def count(node, in_axis: bool) -> int:
        in_axis = in_axis or (node.get("id") or "").startswith("matplotlib.axis")
        own = int(not in_axis and node.get("clip-path") is not None)
        return own + sum(count(child, in_axis) for child in node)

    return count(ET.fromstring(svg), False)


def test_no_printed_panel_is_an_empty_frame(rendered):
    """An empty frame reads as a measured absence, not as a figure that failed."""
    _reporter, _constructs, _interactive, printed = rendered
    for fig in printed:
        for panel in fig["panels"]:
            assert panel["url"].startswith("data:image/svg+xml;base64,")
            raw = _base64.b64decode(panel["url"].split(",", 1)[1])
            assert _data_marks(raw) > 0, (fig["id"], panel["label"])


def test_a_printed_panel_names_the_construct_it_shows(rendered):
    """The title moved out of the image, so the panel label is the only thing left
    saying which construct a reader is looking at."""
    _reporter, constructs, _interactive, printed = rendered
    by_id = {f["id"]: f for f in printed}
    for panel in by_id["per-position"]["panels"]:
        assert panel["name"] == panel["label"]
        assert panel["name"] in constructs
        assert panel["detail"], "the measurement detail travels with its own panel"


# --- how the PDF is laid out --------------------------------------------------

def test_the_printed_figures_are_grouped_by_construct(rendered):
    """Paper has no selector. Figure order put all fifteen per-position profiles
    together, then all fifteen consensus plots, so following one construct meant seven
    separate places in a document the reader is holding."""
    reporter, constructs, _interactive, printed = rendered
    sections = reporter._print_sections(printed)
    assert [s["name"] for s in sections["constructs"]] == constructs
    for section in sections["constructs"]:
        ids = [f["id"] for band in section["bands"] for f in band["figures"]]
        assert set(ids) == REQUIRED_SELECTOR_FIGURES | {"consensus"} & set(ids), ids


def test_read_length_and_polya_come_first_in_a_construct_section(rendered):
    """Decided 2026-10-15: they are what a reader checks before asking whether the
    sequence is right, and the error figures mean less without them."""
    reporter, _constructs, _interactive, printed = rendered
    section = reporter._print_sections(printed)["constructs"][0]
    ids = [f["id"] for band in section["bands"] for f in band["figures"]]
    assert ids[:2] == ["read-length-by-construct", "polya-by-construct"]
    assert ids[2:3] == ["per-position"]


def test_run_level_figures_are_not_buried_in_the_first_construct(rendered):
    """They are about the run and not about any construct; under its heading they would
    look like its."""
    reporter, _constructs, _interactive, printed = rendered
    sections = reporter._print_sections(printed)
    # Run-level figures now live in named groups (Whole run, Across constructs, and
    # Sequencing information after the constructs) rather than one undifferentiated
    # block, so the ids come from the groups on either side.
    run_ids = {f["id"]
               for group in sections["before"] + sections["after"]
               for band in group["bands"] for f in band["figures"]}
    assert "read-length" in run_ids and "polya" in run_ids
    for section in sections["constructs"]:
        ids = {f["id"] for band in section["bands"] for f in band["figures"]}
        assert not (ids & run_ids)
    # And "Whole run" is the first thing a reader meets, not the last.
    assert sections["before"][0]["name"] == "Whole run"


def test_only_reference_coordinate_figures_take_the_whole_width(rendered):
    """1,200-1,900 positions are dense at seven inches already. Everything else reads
    perfectly well at 84 mm — including the shortfall heatmap, which is SQUARE, so full
    width makes it full height too: two thirds of a page for no gain."""
    _reporter, _constructs, _interactive, printed = rendered
    wide = {f["id"] for f in printed if f["full_width"]}
    assert wide == {"per-position"}, wide


def test_a_full_width_figure_is_banded_apart_from_the_grid(rendered):
    """Measured on WeasyPrint 69.0: `grid-column: 1 / -1` collapses the track sizing of
    the whole container — 84 mm cells laid out at 0-10 mm. So consecutive figures of the
    same width are banded and nothing ever spans."""
    reporter, _constructs, _interactive, printed = rendered
    section = reporter._print_sections(printed)["constructs"][0]
    for band in section["bands"]:
        widths = {f["full_width"] for f in band["figures"]}
        assert len(widths) == 1, "a band mixes widths, so one of them must span"
    # ...and the bands alternate rather than being one lump.
    assert [b["full_width"] for b in section["bands"]] == [False, True, False]


def test_only_the_whole_run_read_length_opens_between_the_1st_and_99th_percentiles(
        rendered):
    """Read length over the whole run has a peak per construct, so its view runs from
    the 1st to the 99th percentile (2026-11-09); quality keeps the Tukey fences."""
    from mrnaspect.reporting import figspec

    reporter, _constructs, interactive, _printed = rendered
    figs = {f["id"]: f["spec"] for f in interactive}
    rs = reporter._snapshots[reporter.module_names["run_summary"]]
    want = figspec._default_range(rs["read_length_distribution"],
                                  figspec.WINDOW_QUANTILES)
    assert want != figspec._default_range(rs["read_length_distribution"]), (
        "on this fixture the two rules agree, so this test cannot tell them apart")
    assert figs["read-length"]["layout"]["xaxis"]["range"] == list(want)
    fence = figspec._default_range(rs["quality_score_distribution"])
    assert figs["qscore"]["layout"]["xaxis"]["range"] == list(fence)


def test_poly_a_opens_between_the_1st_and_99th_percentiles_run_and_construct(rendered):
    """2026-11-09: the poly(A) views, whole run and per construct, too. The fixture
    has no corrected series, so each view is exactly one histogram's window."""
    from mrnaspect.reporting import figspec

    reporter, _constructs, interactive, _printed = rendered
    figs = {f["id"]: f["spec"] for f in interactive}
    pa = reporter._snapshots[reporter.module_names["polya"]]["polya_distribution"]
    want = figspec._default_range(pa, figspec.WINDOW_QUANTILES)
    assert want != figspec._default_range(pa), "the two rules agree here; proves nothing"
    # One series, drawn as two traces (bars, then the curve through them).
    assert {t["name"] for t in figs["polya"]["data"]} == {"uncorrected"}
    assert figs["polya"]["layout"]["xaxis"]["range"] == list(want)
    shown = dict(reporter._reported_constructs())
    for b in figs["polya-by-construct"]["layout"]["updatemenus"][0]["buttons"]:
        dist = shown[b["label"]]["polya"]["polya_distribution"]
        assert b["args"][1]["xaxis.range"] == list(
            figspec._default_range(dist, figspec.WINDOW_QUANTILES)), b["label"]
