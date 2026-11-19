"""The per-position figure must leave a HOLE where coverage is absent.

An uncovered reference position carries null, not 0.0 (item 5). A figure that draws it
as zero-height — or fills straight across it — says "measured, error-free" about a
position nothing was measured at. This is a rendering property, so it is checked on
rendered PIXELS: every cheaper check (does it call fill_between? is `where` passed?)
tests the implementation rather than the claim.

The guard exists because the fast way to draw this figure has a wrong version that looks
right. Replacing per-position bars with one step fill is ~29x faster (933 ms of `ax.bar`
alone, on a 2,245-position construct, against 49 ms for the whole figure) — but the
natural way to write it, keeping only the covered points and filling those, draws
straight across every hole. Measured on the series below: 34,320 coloured pixels where
there should be none, and nothing about the output looks wrong. Nothing else in the
suite would notice.

Since 2026-11-04 the per-position figure is bars only: the consensus curve, which is
where a bridge could be drawn, went into it as the stacked substitution + deletion bars.
Bars cannot bridge — a dropped position and a zero-height one both ink nothing — so what
is left to catch is the opposite failure, ink where there is no measurement, and a
profile drawn with holes where there are none.
"""

from __future__ import annotations

import io

import pytest

from mrnaspect.reporting import figrender, figspec

#: Pixels trimmed from each edge of the axes box, to leave the spines outside.
_SPINE_PAD = 3

np = pytest.importorskip("numpy")
pytest.importorskip("matplotlib")


def _crop_to_axes(spec) -> "np.ndarray":
    """The real figure path — spec, then renderer — decoded back to the PLOT's pixels.

    Both halves, because either can fill a hole: the spec can drop the nulls instead of
    carrying them, and the renderer can turn them into zeros. The second is the one that
    bit: `None` is not a float, so the obvious conversion raises and the obvious fix for
    THAT is 0.0, which draws straight across.

    Cropped to the axes. The legend sits OUTSIDE the plot and its swatch is the same blue
    as the series, so an uncropped image puts ink to the right of the data: the inked
    span grows by the gutter and the legend, and a correct figure measures a hole of 29.8%
    where the series says 33%. This is the second time the same shape of mistake has been
    made in this file — the first was mapping positions to columns by proportion of IMAGE
    width — and the lesson is the same one: the picture is bigger than the plot.
    """
    import matplotlib.pyplot as plt
    _label, traces, layout = figrender._panels(spec)[0]
    fig, ax = figrender.draw_panel(traces, layout)
    try:
        buf = io.BytesIO()
        # No bbox_inches="tight": it re-crops the canvas, which would move the axes out
        # from under the box we are about to measure.
        fig.savefig(buf, format="png", dpi=figrender.PANEL_DPI)
        box = ax.get_window_extent()
        img = plt.imread(io.BytesIO(buf.getvalue()))
        # INSIDE the axes, by a few pixels on every side. The bottom spine runs the full
        # width in the same chrome grey as the curve, so including it inks every column
        # and the largest gap is 0 no matter what the data says — a measurement that
        # cannot fail. Colour cannot separate them; geometry can.
        pad = _SPINE_PAD
        top = img.shape[0] - int(box.y1)
        return img[max(top + pad, 0):img.shape[0] - int(box.y0) - pad,
                   int(box.x0) + pad:int(box.x1) - pad]
    finally:
        plt.close(fig)


def _profile(series: list[float | None]) -> "np.ndarray":
    """The upper panel of the per-position figure over one series of substitution rates.

    No consensus snapshot: no threshold line to chop the empty region into dashes, and
    no flag markers. The lower panel is outside the crop.
    """
    blank = [None if v is None else 0.0 for v in series]
    return _crop_to_axes(figspec.per_position_spec([("A", {
        "substitution_rate": series, "deletion_rate": blank, "insertion_rate": blank},
        None)]))


def _is_series_hue(img):
    """The first categorical blue, the substitution bars — excludes the grey chrome."""
    return (img[..., 2] > 0.5) & (img[..., 0] < 0.5)


def _largest_hole_fraction(img: "np.ndarray", mask=_is_series_hue) -> float:
    """Longest run of empty columns INSIDE the inked span, as a fraction of it.

    Measured relative to the inked span, not to the image: the axes box does not fill
    the PNG (measured 103..656 of 700 px with the current figsize and labels), so
    mapping a reference position to a pixel column by proportion of image width is
    wrong — an earlier version of this test did exactly that and failed against a
    correct figure.
    """
    cols = mask(img).sum(axis=0)
    ink = np.nonzero(cols)[0]
    assert ink.size, "nothing was plotted at all"
    span = cols[ink.min():ink.max() + 1]
    best = run = 0
    for empty in span == 0:
        run = run + 1 if empty else 0
        best = max(best, run)
    return best / len(span)


def test_the_profile_inks_nothing_where_coverage_is_absent() -> None:
    n, lo, hi = 300, 100, 200
    series: list[float | None] = [0.2] * n
    for i in range(lo, hi):
        series[i] = None
    assert _largest_hole_fraction(_profile(series)) == pytest.approx(1 / 3, abs=0.04)


def test_a_fully_covered_profile_has_no_holes() -> None:
    """The converse, so the first test cannot pass by drawing nothing at all."""
    hole = _largest_hole_fraction(_profile([0.2] * 300))
    assert hole < 0.02, f"a fully covered series left a {hole:.1%} gap"


def test_an_all_null_series_still_renders() -> None:
    """A construct with no coverage yet gets an empty-axes placeholder, not a crash —
    an interim is rendered after the first POD5, when this is the normal case."""
    assert _profile([None] * 50).size > 0
