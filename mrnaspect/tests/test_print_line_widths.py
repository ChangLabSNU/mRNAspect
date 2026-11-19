"""Print line widths: a spec width is CSS px, converted to points as font sizes are.

2026-11-05: the PDF's graph outlines should be 1.0 pt. figrender drew a spec width
as that many points, so a series outline (SERIES_LINE_PX = 1.5) printed at 1.5 pt, while
font sizes were already divided by SPEC_UNITS_PER_PT. Dividing widths the same way puts
the outlines at exactly 1 pt and keeps reference lines (width 1 in the spec) thinner than
the data they mark.
"""

from __future__ import annotations

import pytest

from mrnaspect.reporting import figrender as fr
from mrnaspect.reporting import figspec

OUTLINE_PT = 1.0


def _close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


def _outlined_bar(x):
    colour = figspec.CATEGORICAL[0]
    return {"type": "bar", "x": list(x), "y": [1, 2, 3][:len(x)],
            "marker": {"color": figspec.fill_of(colour),
                       "line": {"color": colour, "width": figspec.SERIES_LINE_PX}}}


def test_the_spec_series_width_converts_to_one_point():
    """The figspec value the printed outline rests on."""
    assert figspec.SERIES_LINE_PX / figspec.SPEC_UNITS_PER_PT == pytest.approx(OUTLINE_PT)


def test_a_step_histogram_outline_prints_at_one_point():
    colour = figspec.CATEGORICAL[0]
    tr = {"type": "scatter", "mode": "lines", "x": [0, 1, 2], "y": [1, 3, 2],
          "line": {"shape": "hv", "width": figspec.SERIES_LINE_PX, "color": colour},
          "fill": "tozeroy", "fillcolor": figspec.fill_of(colour)}
    fig, ax = fr.draw_panel([tr], {}, theme=fr.GRAYSCALE)
    assert ax.lines[0].get_linewidth() == pytest.approx(OUTLINE_PT)
    _close(fig)


@pytest.mark.parametrize("x", [("a", "b"), (1, 2, 3)], ids=["named", "numeric"])
def test_a_bar_outline_prints_at_one_point(x):
    """Both bar paths: named categories (ax.bar) and numeric x (one PolyCollection, the
    MAPQ figure's)."""
    fig, ax = fr.draw_panel([_outlined_bar(x)], {}, theme=fr.GRAYSCALE)
    widths = ([p.get_linewidth() for p in ax.patches] if ax.patches
              else list(ax.collections[0].get_linewidths()))
    assert widths and all(w == pytest.approx(OUTLINE_PT) for w in widths)
    _close(fig)


def test_a_reference_line_prints_thinner_than_the_outline():
    """Quartile and threshold lines are width 1 in the spec: 0.67 pt, under the data."""
    fig, ax = fr.draw_panel([], {"shapes": [
        {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 5, "y1": 5,
         "line": {"width": 1, "dash": "dot"}}]})
    width = ax.lines[0].get_linewidth()
    assert width == pytest.approx(1 / figspec.SPEC_UNITS_PER_PT)
    assert width < OUTLINE_PT
    _close(fig)


def test_a_line_without_a_width_prints_as_a_series_outline():
    fig, ax = fr.draw_panel([{"type": "scatter", "mode": "lines", "x": [0, 1],
                              "y": [1, 2]}], {})
    assert ax.lines[0].get_linewidth() == pytest.approx(OUTLINE_PT)
    _close(fig)
