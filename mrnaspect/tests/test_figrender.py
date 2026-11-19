"""The spec renderer: one implementation of each chart, two media.

Every guard here is written against an ARTIST rather than a pixel. A PNG assertion cannot
tell a missing series from a faint one — which is exactly how a first look at the output
passed a shortfall heatmap whose 64 iso-length contour lines had all been dropped, under a
caption that still said "dotted lines are iso-length contours".
"""

from __future__ import annotations

import math

import pytest

from mrnaspect.reporting import figrender as fr
from mrnaspect.reporting import figspec

from .test_construct_figures import _data_marks


def _close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


# --- the dropdown becomes one figure per option ------------------------------

def test_a_spec_without_a_selector_is_one_panel():
    spec = {"data": [{"type": "scatter", "x": [1, 2], "y": [1, 2]}], "layout": {}}
    assert fr.panel_count(spec) == 1
    assert fr.render_spec(spec)[0].label is None


def test_each_dropdown_option_becomes_its_own_panel_with_that_option_visible():
    """Paper has no selector, so every option has to be its own figure."""
    spec = {
        "data": [{"type": "scatter", "x": [1], "y": [1], "name": "a", "visible": True},
                 {"type": "scatter", "x": [1], "y": [2], "name": "b", "visible": False}],
        "layout": {"updatemenus": [{"buttons": [
            {"label": "A", "method": "update",
             "args": [{"visible": [True, False]}, {"title": {"text": "A"}}]},
            {"label": "B", "method": "update",
             "args": [{"visible": [False, True]}, {"title": {"text": "B"}}]},
        ]}]},
    }
    panels = fr._panels(spec)
    assert [p[0] for p in panels] == ["A", "B"]
    assert [t["visible"] for t in panels[0][1]] == [True, False]
    assert [t["visible"] for t in panels[1][1]] == [False, True]
    # The layout patch travels with its own panel, not with whichever loaded first.
    assert panels[1][2]["title"] == {"text": "B"}
    # And the selector itself must not survive into the drawn layout.
    assert "updatemenus" not in panels[0][2]


def test_a_dotted_restyle_key_reaches_the_nested_field():
    spec = {"data": [{"type": "scatter", "x": [1], "y": [1], "marker": {"size": 6}}],
            "layout": {"updatemenus": [{"buttons": [
                {"label": "X", "args": [{"marker.color": ["#ff0000"]}, {}]}]}]}}
    _, traces, _ = fr._panels(spec)[0]
    assert traces[0]["marker"] == {"size": 6, "color": "#ff0000"}


def test_the_patch_does_not_leak_into_the_other_panels():
    """Each panel starts from the ORIGINAL data, or option 3 inherits option 2's state."""
    spec = {"data": [{"type": "scatter", "x": [1], "y": [1]}],
            "layout": {"updatemenus": [{"buttons": [
                {"label": "A", "args": [{"marker.color": ["#111111"]}, {}]},
                {"label": "B", "args": [{}, {}]}]}]}}
    panels = fr._panels(spec)
    assert panels[0][1][0].get("marker", {}).get("color") == "#111111"
    assert "color" not in panels[1][1][0].get("marker", {})


# --- titles come back as text, not baked into the image ----------------------

@pytest.mark.parametrize("markup, expected", [
    ('QK-09<br><span style="font-size:11px">1200 positions</span>', ("QK-09", "1200 positions")),
    ('<span style="color:#898781">Read end reason</span>', ("", "Read end reason")),
    ("Plain name", ("Plain name", "")),
    ("", ("", "")),
])
def test_the_title_is_parsed_back_into_name_and_detail(markup, expected):
    """A title baked into a PNG cannot be selected, searched or restyled.

    figspec writes plotly markup; this turns it back into text the caller sets beside the
    image. A figure with no selector carries ONLY the detail — the HTML caption already
    names it — so that case must not be read as a name.
    """
    assert fr._text({"text": markup}) == expected


def test_the_detail_travels_with_the_panel_it_describes():
    spec = {"data": [{"type": "scatter", "x": [1], "y": [1]}],
            "layout": {"title": {"text": "first"}, "updatemenus": [{"buttons": [
                {"label": "A", "args": [{}, {"title": {"text": "A<br><i>n=1</i>"}}]},
                {"label": "B", "args": [{}, {"title": {"text": "B<br><i>n=2</i>"}}]}]}]}}
    panels = fr.render_spec(spec)
    assert [(p.name, p.detail) for p in panels] == [("A", "n=1"), ("B", "n=2")]


# --- the gaps -----------------------------------------------------------------

def test_a_null_becomes_nan_and_not_a_bridge():
    """``None`` is "we never measured here", and 60 of a real run's 165 traces carry one.

    Interpolating across it does not crash and does not look wrong — it silently turns an
    uncovered reference position into a straight line between two real measurements.
    """
    out = fr._nums([1.0, None, 3.0])
    assert math.isnan(out[1])
    assert list(out[[0, 2]]) == [1.0, 3.0]


def test_a_gap_is_not_drawn_through():
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [0, 1, 2], "y": [1, None, 1]}], {})
    ys = ax.lines[0].get_ydata()
    assert math.isnan(ys[1]), "the gap must survive into the artist"
    _close(fig)


# --- the traps that cost a redraw --------------------------------------------

def test_a_log_range_is_read_as_decades_not_as_literal_limits():
    """plotly states a log axis range in log10 units.

    Passing [0, 3] through unchanged sets the limits of a decade axis to 0..3 — a window
    three units wide on a scale that runs to a thousand, i.e. an almost empty plot.
    """
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [1, 2], "y": [1, 500]}],
        {"yaxis": {"type": "log", "range": [0, 3], "dtick": 1}})
    assert ax.get_yscale() == "log"
    assert ax.get_ylim() == pytest.approx((1.0, 1000.0))
    _close(fig)


def test_a_diagonal_shape_is_drawn():
    """The iso-length contours: anchored to neither axis's paper frame.

    An earlier version handled only the two paper cases and dropped these silently, 64 per
    run, under a caption that went on describing them.
    """
    fig, ax = fr.draw_panel([], {"shapes": [
        {"type": "line", "x0": 0, "x1": 100, "y0": 100, "y1": 0,
         "line": {"dash": "dot", "width": 1}}]})
    assert len(ax.lines) == 1, (
        "the contour is missing. It fails SILENTLY — the plot still looks like a plot, "
        "under a caption that goes on describing lines that are not there.")
    assert list(ax.lines[0].get_xdata()) == [0.0, 100.0]
    assert list(ax.lines[0].get_ydata()) == [100.0, 0.0]
    _close(fig)


@pytest.mark.parametrize("shape, attr", [
    ({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 5, "y1": 5}, "y"),
    ({"type": "line", "yref": "paper", "y0": 0, "y1": 1, "x0": 7, "x1": 7}, "x"),
])
def test_a_paper_anchored_shape_spans_the_plot(shape, attr):
    fig, ax = fr.draw_panel([], {"shapes": [shape]})
    assert len(ax.lines) == 1
    data = (ax.lines[0].get_ydata() if attr == "y" else ax.lines[0].get_xdata())
    assert len(set(data)) == 1, "a spanning line is constant on the other axis"
    _close(fig)


def test_a_half_anchored_sloping_shape_is_refused_rather_than_misplaced():
    with pytest.raises(ValueError, match="anchored to paper"):
        fr.draw_panel([], {"shapes": [
            {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 1, "y1": 9}]})


def test_an_unset_showlegend_still_draws_a_legend():
    """plotly's default is ON. Reading unset as False strips the legend from exactly the
    charts whose categories are encoded in colour."""
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [1], "y": [1], "name": "mismatch"},
         {"type": "scatter", "mode": "lines", "x": [1], "y": [2], "name": "deletion"}], {})
    assert ax.get_legend() is not None
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["mismatch", "deletion"]
    _close(fig)


def test_the_legend_sits_outside_the_plot():
    """Inside, a swatch can land on a fill of its own colour and vanish."""
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [1], "y": [1], "name": "a"},
         {"type": "scatter", "mode": "lines", "x": [1], "y": [2], "name": "b"}], {})
    assert ax.get_legend().get_bbox_to_anchor().x0 > ax.get_window_extent().x1
    _close(fig)


def test_showlegend_false_is_honoured():
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [1], "y": [1], "name": "a"},
         {"type": "scatter", "mode": "lines", "x": [1], "y": [2], "name": "b"}],
        {"showlegend": False})
    assert ax.get_legend() is None
    _close(fig)


def test_a_stacked_band_sits_on_the_one_below_it():
    """Filling each band to zero paints them over one another: the chart would read as the
    largest series alone and every smaller one would be invisible."""
    fig, ax = fr.draw_panel([
        {"type": "scatter", "mode": "lines", "x": [0, 1], "y": [0.6, 0.6],
         "stackgroup": "one", "fillcolor": "#396cd8", "line": {"width": 0}, "name": "a"},
        {"type": "scatter", "mode": "lines", "x": [0, 1], "y": [0.4, 0.4],
         "stackgroup": "one", "fillcolor": "#519b30", "line": {"width": 0}, "name": "b"},
    ], {})
    spans = [(c.get_paths()[0].vertices[:, 1].min(),
              c.get_paths()[0].vertices[:, 1].max()) for c in ax.collections]
    # The TOP alone does not catch this: y is already cumulative by the time it is filled,
    # so a band filled to zero still ends at the total. The bottom edge is the assertion.
    assert spans[0] == pytest.approx((0.0, 0.6))
    assert spans[1] == pytest.approx((0.6, 1.0)), (
        "the second band must START at the first one's top; filling it to zero paints it "
        "over the band beneath and the smaller series disappears")
    _close(fig)


def test_stacked_bars_stack():
    fig, ax = fr.draw_panel([
        {"type": "bar", "x": [1, 2], "y": [1, 1], "marker": {"color": "#396cd8"}},
        {"type": "bar", "x": [1, 2], "y": [2, 2], "marker": {"color": "#519b30"}},
    ], {"barmode": "stack"})
    tops = [c.get_paths()[0].vertices[:, 1].max() for c in ax.collections]
    assert tops == pytest.approx([1.0, 3.0])
    _close(fig)


def test_an_unknown_trace_type_is_refused_not_skipped():
    """A silently dropped series is a panel that is wrong and says nothing."""
    with pytest.raises(ValueError, match="cannot draw trace type"):
        fr.draw_panel([{"type": "violin", "x": [1], "y": [1]}], {})


# --- theme --------------------------------------------------------------------

def test_a_theme_substitutes_colours_without_touching_the_spec():
    spec_color = figspec.CATEGORICAL[0]
    assert fr.SCREEN(spec_color) == spec_color
    assert fr.GRAYSCALE(spec_color) != spec_color
    assert fr.GRAYSCALE(None) is None


def test_the_grayscale_theme_keeps_the_categories_apart():
    """Three stacked segments in one grey are one segment. The steps have to separate."""
    greys = [fr.GRAYSCALE(c) for c in figspec.CATEGORICAL]
    assert len(set(greys)) == len(greys)
    lums = sorted(int(g[1:3], 16) for g in greys)
    assert min(b - a for a, b in zip(lums, lums[1:])) >= 20, (
        f"adjacent greys too close to tell apart in print: {greys}")


def test_colour_follows_the_entity_not_its_rank():
    """The same spec colour maps to the same output colour wherever it appears, so a
    filter that changes the series count cannot repaint the survivors."""
    assert fr.GRAYSCALE(figspec.CATEGORICAL[2]) == fr.GRAYSCALE(figspec.CATEGORICAL[2])


# --- the contract with figspec ------------------------------------------------

def test_every_trace_type_figspec_may_emit_can_be_drawn():
    """The closure this renderer rests on. If figspec's guarded set ever grows, this fails
    here rather than shipping a blank panel to a reader."""
    drawable = {"scatter", "bar", "heatmap"}
    assert figspec.SUPPORTED_TRACE_TYPES == drawable, (
        f"figspec may now emit {figspec.SUPPORTED_TRACE_TYPES - drawable}, which "
        f"figrender cannot draw. Teach draw_panel the new type or drop it from the spec.")


def test_every_spec_builder_renders():
    """The end-to-end contract: each of figspec's nine builders survives the renderer.

    The trace-type closure above says the renderer knows every MARK a spec may carry; this
    says it also survives the LAYOUTS they are carried in — log axes, equal aspect, shapes,
    stacked bars, a colour bar. Fixtures come from ``test_figspec`` rather than being
    written again here, so the two suites cannot drift apart about what a snapshot is.
    """
    from .test_figspec import cons, dist, err, joint, pos_dist

    specs = {
        "per_position": figspec.per_position_spec([("A", err([0.1, None, 0.3]), cons()),
                                                   ("B", err([0.2, 0.4]), None)]),
        "histogram": figspec.histogram_spec([("a", dist([1, 2, 3]), "#111111")],
                                            x_title="x"),
        "terminal_reach": figspec.terminal_reach_spec(
            [("A", pos_dist([1, 1, 9, 1, 2]))], terminus="last"),
        "joint_shortfall": figspec.joint_shortfall_spec([("A", joint([(1, 3, 7)]), 100)]),
        "ranked_bar": figspec.ranked_bar_spec([("a", 1.0), ("b", 9.0)],
                                              x_title="x"),
        "category_bar": figspec.category_bar_spec({"0": 3, "60": 524},
                                                  x_title="MAPQ", log_y=True),
        "voltage": figspec.voltage_spec(
            [{"scan": 1, "t_hours": 0.0, "running_mv": -185.0, "offset_mv": -5.0},
             {"scan": 2, "t_hours": 1.5, "running_mv": -195.0, "offset_mv": -15.0}]),
    }
    for name, spec in specs.items():
        panels = fr.render_spec(spec)
        assert panels, f"{name} rendered nothing"
        for p in panels:
            assert _data_marks(p.svg) > 0, (
                f"{name}/{p.label} draws nothing in its plot area — an empty panel is "
                f"what a dropped series looks like from outside")


# --- emphasis: the marks a figure points at -----------------------------------

def test_the_print_theme_reddens_only_the_marks_the_spec_tagged():
    """One spec, two media, same MEANING.

    The screen keeps a hue per variant class; print turns the same marks red against an
    otherwise grey page. Neither is achieved by a second spec — the tag is in the data.
    """
    tagged = {"type": "scatter", "mode": "markers", "x": [1], "y": [1],
              "marker": {"color": figspec.CATEGORICAL[0]}, "meta": {"emphasis": True}}
    plain = {"type": "scatter", "mode": "markers", "x": [1], "y": [1],
             "marker": {"color": figspec.CATEGORICAL[0]}}
    assert fr.GRAYSCALE.for_trace(tagged, figspec.CATEGORICAL[0]) == fr.GRAYSCALE.emphasis
    assert fr.GRAYSCALE.for_trace(plain, figspec.CATEGORICAL[0]) != fr.GRAYSCALE.emphasis
    # On screen the tag changes nothing: the spec already chose the colour.
    assert fr.SCREEN.for_trace(tagged, figspec.CATEGORICAL[0]) == figspec.CATEGORICAL[0]


def test_print_draws_a_flag_at_half_the_diameter_and_no_other_marker():
    """2026-11-12: the red flags were too big on paper. What shows of a marker is
    its face plus its edge, so both are halved (the per-position flag, size 7: 5.2 pt
    across before, 2.6 pt now). Untagged markers, such as the voltage figure's, and
    every marker on screen keep their size."""
    flag = {"type": "scatter", "mode": "markers", "x": [1], "y": [1],
            "meta": {"emphasis": True},
            "marker": {"size": 7, "color": figspec.EMPHASIS, "symbol": "circle"}}
    plain = {**flag, "meta": {}}

    def across(trace, theme) -> float:
        fig, ax = fr.draw_panel([trace], {}, theme=theme)
        (line,) = ax.get_lines()
        drawn = line.get_markersize() + line.get_markeredgewidth()
        _close(fig)
        return drawn

    assert across(flag, fr.GRAYSCALE) == pytest.approx(across(flag, fr.SCREEN) / 2)
    assert across(plain, fr.GRAYSCALE) == pytest.approx(across(plain, fr.SCREEN))



def test_the_emphasis_colour_is_the_only_chroma_in_print():
    """"Black and white, with only what matters in red" is checkable, so check it."""
    def chroma(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return max(r, g, b) - min(r, g, b)

    ramp = tuple(step[1] for step in figspec.SEQUENTIAL_BLUE)
    for token in figspec.CATEGORICAL + ramp + (figspec.INK_PRIMARY, figspec.GRIDLINE,
                                               figspec.AXIS_LINE):
        assert chroma(fr.GRAYSCALE(token)) == 0, f"{token} is not grey in print"
    assert chroma(fr.GRAYSCALE(figspec.EMPHASIS)) > 100


def test_the_sequential_ramp_is_mapped_too_and_stays_monotonic():
    """A colour map WALKS the ramp rather than looking a colour up, so an unmapped stop
    passes straight through — which left the shortfall heatmap blue on a monochrome page,
    with nothing to say it should not be. In grey, lightness is the only channel a
    magnitude ramp has, so it has to stay ordered."""
    def lightness(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    greys = [fr.GRAYSCALE(step[1]) for step in figspec.SEQUENTIAL_BLUE]
    assert len(set(greys)) == len(greys), f"two stops map to one grey: {greys}"
    assert all(a > b for a, b in zip(greys, greys[1:])
               for a, b in [(lightness(a), lightness(b))]), (
        f"the ramp is not light-to-dark in print: {greys}")


def test_the_first_print_slot_is_not_near_black():
    """Most charts use slot 1 ALONE — read length, poly(A), MAPQ, read share, the ranked
    medians. A single series has nothing to be told apart from, so the darkest step buys
    nothing and costs a page that reads as heavy: a filled histogram outlined in
    near-black was the darkest thing on it."""
    def lightness(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert lightness(fr.GRAYSCALE(figspec.CATEGORICAL[0])) >= 48


# --- typography ---------------------------------------------------------------

def test_the_spec_font_reaches_the_drawing():
    """The renderer used to ignore layout.font entirely and take matplotlib's defaults —
    10 pt black DejaVu against the spec's 12, INK_SECONDARY and a system stack. So two
    figures drawn from the SAME spec set their axis furniture differently, which is the
    drift this module exists to prevent."""
    fig, ax = fr.draw_panel(
        [{"type": "scatter", "mode": "lines", "x": [1, 2], "y": [1, 2]}],
        figspec._layout(xaxis={"title": {"text": "x"}}))
    expected = 12 * fr._FONT_PT_PER_SPEC_UNIT
    tick = ax.get_xticklabels()[0]
    assert tick.get_fontsize() == pytest.approx(expected)
    assert ax.xaxis.label.get_fontsize() == pytest.approx(expected + 1)
    # ...and the spec's ink, not matplotlib's black.
    assert tick.get_color() != "black"
    _close(fig)


def test_print_typography_follows_the_print_theme():
    """The text is chrome, so it is themed like chrome — not left at the screen's ink."""
    layout = figspec._layout()
    fig, ax = fr.draw_panel([{"type": "scatter", "mode": "lines", "x": [1], "y": [1]}],
                            layout, theme=fr.GRAYSCALE)
    assert ax.get_xticklabels()[0].get_color() == fr.GRAYSCALE(figspec.INK_SECONDARY)
    _close(fig)


def test_only_installed_font_families_are_requested():
    """matplotlib logs `findfont: Font family not found` once per missing name per
    render, and an operator reads that as something having gone wrong with the report.
    Its own default list ships names most Linux boxes do not have."""
    from matplotlib import font_manager

    have = {f.name for f in font_manager.fontManager.ttflist}
    for stack in (fr._font_family(figspec.FONT_FAMILY), fr._font_family(None)):
        assert stack, "the stack must never come back empty"
        assert set(stack) <= have, f"not installed: {sorted(set(stack) - have)}"


def test_the_print_greys_all_stay_visible_against_paper():
    """A step that drops below 3:1 is a band the reader cannot see at all."""
    def contrast(hexcolor):
        def chan(c):
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (chan(int(hexcolor[i:i + 2], 16)) for i in (1, 3, 5))
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return 1.05 / (lum + 0.05)

    for token in figspec.CATEGORICAL:
        assert contrast(fr.GRAYSCALE(token)) >= 3.0, token


def test_every_print_grey_that_shares_a_chart_carries_its_own_texture():
    """Identity in print is the TEXTURE, not the grey.

    Holding every step above 3:1 leaves ~0.34 of OKLab lightness for four categories —
    three gaps of about 11 against a 15 floor — so the greys alone cannot do it. That is
    a property of monochrome, not a palette that can be re-stepped out of it.
    """
    hatches = [fr.GRAYSCALE.hatch(c) for c in figspec.CATEGORICAL]
    assert hatches[0] is None, "the first slot is solid; texture starts at the second"
    assert all(hatches[1:]), f"an untextured grey shares a chart with others: {hatches}"
    assert len(set(hatches[1:])) == len(hatches[1:]), "two categories share a texture"


def test_a_hatch_is_actually_drawn_and_in_the_hatch_colour():
    """edgecolor does not colour a hatch, and the rcParam does not reach a Collection.

    Measured on matplotlib 3.11.2: with a white EDGE the band comes out one flat grey,
    so a category meant to be told apart by texture is told apart by its grey alone.
    """
    fig, ax = fr.draw_panel([
        {"type": "scatter", "mode": "lines", "x": [0, 1], "y": [1, 1],
         "stackgroup": "one", "fillcolor": figspec.CATEGORICAL[1],
         "line": {"width": 0}, "name": "b"}], {}, theme=fr.GRAYSCALE)
    band = ax.collections[0]
    assert band.get_hatch() == fr.GRAYSCALE.hatch(figspec.CATEGORICAL[1])
    assert tuple(band.get_hatchcolor()[0][:3]) == (1.0, 1.0, 1.0)
    _close(fig)


def _filled(fillcolor=None, colour=figspec.CATEGORICAL[1]):
    tr = {"type": "scatter", "mode": "lines", "x": [0, 1], "y": [1, 1],
          "line": {"color": colour}, "fill": "tozeroy"}
    if fillcolor is not None:
        tr["fillcolor"] = fillcolor
    return tr


def test_a_stated_fill_is_drawn_at_its_own_alpha():
    """figspec states every distribution's fill (FILL_ALPHA). figrender drew any stated
    fill at full strength and an unstated one at 0.25, while plotly draws an unstated one
    at 0.5: one series at three strengths across the GUI, the screen and the PDF."""
    from matplotlib.colors import to_hex
    colour = figspec.CATEGORICAL[1]
    fig, ax = fr.draw_panel([_filled(figspec.fill_of(colour))], {})
    area = ax.collections[0]
    assert area.get_alpha() == pytest.approx(figspec.FILL_ALPHA)
    assert to_hex(area.get_facecolor()[0], keep_alpha=False) == colour
    _close(fig)


def test_an_unstated_fill_is_drawn_as_plotly_draws_it():
    fig, ax = fr.draw_panel([_filled()], {})
    assert ax.collections[0].get_alpha() == pytest.approx(0.5)
    _close(fig)


def test_a_stated_fill_still_greys_and_hatches_in_print():
    """The print theme maps a HEX. An rgba() fill looked up whole would miss the map and
    keep its hue on the monochrome page, and lose its hatch."""
    from matplotlib.colors import to_hex
    colour = figspec.CATEGORICAL[1]
    fig, ax = fr.draw_panel([_filled(figspec.fill_of(colour))], {}, theme=fr.GRAYSCALE)
    area = ax.collections[0]
    assert to_hex(area.get_facecolor()[0], keep_alpha=False) == fr.GRAYSCALE(colour)
    assert area.get_hatch() == fr.GRAYSCALE.hatch(colour)
    assert area.get_alpha() == pytest.approx(figspec.FILL_ALPHA)
    _close(fig)


def _bar(colour, x=("a", "b")):
    return {"type": "bar", "x": list(x), "y": [1, 2],
            "marker": {"color": figspec.fill_of(colour),
                       "line": {"color": colour, "width": figspec.SERIES_LINE_PX}}}


def test_a_bar_is_a_translucent_face_under_an_opaque_outline():
    """The alpha goes into the face only. As the artist's alpha it would fade the outline
    with it, and the outline is what keeps a light bar's edge on the page."""
    from matplotlib.colors import to_hex
    colour = figspec.CATEGORICAL[0]
    fig, ax = fr.draw_panel([_bar(colour)], {})
    patch = ax.patches[0]
    face, edge = patch.get_facecolor(), patch.get_edgecolor()
    assert to_hex(face, keep_alpha=False) == colour
    assert face[3] == pytest.approx(figspec.FILL_ALPHA)
    assert to_hex(edge, keep_alpha=False) == colour and edge[3] == pytest.approx(1.0)
    # In points: the spec's CSS px divided as font sizes are (test_print_line_widths).
    assert patch.get_linewidth() == pytest.approx(
        figspec.SERIES_LINE_PX / figspec.SPEC_UNITS_PER_PT)
    _close(fig)


def test_a_numeric_bar_prints_as_grey_face_and_grey_outline():
    """MAPQ's path: numeric x draws one PolyCollection. In print it was a solid block of
    the slot's grey; it is that grey at FILL_ALPHA under the same grey outlined."""
    from matplotlib.colors import to_hex
    colour = figspec.CATEGORICAL[0]
    fig, ax = fr.draw_panel([_bar(colour, x=(0.0, 60.0))], {}, theme=fr.GRAYSCALE)
    coll = ax.collections[0]
    face, edge = coll.get_facecolor()[0], coll.get_edgecolor()[0]
    assert to_hex(face, keep_alpha=False) == fr.GRAYSCALE(colour)
    assert face[3] == pytest.approx(figspec.FILL_ALPHA)
    assert to_hex(edge, keep_alpha=False) == fr.GRAYSCALE(colour) and edge[3] == 1.0
    _close(fig)


def test_an_outlined_bar_keeps_its_hatch_in_print():
    """The outline takes the edge colour; the texture is coloured on its own path
    (set_hatchcolor), so a bar that needs one in print still has it."""
    from matplotlib.colors import to_hex
    colour = figspec.CATEGORICAL[1]
    fig, ax = fr.draw_panel([_bar(colour)], {}, theme=fr.GRAYSCALE)
    patch = ax.patches[0]
    assert patch.get_hatch() == fr.GRAYSCALE.hatch(colour)
    assert to_hex(patch.get_edgecolor(), keep_alpha=False) == fr.GRAYSCALE(colour)
    _close(fig)


def test_a_hatch_is_dropped_when_the_bar_is_too_narrow_to_hold_one():
    """1200 bars across 7 inches is under a point each: the texture becomes white
    streaks that read as gaps in the data."""
    many = list(range(1, 1201))
    fig, ax = fr.draw_panel(
        [{"type": "bar", "x": many, "y": [0.5] * len(many),
          "marker": {"color": figspec.CATEGORICAL[1]}}], {}, theme=fr.GRAYSCALE)
    assert ax.collections[0].get_hatch() in (None, "")
    _close(fig)
    few = [1, 2, 3, 4]
    fig, ax = fr.draw_panel(
        [{"type": "bar", "x": few, "y": [0.5] * len(few),
          "marker": {"color": figspec.CATEGORICAL[1]}}], {}, theme=fr.GRAYSCALE)
    assert ax.collections[0].get_hatch() == fr.GRAYSCALE.hatch(figspec.CATEGORICAL[1])
    _close(fig)


def test_marker_symbols_survive_into_the_drawing():
    """In print every emphasised mark is the same red, so the shape is the only thing
    left telling one kind of mark from another. Every symbol the renderer maps must reach
    the page as itself."""
    symbols = list(fr._SYMBOLS)
    traces = [{"type": "scatter", "mode": "markers", "x": [1], "y": [1],
               "name": symbol, "marker": {"color": figspec.EMPHASIS, "symbol": symbol},
               "meta": {"emphasis": True}}
              for symbol in symbols]
    fig, ax = fr.draw_panel(traces, {}, theme=fr.GRAYSCALE)
    drawn = [ln.get_marker() for ln in ax.lines]
    assert sorted(drawn) == sorted(fr._SYMBOLS[s] for s in symbols), (
        f"symbols collapsed or changed: {drawn}")
    colours = {ln.get_color() for ln in ax.lines}
    assert colours == {fr.GRAYSCALE.emphasis}, "every emphasised mark should be the print red"
    _close(fig)


def test_a_dotted_layout_key_lands_where_plotly_would_put_it():
    """A relayout patch keys nested fields with dots: {"xaxis.range": [...]}.

    Merged flat it becomes a literal "xaxis.range" key that nothing reads, and the panel
    quietly keeps the layout's own window — every construct after the first shown through
    the wrong range, with no error and a plot that looks entirely reasonable.
    """
    spec = {"data": [{"type": "scatter", "mode": "lines", "x": [0, 100], "y": [1, 1]}],
            "layout": {"xaxis": {"range": [0, 10], "title": {"text": "nt"}},
                       "updatemenus": [{"buttons": [
                           {"label": "A", "args": [{}, {"xaxis.range": [40, 60]}]}]}]}}
    _label, _traces, layout = fr._panels(spec)[0]
    assert layout["xaxis"]["range"] == [40, 60]
    assert "xaxis.range" not in layout
    # ...and the sibling keys of the patched field survive.
    assert layout["xaxis"]["title"] == {"text": "nt"}

    fig, ax = fr.draw_panel(_traces, layout)
    assert ax.get_xlim() == pytest.approx((40.0, 60.0))
    _close(fig)


def test_patching_one_panel_does_not_reach_the_others():
    """The layout is copied per panel, or panel 2's window follows panel 1 around."""
    spec = {"data": [{"type": "scatter", "mode": "lines", "x": [0, 100], "y": [1, 1]}],
            "layout": {"xaxis": {"range": [0, 10]}, "updatemenus": [{"buttons": [
                {"label": "A", "args": [{}, {"xaxis.range": [40, 60]}]},
                {"label": "B", "args": [{}, {}]}]}]}}
    panels = fr._panels(spec)
    assert panels[0][2]["xaxis"]["range"] == [40, 60]
    assert panels[1][2]["xaxis"]["range"] == [0, 10]


# --- a second y axis: a panel under the first, sharing x -----------------------------

def _two_panels(**extra):
    """plotly's stacked-subplot layout: y over y2 by domain, one x anchored at the bottom."""
    return {"barmode": "stack",
            "xaxis": {"anchor": "y2", "title": {"text": "position"}},
            "yaxis": {"domain": [0.3, 1.0], "title": {"text": "upper"}},
            "yaxis2": {"domain": [0.0, 0.22], "anchor": "x", "title": {"text": "lower"}},
            **extra}


def _top(collection) -> float:
    return collection.get_paths()[0].vertices[:, 1].max()


def test_a_second_y_axis_becomes_a_panel_under_the_first():
    """Paper gets one axes per domain, top first, sharing x and sized by the domains."""
    fig, ax = fr.draw_panel([
        {"type": "bar", "x": [1, 2], "y": [0.5, 0.5], "marker": {"color": "#396cd8"}},
        {"type": "bar", "x": [1, 2], "y": [0.1, 0.2], "yaxis": "y2",
         "marker": {"color": "#519b30"}},
    ], _two_panels())
    assert len(fig.axes) == 2 and fig.axes[0] is ax
    lower = fig.axes[1]
    assert ax.get_position().y0 > lower.get_position().y1
    assert ax.get_position().height > 2 * lower.get_position().height
    assert len(ax.collections) == 1 and len(lower.collections) == 1
    assert lower.get_ylabel() == "lower" and ax.get_ylabel() == "upper"
    # One x axis: its title under the bottom panel only.
    assert lower.get_xlabel() == "position" and ax.get_xlabel() == ""
    _close(fig)


def test_each_panel_stacks_only_its_own_bars():
    """A bar on the lower panel starts from 0, not from the top of the upper stack."""
    fig, ax = fr.draw_panel([
        {"type": "bar", "x": [1, 2], "y": [1, 1], "marker": {"color": "#396cd8"}},
        {"type": "bar", "x": [1, 2], "y": [2, 2], "marker": {"color": "#519b30"}},
        {"type": "bar", "x": [1, 2], "y": [0.5, 0.5], "yaxis": "y2",
         "marker": {"color": "#9f4f8f"}},
    ], _two_panels())
    assert [_top(c) for c in ax.collections] == pytest.approx([1.0, 3.0])
    assert _top(fig.axes[1].collections[0]) == pytest.approx(0.5)
    _close(fig)


def test_a_shape_lands_on_the_axis_its_yref_names():
    line = {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "line": {"dash": "dash"}}
    fig, ax = fr.draw_panel(
        [{"type": "bar", "x": [1], "y": [0.5]},
         {"type": "bar", "x": [1], "y": [0.1], "yaxis": "y2"}],
        _two_panels(shapes=[{**line, "y0": 0.5, "y1": 0.5, "yref": "y"},
                            {**line, "y0": 0.05, "y1": 0.05, "yref": "y2"}]))
    assert [ln.get_ydata()[0] for ln in ax.lines] == [0.5]
    assert [ln.get_ydata()[0] for ln in fig.axes[1].lines] == [0.05]
    _close(fig)


def test_a_trace_on_an_axis_the_layout_does_not_declare_is_refused():
    """Drawn on the main axes instead, it would sit on a scale it was not meant for."""
    with pytest.raises(ValueError, match="y3"):
        fr.draw_panel([{"type": "bar", "x": [1], "y": [1], "yaxis": "y3"}], _two_panels())
    with pytest.raises(ValueError, match="y2"):
        fr.draw_panel([{"type": "bar", "x": [1], "y": [1], "yaxis": "y2"}], {})


def test_the_legend_lists_every_panels_series_once():
    fig, ax = fr.draw_panel([
        {"type": "bar", "x": [1], "y": [0.5], "name": "substitution",
         "marker": {"color": "#396cd8"}},
        {"type": "bar", "x": [1], "y": [0.1], "yaxis": "y2", "name": "insertion",
         "marker": {"color": "#9f4f8f"}},
    ], _two_panels())
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["substitution",
                                                                   "insertion"]
    assert fig.axes[1].get_legend() is None
    _close(fig)
