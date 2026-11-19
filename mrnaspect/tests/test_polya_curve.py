"""Poly(A) at 1 nt bins with a fixed-bandwidth curve over them (2026-11-09).

Denser bins and a smoothed curve were asked for; chosen were 1 nt bins and a 2 nt
Gaussian kernel. The settled rules:

* the bandwidth is FIXED in nt and never below a bin, so a live curve does not sharpen as
  reads arrive (an n-adaptive rule such as Silverman's would);
* the bars stay, faint, under the curve, so smoothing cannot hide a second peak;
* Core computes the curve (live snapshot and final figures); a surface only draws it.

And the one that makes this safe to ship two weeks before the preprint: no reported number
depends on the bins or the curve. The quartiles are exact (one count per integer pt), and
so is the short-tail count.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mrnaspect.analysis.modules.polya import PolyAModule
from mrnaspect.analysis.streaming_interface import gaussian_smooth
from mrnaspect.reporting import figrender, figspec
from mrnaspect.reporting.reporter import CoAReporter

from .test_polya_correction_v2 import V1, _read, _ref

PEAK_2NT = 1 / (2.0 * math.sqrt(2 * math.pi))   # a unit spike's peak under SD 2 bins


def _module(pts, config=None, *, arm="U", ref=None) -> PolyAModule:
    m = PolyAModule(config, arm=arm, ref_seq=ref)
    for i, pt in enumerate(pts):
        m.update(_read(pt, V1, name=f"r{i}"))
    return m


# --- the kernel -------------------------------------------------------------------------

def test_a_spike_spreads_into_a_gaussian_of_the_stated_sd_and_keeps_its_reads() -> None:
    counts = [0] * 30
    counts[15] = 1000
    s = gaussian_smooth(counts, 1.0, 2.0)
    assert len(s) == 30
    assert s[15] == pytest.approx(1000 * PEAK_2NT, abs=0.05)
    assert s[13] == s[17] and s[11] == s[19], "symmetric about the spike"
    # One SD out the kernel is exp(-1/2) of its peak.
    assert s[17] / s[15] == pytest.approx(math.exp(-0.5), rel=1e-3)
    assert sum(s) == pytest.approx(1000, abs=0.2)


def test_the_bandwidth_is_in_the_bins_own_unit_not_in_bins() -> None:
    """2 nt over 2 nt bins is 1 bin: the same kernel in nt is narrower in bins."""
    counts = [0] * 20
    counts[10] = 100
    wide, narrow = gaussian_smooth(counts, 1.0, 2.0), gaussian_smooth(counts, 2.0, 2.0)
    assert narrow[10] > wide[10]
    assert narrow[11] / narrow[10] == pytest.approx(math.exp(-0.5), rel=1e-2)


def test_mass_past_the_first_bin_is_dropped_not_folded_back() -> None:
    s = gaussian_smooth([100] + [0] * 20, 1.0, 2.0)
    assert 50 < sum(s) < 60, "about half the kernel lies below the first bin"


@pytest.mark.parametrize("counts", [[], [0, 0, 0]])
def test_nothing_to_smooth_is_zeros_of_the_same_length(counts) -> None:
    assert gaussian_smooth(counts, 1.0, 2.0) == [0.0] * len(counts)


@pytest.mark.parametrize("width, bw", [(1.0, 0.0), (1.0, -2.0), (0.0, 2.0),
                                       (1.0, float("nan")), (1.0, float("inf"))])
def test_a_bandwidth_or_width_that_is_not_a_positive_number_raises(width, bw) -> None:
    with pytest.raises(ValueError, match="positive number"):
        gaussian_smooth([1, 2, 3], width, bw)


@pytest.mark.parametrize("counts", [[1, -1, 2], [1, float("nan"), 2]])
def test_a_negative_or_missing_count_raises_rather_than_drawing(counts) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        gaussian_smooth(counts, 1.0, 2.0)


# --- the module -------------------------------------------------------------------------

def test_the_distribution_carries_its_curve_beside_its_1_nt_bins() -> None:
    pts = np.random.default_rng(5).integers(60, 160, size=500)
    dist = _module(pts).snapshot()["polya_distribution"]
    assert (dist["bin_min"], dist["bin_width"], dist["n_bins"]) == (-0.5, 1.0, 500)
    # Centred on the integers: pt v lands in bin v, so bin i is the reads with pt == i.
    assert dist["bin_counts"][100] == int((pts == 100).sum())
    assert dist["smoothing"] == {"kernel": "gaussian", "bandwidth_nt": 2.0}
    assert dist["smoothed_counts"] == gaussian_smooth(dist["bin_counts"], 1.0, 2.0)


def test_no_reported_number_depends_on_the_bins_or_the_curve() -> None:
    """The old 5 nt bins with a 5 nt kernel, and no curve at all, report what the 1 nt
    default reports: quartiles, mean, under/overflow and the short-tail rate."""
    pts = list(np.random.default_rng(9).integers(1, 300, size=700)) + [500, 512, 640]
    configs = [None,
               {"histogram": {"bin_min": 0.0, "bin_max": 500.0, "n_bins": 100},
                "smoothing_bandwidth_nt": 5.0},
               {"smoothing_bandwidth_nt": None}]
    snaps = [_module(pts, dict(cfg or {}, short_threshold=80)).snapshot()
             for cfg in configs]

    def reported(s):
        d = s["polya_distribution"]
        return ({k: d[k] for k in ("n", "Q1", "median", "Q3", "underflow", "overflow")},
                s["polya_length"], s["short_tail"])

    first = reported(snaps[0])
    assert first[0]["overflow"] == 3, "pt >= 500 overflows, as under the 5 nt bins"
    assert all(reported(s) == first for s in snaps[1:])


def test_the_corrected_curve_has_the_same_bandwidth_on_its_own_axis() -> None:
    """Corrected bins are F nt wide, so 2 nt there is 2/F bins: one spike's peak height
    in reads per corrected bin is n * F / (2 * sqrt(2 pi))."""
    ref = _ref(1183, 0.24598478444632291, 3)
    snap = _module([120] * 400, arm="m1psi", ref=ref).snapshot()
    assert snap["correction"]["applied"]
    F = snap["correction"]["factor"]
    corr = snap["polya_distribution_corrected"]
    assert corr["smoothing"] == {"kernel": "gaussian", "bandwidth_nt": 2.0}
    assert len(corr["smoothed_counts"]) == len(corr["bin_counts"])
    assert max(corr["smoothed_counts"]) == pytest.approx(400 * F * PEAK_2NT, rel=0.01)
    raw = snap["polya_distribution"]["smoothed_counts"]
    assert max(raw) == pytest.approx(400 * PEAK_2NT, rel=0.01)


def test_a_withheld_correction_has_an_empty_curve_too() -> None:
    corr = _module([100] * 50, arm=None).snapshot()["polya_distribution_corrected"]
    assert corr["n"] == 0
    assert corr["smoothed_counts"] == [0.0] * len(corr["bin_counts"])


def test_no_curve_when_switched_off() -> None:
    snap = _module([100] * 50, {"smoothing_bandwidth_nt": None}).snapshot()
    for key in ("polya_distribution", "polya_distribution_corrected"):
        assert "smoothed_counts" not in snap[key] and "smoothing" not in snap[key]


@pytest.mark.parametrize("histogram", [
    {"n_bins": 400},                                   # 1.25 nt
    {"bin_min": 0.0, "bin_max": 500.0, "n_bins": 1000},  # 0.5 nt
    {"bin_min": 0.0, "bin_max": 500.0, "n_bins": 200},   # 2.5 nt
])
def test_bins_that_are_not_a_whole_number_of_nt_are_refused(histogram) -> None:
    """pt is an integer: such bins hold unequal numbers of possible values (a comb)."""
    with pytest.raises(ValueError, match="whole number of nt"):
        PolyAModule({"histogram": histogram})


@pytest.mark.parametrize("bandwidth", [0.5, 0.0, -1, True, "two", float("nan")])
def test_a_kernel_narrower_than_a_bin_or_not_a_number_is_refused(bandwidth) -> None:
    with pytest.raises(ValueError, match="at least the bin width"):
        PolyAModule({"smoothing_bandwidth_nt": bandwidth})


def test_the_floor_is_the_configured_bin_not_one_nt() -> None:
    two_nt = {"histogram": {"bin_min": -0.5, "bin_max": 499.5, "n_bins": 250}}
    assert PolyAModule({**two_nt, "smoothing_bandwidth_nt": 2})._smoothing_nt == 2.0
    with pytest.raises(ValueError, match=r"bin width \(2 nt\)"):
        PolyAModule({**two_nt, "smoothing_bandwidth_nt": 1.5})


# --- the figures --------------------------------------------------------------------------

def _dist(centre: int, n: int = 200, *, smoothed: bool = True) -> dict:
    counts = [0] * 500
    counts[centre] = n
    d = {"n": n, "bin_min": -0.5, "bin_max": 499.5, "bin_width": 1.0, "n_bins": 500,
         "bin_counts": counts, "Q1": centre, "median": centre, "Q3": centre,
         "underflow": 0, "overflow": 0}
    if smoothed:
        d["smoothing"] = {"kernel": "gaussian", "bandwidth_nt": 2.0}
        d["smoothed_counts"] = gaussian_smooth(counts, 1.0, 2.0)
    return d


def test_a_series_with_a_curve_is_faint_bars_under_a_line() -> None:
    d = _dist(120)
    bars, curve = figspec.histogram_spec([("uncorrected", d, figspec.CATEGORICAL[0])],
                                         x_title="nt")["data"]
    assert bars["fill"] == "tozeroy" and bars["line"]["width"] == 0
    assert bars["showlegend"] is False and "hoverinfo" not in bars, "the bars hover"
    assert "fill" not in curve and curve["line"]["width"] == figspec.SERIES_LINE_PX
    assert curve["hoverinfo"] == "skip"
    assert bars["legendgroup"] == curve["legendgroup"] == "uncorrected"
    assert curve["y"] == d["smoothed_counts"]
    assert curve["x"][120] == 120.0, "drawn at bin centres, on the integers"


def test_a_series_without_a_curve_is_drawn_as_before() -> None:
    """An older snapshot (5 nt, no curve) still renders, e.g. through a resume."""
    old = {"n": 3, "bin_min": 0.0, "bin_max": 500.0, "bin_width": 5.0, "n_bins": 100,
           "bin_counts": [0] * 20 + [3] + [0] * 79, "Q1": 100, "median": 101, "Q3": 102}
    (trace,) = figspec.histogram_spec([("a", old, "#396cd8")], x_title="nt")["data"]
    assert trace["line"]["width"] == figspec.SERIES_LINE_PX and "legendgroup" not in trace


def test_a_curve_that_does_not_match_its_bins_raises() -> None:
    d = _dist(120)
    d["smoothed_counts"] = d["smoothed_counts"][:-1]
    with pytest.raises(ValueError, match="smoothed_counts"):
        figspec.histogram_spec([("a", d, "#396cd8")], x_title="nt")


def _construct_spec() -> dict:
    return figspec.per_construct_histogram_spec(
        [("A", _dist(110)), ("B", _dist(100))], x_title="poly(A) length (nt)",
        corrected={"A": _dist(118)})


def test_the_switch_keeps_its_format_with_one_entry_per_trace() -> None:
    """The GUI re-presents this menu from Core's own args: the format is unchanged
    (per-trace opacity, hoverinfo and showlegend lists); there are two traces a series."""
    spec = _construct_spec()
    layout, data = spec["layout"], spec["data"]
    assert [t["name"] for t in data] == ["uncorrected"] * 2 + ["corrected"] * 2 + ["uncorrected"] * 2
    switch = {b["label"]: b["args"][0] for b in layout["updatemenus"][1]["buttons"]}
    assert all(set(args) == {"opacity", "hoverinfo", "showlegend"} for args in switch.values())
    assert all(len(v) == len(data) for args in switch.values() for v in args.values())
    assert switch["Both"] == {
        "opacity": [1] * 6,
        "hoverinfo": ["all", "skip"] * 3,
        "showlegend": [False, True] * 3,
    }
    assert switch["Corrected"]["opacity"] == [0, 0, 1, 1, 0, 0]
    assert switch["Uncorrected"]["opacity"] == [1, 1, 0, 0, 1, 1]
    assert switch["Corrected"]["hoverinfo"] == ["skip", "skip", "all", "skip", "skip", "skip"]
    # The construct selector shows a construct's bars and curves together.
    selector = [b["args"][0]["visible"] for b in layout["updatemenus"][0]["buttons"]]
    assert selector == [[True] * 4 + [False] * 2, [False] * 4 + [True] * 2]


def test_in_print_the_legend_still_tells_the_two_series_apart() -> None:
    """The fill is on the bars and the line on the curve, two traces; the PDF pairs them
    by legend group, or grayscale print would show two identical ink lines."""
    import matplotlib.pyplot as plt

    _label, traces, layout = figrender._panels(_construct_spec())[0]
    fig, ax = figrender.draw_panel(traces, layout, theme=figrender.GRAYSCALE)
    try:
        legend = ax.get_legend()
        assert [t.get_text() for t in legend.get_texts()] == ["uncorrected", "corrected"]
        hatches = [getattr(h, "get_hatch", lambda: None)() for h in legend.legend_handles]
        assert hatches[0] != hatches[1], hatches
    finally:
        plt.close(fig)


def test_the_pooled_corrected_series_is_redrawn_with_the_as_called_kernel() -> None:
    track = ("A", {"polya": {"correction": {"applied": True},
                             "polya_distribution_corrected": _dist(130, n=50)}})
    pooled, _k = CoAReporter._pooled_corrected([track], onto=_dist(0))
    assert pooled["smoothing"] == {"kernel": "gaussian", "bandwidth_nt": 2.0}
    assert pooled["smoothed_counts"] == gaussian_smooth(pooled["bin_counts"], 1.0, 2.0)
    # Onto a grid with no curve (an older snapshot), the pool draws none either.
    plain, _ = CoAReporter._pooled_corrected([track], onto=_dist(0, smoothed=False))
    assert "smoothed_counts" not in plain
