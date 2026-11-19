"""Poly(A), corrected: a run-level series, and both series for every construct.

Decided 2026-10-28. The whole-run figure drew a "model-corrected" series that
was always empty -- the correction is per construct, so the run-level module never applies
one -- and every construct's corrected distribution existed and was drawn nowhere; only
its median reached the table. Now:

* the run-level figure sums the constructs' corrected histograms, and says that this is
  not the set of reads the as-called series is over. No two constructs share corrected
  bins -- each is stretched by its own correction -- so each is first spread onto the
  as-called bins in proportion to overlap;
* each construct draws both, with a screen-only switch -- both / corrected / as called --
  and print draws both, one image per construct;
* "model-corrected" is "corrected".
"""

from __future__ import annotations

import pytest

from mrnaspect.reporting import figrender, figspec
from mrnaspect.reporting.reporter import CoAReporter


def _dist(centre: float, n: int = 100, n_bins: int = 100, width: float = 5.0) -> dict:
    counts = [0] * n_bins
    counts[int(centre / width)] = n
    return {"n": n, "bin_min": 0.0, "bin_max": width * n_bins, "bin_width": width,
            "n_bins": n_bins, "bin_counts": counts, "Q1": centre - 5, "median": centre,
            "Q3": centre + 5, "underflow": 0, "overflow": 0}


def _spec() -> dict:
    return figspec.per_construct_histogram_spec(
        [("A", _dist(110)), ("B", _dist(100))], x_title="poly(A) length (nt)",
        corrected={"A": _dist(95)})


# --- per construct ---------------------------------------------------------------------

def test_a_corrected_construct_draws_both_series() -> None:
    spec = _spec()
    assert [t["name"] for t in spec["data"]] == ["uncorrected", "corrected", "uncorrected"]
    first = spec["layout"]["updatemenus"][0]["buttons"][0]["args"][1]
    subtitle = str(first["title"])
    assert "uncorrected:" in subtitle and " corrected:" in subtitle, subtitle
    assert not first["shapes"], "a marker would describe a series the switch can hide"


def test_the_switch_shows_either_or_both() -> None:
    menus = _spec()["layout"]["updatemenus"]
    assert len(menus) == 2
    switch = {b["label"]: b["args"][0]["opacity"] for b in menus[1]["buttons"]}
    assert switch == {"Both": [1, 1, 1], "Corrected": [0, 1, 0], "Uncorrected": [1, 0, 1]}


def test_the_switch_and_the_construct_selector_touch_different_properties() -> None:
    """Each control owns a property, so neither undoes the other: the selector sets
    `visible`, the switch opacity. One `visible` each would reset the other's choice."""
    construct_menu, switch = _spec()["layout"]["updatemenus"]
    assert all(set(b["args"][0]) == {"visible"} for b in construct_menu["buttons"])
    assert all("visible" not in b["args"][0] for b in switch["buttons"])


def test_print_draws_both_once_per_construct() -> None:
    """Print expands the FIRST menu only, so the switch adds no images."""
    panels = figrender._panels(_spec())
    assert [label for label, _d, _l in panels] == ["A", "B"]
    a_traces = [t for t in panels[0][1] if t.get("visible")]
    assert [t["name"] for t in a_traces] == ["uncorrected", "corrected"]
    assert all(t.get("opacity", 1) == 1 for t in a_traces)


def test_in_print_the_legend_tells_the_two_apart() -> None:
    """Both lines print in ink; the fills differ by grey and hatch. A legend of lines alone
    showed two identical entries -- found rendering the declared fixture, 2026-10-28."""
    import matplotlib.pyplot as plt

    _label, traces, layout = figrender._panels(_spec())[0]
    fig, ax = figrender.draw_panel(traces, layout, theme=figrender.GRAYSCALE)
    try:
        legend = ax.get_legend()
        assert [t.get_text() for t in legend.get_texts()] == ["uncorrected", "corrected"]
        hatches = [getattr(h, "get_hatch", lambda: None)() for h in legend.legend_handles]
        assert hatches[0] != hatches[1], hatches
    finally:
        plt.close(fig)


def test_without_a_correction_the_figure_is_as_it_was() -> None:
    spec = figspec.per_construct_histogram_spec(
        [("A", _dist(110)), ("B", _dist(100))], x_title="poly(A) length (nt)")
    assert len(spec["layout"]["updatemenus"]) == 1
    assert [t["name"] for t in spec["data"]] == ["A", "B"]
    assert spec["layout"]["updatemenus"][0]["buttons"][0]["args"][1]["shapes"]


# --- the run level ------------------------------------------------------------------------

def _track(name: str, applied: bool, dist: dict | None) -> tuple:
    return (name, {"polya": {"correction": {"applied": applied},
                             "polya_distribution_corrected": dist or {"n": 0}}})


GRID = _dist(0)          # the as-called bins: [0, 500), 5 nt


def test_the_run_level_series_pools_only_constructs_whose_correction_applied() -> None:
    pooled, k = CoAReporter._pooled_corrected([
        _track("A", True, _dist(95, n=10)),
        _track("B", False, _dist(90, n=99)),      # withheld: not in it
        _track("C", True, None),                  # no tails: not in it
        _track("D", True, _dist(95, n=5)),
    ], onto=GRID)
    assert (k, pooled["n"]) == (2, 15)
    assert pooled["bin_counts"][19] == 15, "same bins as the grid: nothing moves"
    assert sum(pooled["bin_counts"]) == 15
    assert pooled["median"] is None, "no quantiles read off pooled bins"


def test_nothing_to_pool_is_none() -> None:
    assert CoAReporter._pooled_corrected([_track("A", False, _dist(95))], onto=GRID) is None


def _stretched(counts: dict[int, float], lo: float, width: float) -> dict:
    """A corrected histogram as the module writes it: raw counts on mapped edges."""
    d = _dist(0, n=0)
    d.update(bin_min=lo, bin_width=width, bin_max=round(lo + 100 * width, 2),
             bin_counts=[counts.get(i, 0) for i in range(100)], n=sum(counts.values()))
    return d


def test_a_stretched_bin_is_shared_by_overlap() -> None:
    """[95, 100.67) on 5 nt bins: 5 of its 5.67 nt are in [95, 100), 0.67 in [100, 105)."""
    out, below, above = CoAReporter._spread_onto(_stretched({0: 10}, 95.0, 5.67), 0.0, 5.0,
                                                 100)
    assert out[19] == pytest.approx(10 * 5 / 5.67)
    assert out[20] == pytest.approx(10 * 0.67 / 5.67)
    assert sum(out) == pytest.approx(10) and below == above == 0


def test_what_falls_off_the_grid_is_counted_off_it() -> None:
    """QK-09's first corrected bin on the test BAM starts at -2.72 nt."""
    d = _stretched({0: 6, 87: 3}, -2.72, 5.6706)   # bin 87 = [490.62, 496.29)... and 88
    d["bin_counts"][88] = 4                          # = [496.29, 501.96): 1.96 nt above 500
    out, below, above = CoAReporter._spread_onto(d, 0.0, 5.0, 100)
    assert below == pytest.approx(6 * 2.72 / 5.6706)
    assert above == pytest.approx(4 * (-2.72 + 89 * 5.6706 - 500) / 5.6706)
    assert sum(out) + below + above == pytest.approx(13)


def test_two_stretches_pool_onto_one_grid_and_keep_every_read() -> None:
    a = _stretched({17: 10}, -2.72, 5.6706)
    b = _stretched({19: 7}, -0.01, 4.9965)
    pooled, k = CoAReporter._pooled_corrected(
        [("A", {"polya": {"correction": {"applied": True}, "polya_distribution_corrected": a}}),
         ("B", {"polya": {"correction": {"applied": True}, "polya_distribution_corrected": b}})],
        onto=GRID)
    assert k == 2 and pooled["bin_width"] == 5 and pooled["n_bins"] == 100
    assert sum(pooled["bin_counts"]) + pooled["underflow"] + pooled["overflow"] == (
        pytest.approx(17))
    # A's bin 17 is [93.68, 99.35) and B's bin 19 [94.92, 99.92): both straddle 95.
    a_lo, b_lo = -2.72 + 17 * 5.6706, -0.01 + 19 * 4.9965
    assert pooled["bin_counts"][18] == pytest.approx(
        10 * (95 - a_lo) / 5.6706 + 7 * (95 - b_lo) / 4.9965)
    assert pooled["bin_counts"][19] == pytest.approx(
        10 * (a_lo + 5.6706 - 95) / 5.6706 + 7 * (b_lo + 4.9965 - 95) / 4.9965)


def test_fractional_counts_hover_with_one_decimal() -> None:
    whole = _dist(95)
    spread = dict(whole, bin_counts=[c / 3 for c in whole["bin_counts"]])
    spec = figspec.histogram_spec([("uncorrected", whole, "#000"), ("corrected", spread, "#111")],
                                  x_title="nt")
    assert [t["hovertemplate"] for t in spec["data"]] == [
        "uncorrected: %{y} reads<extra></extra>", "corrected: %{y:.1f} reads<extra></extra>"]
