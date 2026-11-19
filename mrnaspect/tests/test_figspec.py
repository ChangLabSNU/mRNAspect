"""Figure specs are data, so they are testable without a browser or plotly."""

from __future__ import annotations

import pytest

from mrnaspect.reporting.figspec import INSERTION, MISMATCH_PARTS, per_position_spec


def err(sub, dele=None, ins=None):
    """An error_hotspot snapshot reduced to its per-position arrays."""
    n = len(sub)
    return {
        "substitution_rate": list(sub),
        "deletion_rate": list(dele if dele is not None else [0.0] * n),
        "insertion_rate": list(ins if ins is not None else [0.0] * n),
    }


def cons(sites=(), threshold=0.5, **extra):
    """A consensus snapshot reduced to what the figure reads."""
    return {"mismatch_sites": [dict(s) for s in sites], "mismatch_threshold": threshold,
            **extra}


def _s(pos, rate, ref="A", depth=10, **counts):
    """A flagged position as the consensus module emits it; ``pos`` is 0-based."""
    base_counts = {"A": 0, "C": 0, "G": 0, "T": 0, "del": 0, "ins": 0}
    base_counts.update(counts)
    return {"pos": pos, "mismatch_rate": rate, "ref_base": ref, "depth": depth,
            "base_counts": base_counts}


def _named(spec, name):
    return [t for t in spec["data"] if t["name"] == name]


# =============================================================================
# Per-position: where the reads leave the reference, and how
# =============================================================================

# --- the panels ---------------------------------------------------------------------

def test_substitution_and_deletion_stack_into_the_mismatch_rate():
    """Their sum is the share of reads not carrying the reference base, the quantity the
    flags are on, so they share the upper panel and its [0, 1] axis."""
    spec = per_position_spec([("C", err([0.1, 0.2], [0.05, 0.0]), cons())])
    parts = [t for t in spec["data"] if t["type"] == "bar" and t.get("yaxis") is None]
    assert [t["name"] for t in parts] == [n for _, n, _ in MISMATCH_PARTS]
    assert spec["layout"]["barmode"] == "stack"
    assert spec["layout"]["yaxis"]["range"] == [0, 1]


def test_insertions_are_drawn_apart_in_a_lower_panel():
    """An insertion takes no share of the reads; stacked on the parts, it would push a
    bar past the threshold that the reads did not cross."""
    spec = per_position_spec([("C", err([0.1], ins=[0.3]), cons())])
    ins = _named(spec, INSERTION[1])[0]
    assert ins["yaxis"] == "y2" and ins["y"] == [0.3]
    lay = spec["layout"]
    assert lay["yaxis2"]["domain"][1] < lay["yaxis"]["domain"][0]
    assert lay["xaxis"]["anchor"] == "y2"


def test_stacking_forces_a_linear_axis():
    """On a log axis the segments of a stacked bar do not add to its total."""
    spec = per_position_spec([("A", err([0.1]), cons())])
    assert spec["layout"]["yaxis"].get("type") in (None, "linear")


def test_positions_are_one_based_and_cover_the_whole_array():
    spec = per_position_spec([("C", err([0.1, 0.2, 0.3]), cons())])
    sub = _named(spec, "substitution")[0]
    assert sub["x"] == [1, 2, 3] and sub["y"] == [0.1, 0.2, 0.3]


def test_uncovered_positions_stay_null_so_they_render_as_gaps():
    """None must survive into the spec: a gap and a zero are different findings."""
    spec = per_position_spec([("C", err([0.1, None, 0.3]), cons())])
    assert _named(spec, "substitution")[0]["y"] == [0.1, None, 0.3]


def test_arrays_that_disagree_in_length_are_refused():
    bad = {"substitution_rate": [0.1, 0.2], "deletion_rate": [0.1], "insertion_rate": [0.0]}
    with pytest.raises(ValueError, match="disagree in length"):
        per_position_spec([("C", bad, cons())])


def test_a_snapshot_with_no_per_position_arrays_is_refused():
    with pytest.raises(KeyError):
        per_position_spec([("C", {"mean_total_error_rate": 0.1}, cons())])


def test_a_construct_with_no_coverage_still_renders():
    """A reference nothing aligned to must not crash the report."""
    spec = per_position_spec([("empty", err([None] * 3), cons())])
    assert _named(spec, "substitution")[0]["y"] == [None] * 3
    assert _named(spec, "flagged")[0]["x"] == []


# --- the flags ------------------------------------------------------------------------

def test_a_flag_marker_stands_on_its_own_bar():
    """``pos`` is 0-based and the bars 1-based; the marker is the consensus rate, which
    is the stacked top of its bar."""
    spec = per_position_spec([("C", err([0.1, 0.9, 0.2], [0.0, 0.06, 0.0]),
                               cons([_s(1, 0.96)]))])
    flag = _named(spec, "flagged")[0]
    assert (flag["x"], flag["y"]) == ([2], [0.96])
    sub, dele = _named(spec, "substitution")[0], _named(spec, "deletion")[0]
    i = sub["x"].index(2)
    assert sub["y"][i] + dele["y"][i] == pytest.approx(flag["y"][0])
    assert flag["meta"] == {"emphasis": True} and flag["marker"]["symbol"] == "circle"


def test_the_hover_carries_the_evidence_not_just_the_number():
    """What the reads carry instead is the actionable part; a bare 0.959 is not."""
    spec = per_position_spec([("A", err([0.0] * 40),
                               cons([_s(39, 0.959, ref="A", depth=370, A=15, G=355)]))])
    flag = _named(spec, "flagged")[0]
    assert flag["customdata"] == [["A", 15, 370, "A 15 · G 355"]]
    assert "customdata[0]" in flag["hovertemplate"]


def test_the_threshold_drawn_is_the_configured_one():
    """Not invented at display time: it is what the module flagged at."""
    spec = per_position_spec([("A", err([0.1]), cons(threshold=0.25))])
    shapes = spec["layout"]["shapes"]
    assert [sh["y0"] for sh in shapes] == [0.25] and shapes[0]["yref"] == "y"


def test_without_a_consensus_snapshot_the_bars_still_render():
    """No flags and no threshold to draw, rather than a crash or an invented line."""
    spec = per_position_spec([("A", err([0.1, 0.2]), None)])
    assert spec["layout"]["shapes"] == []
    assert _named(spec, "flagged")[0]["x"] == []
    assert _named(spec, "substitution")[0]["y"] == [0.1, 0.2]


def test_no_null_based_flagging_leaks_in():
    """The flags are the consensus module's, at its threshold, and nothing else.

    error_hotspot ranks positions (``top_positions``) and would rank them against a null
    once one exists; a snapshot that carries those keys must still change nothing here.
    """
    plain = err([0.1, 0.9])
    with_null = err([0.1, 0.9])
    with_null["top_positions"] = [{"pos": 0, "residual": 0.8}]
    with_null["ranked_by"] = "residual_over_null"
    with_null["null"] = {"model": "m", "arm": "m1psi", "table": "t.tsv"}
    assert (per_position_spec([("A", with_null, cons())])
            == per_position_spec([("A", plain, cons())])), (
        "a snapshot's hotspot keys reached the figure")


# --- the subtitle -----------------------------------------------------------------------

def test_the_detail_gives_identity_its_breakdown_and_the_flags():
    """The construct's identity and what the rest of its reads are, which add to 100,
    then how many positions are flagged and at what."""
    c = cons([_s(0, 0.9), _s(1, 0.6)], consensus_identity_pct=79.4869,
             substitution_pct=12.7661, deletion_pct=7.747, insertions_per_100_bases=3.3661)
    title = per_position_spec([("QK-09", err([0.9, 0.6]), c)])["layout"]["title"]["text"]
    for part in ("per-base identity 79.5%", "substitution 12.8%", "deletion 7.7%",
                 "insertions 3.4 per 100 bases", "2 positions flagged at mismatch ≥ 0.5"):
        assert part in title, (part, title)


def test_a_construct_with_one_flag_says_position():
    title = per_position_spec([("A", err([0.9]), cons([_s(0, 0.9)]))])[
        "layout"]["title"]["text"]
    assert "1 position flagged" in title


# --- the dropdown -----------------------------------------------------------------------

def test_no_tracks_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        per_position_spec([])


def test_only_the_first_construct_is_visible_initially():
    spec = per_position_spec([("A", err([0.1]), cons()), ("B", err([0.2]), cons())])
    vis = [t["visible"] for t in spec["data"]]
    per = len(vis) // 2
    assert vis[:per] == [True] * per and vis[per:] == [False] * per


def test_each_button_shows_exactly_one_construct():
    tracks = [("A", err([0.1]), cons()), ("B", err([0.2]), cons()),
              ("C", err([0.3]), cons())]
    spec = per_position_spec(tracks)
    buttons = spec["layout"]["updatemenus"][0]["buttons"]
    assert [b["label"] for b in buttons] == ["A", "B", "C"]
    n_traces = len(spec["data"])
    for i, b in enumerate(buttons):
        visible = b["args"][0]["visible"]
        # Length must match the trace count, or plotly silently leaves the tail as-is
        # and two constructs are drawn on top of each other.
        assert len(visible) == n_traces
        per = n_traces // 3
        assert visible == [j // per == i for j in range(n_traces)]


def test_the_title_and_threshold_follow_the_dropdown():
    """The caption names a construct; showing another's would be a fabricated label."""
    spec = per_position_spec([("A", err([0.1]), cons(threshold=0.1)),
                              ("B", err([0.2]), cons(threshold=0.3))])
    b = spec["layout"]["updatemenus"][0]["buttons"]
    for button in b:
        assert button["args"][1]["title"]["text"].startswith(button["label"] + "<br>")
    assert b[0]["args"][1]["shapes"][0]["y0"] == 0.1
    assert b[1]["args"][1]["shapes"][0]["y0"] == 0.3


def test_the_range_slider_is_present():
    """Item 2: the same selection the distributions have."""
    spec = per_position_spec([("A", err([0.1]), cons())])
    assert spec["layout"]["xaxis"]["rangeslider"]["visible"] is True


def test_the_spec_is_json_serialisable():
    """It travels to the GUI and into the report as JSON; a numpy scalar would not."""
    import json
    spec = per_position_spec([("A", err([0.1, None, 0.0]), cons([_s(0, 0.6)]))])
    json.loads(json.dumps(spec))


# =============================================================================
# Distributions
# =============================================================================

from mrnaspect.reporting.figspec import (  # noqa: E402
    _default_range, _escaped_note, _outside_default, histogram_spec,
)


def dist(counts, *, bin_min=0.0, width=10.0, q1=None, median=None, q3=None,
         under=0, over=0):
    return {
        "n": sum(counts) + under + over, "bin_min": bin_min,
        "bin_max": bin_min + width * len(counts), "n_bins": len(counts),
        "bin_width": width, "bin_counts": list(counts),
        "underflow": under, "overflow": over,
        "Q1": q1, "median": median, "Q3": q3,
    }


def test_the_default_window_is_the_tukey_fence():
    d = dist([0] * 100, q1=100.0, q3=200.0)
    d["bin_counts"][5] = 1
    d["bin_counts"][95] = 1
    lo, hi = _default_range(d)
    assert hi == 200.0 + 1.5 * 100.0   # Q3 + 1.5 IQR
    assert lo == 50.0                  # clamped to the first occupied bin, not negative


def test_the_window_never_leaves_the_histogram_or_the_data():
    """Empty axis beyond the data is the thing being removed; don't add it back."""
    d = dist([0, 5, 0, 0, 0, 0, 0, 0, 0, 0], q1=10.0, q3=90.0)
    lo, hi = _default_range(d)
    assert lo >= d["bin_min"] and hi <= d["bin_max"]
    assert hi <= 20.0  # the last occupied bin ends at 20


def test_a_lone_far_outlier_does_not_drag_the_window_open():
    """The measured failure of 'first to last occupied bin'.

    On a real run 99% of read-length mass sat below 2,405 nt while the last occupied bin
    was at 4,343, and the naive rule opened on nearly the whole 5,700 nt axis.
    """
    counts = [0] * 500
    for i in range(10, 40):
        counts[i] = 100
    counts[480] = 1
    d = dist(counts, width=10.0, q1=120.0, q3=380.0)
    lo, hi = _default_range(d)
    assert hi < 1000.0, hi


def _run_of_two_constructs(over: int = 0) -> dict:
    """1,000 reads: 5 at 0-10 nt, 45 fragments at 30-40, 855 at 100-130 (construct A),
    90 at 500-530 (construct B), 5 at 900. The fences see only construct A."""
    counts = [0] * 100
    counts[0], counts[3] = 5, 45
    counts[10] = counts[11] = counts[12] = 285
    counts[50] = counts[51] = counts[52] = 30
    counts[90] = 5
    return dist(counts, q1=105.0, q3=125.0, over=over)


_Q = (0.01, 0.99)


def test_the_fences_hide_a_construct_and_the_fragments_that_quantiles_keep():
    """Run 1: the upper fence (1,376 nt) hid every HA read (~1,900 nt); the lower one
    (953 nt) hid 22% of the reads, the short fragments."""
    d = _run_of_two_constructs()
    assert _default_range(d) == (105.0 - 1.5 * 20.0, 125.0 + 1.5 * 20.0)  # (75, 155)
    # The 10th read is in the 30-40 bin, the 990th in the 520-530 bin; whole bins kept.
    assert _default_range(d, _Q) == (30.0, 530.0)


def test_a_quantile_window_leaves_at_most_one_percent_off_each_side():
    d = _run_of_two_constructs()
    assert _outside_default(d, _Q) == 5 + 5     # the 0-10 nt bin and the 900 nt bin
    assert "10 beyond the default window" in _escaped_note(d, _Q)
    assert _outside_default(d) == 5 + 45 + 90 + 5, "the fences left out both ends"


def test_reads_past_the_axis_count_toward_the_quantiles():
    """Overflow is reads the histogram saw: 25 of them push the 99th percentile past
    every bin, so the view opens to the last occupied one."""
    assert _default_range(_run_of_two_constructs(over=25), _Q) == (30.0, 910.0)


def test_quantiles_outside_zero_to_one_or_out_of_order_are_refused():
    d = _run_of_two_constructs()
    for q in ((0, 0.99), (0.01, 1), (1, 99), (0.99, 0.01)):
        with pytest.raises(ValueError):
            _default_range(d, q)


def test_histogram_spec_opens_on_the_quantile_window_when_asked():
    d = _run_of_two_constructs()
    fences = histogram_spec([("a", d, "#000")], x_title="x")
    window = histogram_spec([("a", d, "#000")], x_title="x", window_quantiles=_Q)
    assert fences["layout"]["xaxis"]["range"] == [75.0, 155.0]
    assert window["layout"]["xaxis"]["range"] == [30.0, 530.0]


def test_reads_beyond_the_window_are_counted_not_dropped():
    counts = [0] * 100
    counts[5] = 50
    counts[90] = 7
    d = dist(counts, q1=50.0, q3=60.0)
    assert _outside_default(d) == 7
    assert "7 beyond the default window" in _escaped_note(d)


def test_escaped_mass_and_the_window_are_stated_as_different_facts():
    """One is outside the measurement, the other outside the current view."""
    counts = [0] * 100
    counts[5] = 50
    counts[90] = 3
    note = _escaped_note(dist(counts, q1=50.0, q3=60.0, under=2, over=4))
    assert "outside the histogram" in note
    assert "beyond the default window" in note


def test_the_note_states_the_fact_and_not_how_to_act_on_it():
    """It travels into the PDF as the panel label, where there is no slider to drag.

    The HTML caption is where a screen instruction belongs, and it exists only on screen.
    """
    counts = [0] * 100
    counts[5] = 50
    counts[90] = 3
    note = _escaped_note(dist(counts, q1=50.0, q3=60.0, under=2, over=4))
    for screen_only in ("drag", "slider", "hover", "click", "zoom"):
        assert screen_only not in note.lower(), (screen_only, note)


def test_a_histogram_with_no_quantiles_still_opens_on_its_data():
    d = dist([0, 0, 9, 0, 0])
    assert _default_range(d) == (20.0, 30.0)


def test_an_empty_histogram_has_no_window():
    assert _default_range(dist([0, 0, 0])) is None
    assert _escaped_note(dist([0, 0, 0])) == ""


def test_the_step_closes_at_the_right_edge_of_the_last_bin():
    """len(x) == len(counts)+1, or the final bin renders as a half-open ramp."""
    spec = histogram_spec([("a", dist([1, 2, 3]), "#000")],
                          x_title="x")
    tr = spec["data"][0]
    assert len(tr["x"]) == 4 and len(tr["y"]) == 4
    assert tr["y"][-1] == 0
    assert tr["line"]["shape"] == "hv"


def test_counts_that_disagree_with_the_declared_geometry_are_refused():
    d = dist([1, 2, 3]); d["n_bins"] = 5
    with pytest.raises(ValueError, match="bin_counts against n_bins"):
        histogram_spec([("a", d, "#000")], x_title="x")


def test_a_histogram_without_geometry_is_refused():
    with pytest.raises(ValueError, match="no bin geometry"):
        histogram_spec([("a", {"bin_counts": [1, 2]}, "#000")], x_title="x")


def test_no_series_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        histogram_spec([], x_title="x")


def test_several_series_overlay_on_one_axis():
    """poly(A) ships a raw and a corrected estimate of the same reads."""
    spec = histogram_spec(
        [("raw", dist([1, 2]), "#111"), ("corrected", dist([2, 1]), "#222")],
        x_title="x")
    assert [t["name"] for t in spec["data"]] == ["raw", "corrected"]


def test_quantile_markers_come_from_one_named_series():
    """Two overlaid distributions' markers would not say which series they belong to."""
    a = dist([1, 1], q1=1.0, median=2.0, q3=3.0)
    b = dist([1, 1], q1=50.0, median=60.0, q3=70.0)
    spec = histogram_spec([("a", a, "#111"), ("b", b, "#222")],
                          x_title="x", quantiles_from=1)
    assert [sh["x0"] for sh in spec["layout"]["shapes"]] == [50.0, 60.0, 70.0]


def test_a_missing_quantile_draws_no_line_rather_than_a_zero():
    """`median` is None when it falls inside escaped mass — that is not position 0."""
    spec = histogram_spec([("a", dist([1, 1], q1=1.0, q3=3.0), "#111")],
                          x_title="x")
    assert [sh["x0"] for sh in spec["layout"]["shapes"]] == [1.0, 3.0]


def test_the_range_slider_spans_the_whole_histogram():
    """The window is a starting point the reader can undo, not a truncation."""
    spec = histogram_spec([("a", dist([1, 0, 0, 1]), "#111")], x_title="x")
    assert spec["layout"]["xaxis"]["rangeslider"]["visible"] is True


# =============================================================================
# Terminal reach
# =============================================================================

from mrnaspect.reporting.figspec import (  # noqa: E402
    terminal_reach_spec, terminus_share,
)


def pos_dist(counts, *, bin_min=0.0, width=10.0):
    return {"bin_min": bin_min, "bin_max": bin_min + width * len(counts),
            "n_bins": len(counts), "bin_width": width, "bin_counts": list(counts),
            "n": sum(counts), "underflow": 0, "overflow": 0}


def test_the_terminal_share_is_the_number_this_figure_is_about():
    assert terminus_share([1, 1, 8], terminus="last") == 0.8
    assert terminus_share([8, 1, 1], terminus="first") == 0.8


def test_an_empty_distribution_has_no_share_rather_than_zero():
    assert terminus_share([0, 0, 0], terminus="last") is None


def test_an_unknown_terminus_is_refused():
    with pytest.raises(ValueError, match="terminus must be"):
        terminus_share([1, 2], terminus="middle")
    with pytest.raises(ValueError, match="terminus must be"):
        terminal_reach_spec([("A", pos_dist([1, 2]))], terminus="middle")


def test_nothing_is_marked_on_the_distribution():
    """Markers were removed: excluding only the final bin marked the whole shoulder of a
    construct that stops a few bases short, which is not distinguishing anything."""
    spec = terminal_reach_spec([("A", pos_dist([1, 1, 9, 1, 2]))], terminus="last")
    assert [t["name"] for t in spec["data"]] == ["reads"]
    assert not spec["layout"].get("annotations")


def test_one_trace_per_construct_and_only_the_first_is_visible():
    spec = terminal_reach_spec(
        [("A", pos_dist([1, 2, 9])), ("B", pos_dist([3, 1, 4]))], terminus="last")
    assert [t["visible"] for t in spec["data"]] == [True, False]


def test_the_subtitle_follows_the_dropdown():
    """The share is per construct; a stale one would describe the wrong reference."""
    spec = terminal_reach_spec(
        [("A", pos_dist([0, 0, 10])), ("B", pos_dist([10, 0, 0]))], terminus="last")
    b = spec["layout"]["updatemenus"][0]["buttons"]
    assert "100.0% in the bin" in b[0]["args"][1]["title"]["text"]
    assert "0.0% in the bin" in b[1]["args"][1]["title"]["text"]


def test_the_step_closes_at_the_end_of_the_last_bin():
    spec = terminal_reach_spec([("A", pos_dist([1, 2, 3]))], terminus="last")
    tr = spec["data"][0]
    assert len(tr["x"]) == 4 and tr["y"][-1] == 0


def test_a_distribution_with_no_geometry_is_refused():
    with pytest.raises(ValueError, match="no bin geometry"):
        terminal_reach_spec([("A", {"bin_counts": [1, 2]})], terminus="last")


def test_no_tracks_is_refused_here_too():
    with pytest.raises(ValueError, match="at least one"):
        terminal_reach_spec([], terminus="last")


# =============================================================================
# Joint shortfall
# =============================================================================

from mrnaspect.reporting.figspec import joint_shortfall_spec  # noqa: E402


def joint(cells, *, n_bins=4, width=10.0, n=None, out=0):
    return {"axes": ["five_prime_short_nt", "three_prime_short_nt"],
            "n_bins": n_bins, "bin_width": width,
            "n": n if n is not None else sum(c[2] for c in cells),
            "cells": [list(c) for c in cells], "out_of_range": out}


def test_the_sparse_cells_land_on_the_right_axes():
    """Row is 3', column is 5' — transposing it silently blames the wrong end."""
    spec = joint_shortfall_spec([("A", joint([(1, 3, 7)]), 100)])
    z = spec["data"][0]["z"]
    assert z[3][1] == 7
    assert z[1][3] is None


def test_empty_cells_are_null_not_zero():
    """None lets the background show through; a zero floor swamps a log colour scale."""
    z = joint_shortfall_spec([("A", joint([(0, 0, 5)]), 100)])["data"][0]["z"]
    assert z[0][0] == 5
    assert all(v is None for row in z for v in row if v != 5)


def test_iso_length_contours_are_drawn_on_the_anti_diagonal():
    """aligned span = ref_length - (d5 + d3), so x + y = constant is one length."""
    spec = joint_shortfall_spec([("A", joint([(0, 0, 1)], n_bins=60, width=10.0), 400)])
    for sh in spec["layout"]["shapes"]:
        assert sh["x0"] + sh["y0"] == sh["x1"] + sh["y1"]
        assert sh["x0"] == 0 and sh["y1"] == 0


def test_contours_outside_the_grid_are_dropped_not_drawn_off_screen():
    spec = joint_shortfall_spec([("A", joint([(0, 0, 1)], n_bins=4, width=10.0), 100000)])
    assert spec["layout"]["shapes"] == []


def test_without_a_reference_length_no_contour_is_guessed():
    spec = joint_shortfall_spec([("A", joint([(0, 0, 1)]), None)])
    assert spec["layout"]["shapes"] == []


def test_the_axes_are_locked_to_one_scale():
    """A stretched axis turns the 45-degree ruler into a different contour."""
    y = joint_shortfall_spec([("A", joint([(0, 0, 1)]), 100)])["layout"]["yaxis"]
    assert y["scaleanchor"] == "x" and y["scaleratio"] == 1


def test_reads_outside_the_grid_are_reported():
    spec = joint_shortfall_spec([("A", joint([(0, 0, 3)], out=11), 100)])
    assert "11 outside the grid" in spec["layout"]["title"]["text"]


def test_shapes_follow_the_dropdown():
    """Contours are layout-level: a stale set rules the wrong construct's lengths."""
    spec = joint_shortfall_spec([
        ("A", joint([(0, 0, 1)], n_bins=60, width=10.0), 400),
        ("B", joint([(0, 0, 1)], n_bins=60, width=10.0), 100),
    ])
    b = spec["layout"]["updatemenus"][0]["buttons"]
    assert [s["x0"] + s["y0"] for s in b[0]["args"][1]["shapes"]] != \
           [s["x0"] + s["y0"] for s in b[1]["args"][1]["shapes"]]


def test_each_cell_covers_its_own_bin():
    """Cell (i, j) counts [i*w, (i+1)*w) nt short. plotly takes x/y as cell centres, so
    they are the bin centres; lower edges would draw every cell half a bin off."""
    trace = joint_shortfall_spec([("A", joint([(1, 3, 7)]), 100)])["data"][0]
    assert trace["x"] == trace["y"] == [5.0, 15.0, 25.0, 35.0]
    assert "± 5 nt" in trace["hovertemplate"]


def test_the_printed_heatmap_spans_the_grid_exactly():
    """The PDF renderer reads the same centres: its grid runs 0 to n*w on both axes, the
    axes end there, and the one filled cell sits at its own bin (since 2026-11-12 a
    vector rectangle per cell, not an image)."""
    import matplotlib.pyplot as plt

    from mrnaspect.reporting import figrender as fr

    trace = joint_shortfall_spec([("A", joint([(1, 3, 7)]), 100)])["data"][0]
    fig, ax = plt.subplots()
    try:
        fr._draw_heatmap(fig, ax, trace, fr.SCREEN)
        assert ax.get_xlim() == pytest.approx((0.0, 40.0))
        assert ax.get_ylim() == pytest.approx((0.0, 40.0))
        (mesh,) = [c for c in ax.collections if c.get_array() is not None]
        (cell,) = mesh.get_paths()
        assert tuple(cell.get_extents().get_points().ravel()) == pytest.approx(
            (10.0, 30.0, 20.0, 40.0))
    finally:
        plt.close(fig)


def test_a_grid_without_geometry_is_refused():
    with pytest.raises(ValueError, match="no grid geometry"):
        joint_shortfall_spec([("A", {"cells": [[0, 0, 1]]}, 100)])


def test_no_tracks_is_refused_for_the_joint_too():
    with pytest.raises(ValueError, match="at least one"):
        joint_shortfall_spec([])


# =============================================================================
# Comparisons and the instrument
# =============================================================================

from mrnaspect.reporting.figspec import (  # noqa: E402
    category_bar_spec, pore_activity_spec, ranked_bar_spec, voltage_spec,
)


def test_ranked_bars_are_sorted_largest_first_as_read():
    """Plotly draws the first horizontal category at the BOTTOM, so the list is reversed."""
    spec = ranked_bar_spec([("a", 1.0), ("b", 9.0), ("c", 5.0)], x_title="x")
    assert spec["data"][0]["y"][::-1] == ["b", "c", "a"]
    assert spec["data"][0]["x"][::-1] == [9.0, 5.0, 1.0]


def test_an_unmeasured_category_sorts_last_and_keeps_its_gap():
    """None is not zero: a category that could not be measured must not rank as worst."""
    spec = ranked_bar_spec([("a", None), ("b", 2.0)], x_title="x")
    assert spec["data"][0]["y"][::-1] == ["b", "a"]
    assert spec["data"][0]["x"][::-1] == [2.0, None]


def test_a_reference_line_is_drawn_only_when_one_is_given():
    """No pooling ratio is known, so no expectation is invented."""
    plain = ranked_bar_spec([("a", 1.0)], x_title="x")
    assert "shapes" not in plain["layout"]
    withref = ranked_bar_spec([("a", 1.0)], x_title="x",
                              reference=(6.67, "even split"))
    assert withref["layout"]["shapes"][0]["x0"] == 6.67


def test_empty_ranked_bars_are_refused():
    with pytest.raises(ValueError, match="at least one"):
        ranked_bar_spec([], x_title="x")


def test_category_bars_are_ordered_numerically_not_lexically():
    """MAPQ arrives keyed by string: '10' must not sort before '2'."""
    spec = category_bar_spec({"2": 1, "10": 5, "60": 9}, x_title="MAPQ")
    assert spec["data"][0]["x"] == [2.0, 10.0, 60.0]


def test_category_bars_can_use_a_log_axis():
    """One value dominates — 524 of 549 reads at MAPQ 60 hides every other bar."""
    spec = category_bar_spec({"0": 3, "60": 524}, x_title="MAPQ", log_y=True)
    assert spec["layout"]["yaxis"]["type"] == "log"


def test_empty_categories_are_refused():
    with pytest.raises(ValueError, match="at least one"):
        category_bar_spec({}, x_title="x")


def test_voltage_draws_running_and_offset_against_run_time():
    series = [{"scan": 1, "t_hours": 0.0, "running_mv": -185.0, "offset_mv": -5.0},
              {"scan": 2, "t_hours": 1.5, "running_mv": -195.0, "offset_mv": -15.0}]
    spec = voltage_spec(series)
    assert [t["name"] for t in spec["data"]] == ["running", "offset"]
    assert spec["data"][0]["x"] == [0.0, 1.5]
    assert spec["data"][0]["y"] == [-185.0, -195.0]


def test_an_empty_voltage_series_is_refused():
    with pytest.raises(ValueError, match="non-empty series"):
        voltage_spec([])


def test_pore_bands_stack_and_the_long_tail_is_summed():
    """Seventeen individually named bands is not a readable figure."""
    series = {"t_minutes": [0, 60], "coverage": [1.0, 0.25],
              "fractions": {"strand": [0.8, 0.7], "pore": [0.1, 0.1],
                            "zero": [0.05, 0.1], "unclassified": [0.05, 0.1]}}
    spec = pore_activity_spec(series)
    names = [t["name"] for t in spec["data"]]
    assert names[:2] == ["strand", "pore"]
    assert names[-1] == "other (2 states)"
    assert spec["data"][-1]["y"] == [0.1, 0.2]
    assert all(t["stackgroup"] == "one" for t in spec["data"])


def test_pore_time_is_converted_to_hours():
    series = {"t_minutes": [0, 120], "coverage": [1.0, 1.0],
              "fractions": {"strand": [0.5, 0.5]}}
    assert pore_activity_spec(series)["data"][0]["x"] == [0.0, 2.0]


def test_partial_minute_coverage_rides_along_rather_than_scaling_the_fractions():
    """Inflating a part-minute to a full one records a process fact as a data fact."""
    series = {"t_minutes": [0, 60], "coverage": [1.0, 0.25],
              "fractions": {"strand": [0.8, 0.8]}}
    spec = pore_activity_spec(series)
    assert spec["data"][0]["y"] == [0.8, 0.8]
    assert spec["data"][0]["customdata"] == [1.0, 0.25]


def test_a_series_without_fractions_is_refused():
    with pytest.raises(ValueError, match="t_minutes and fractions"):
        pore_activity_spec({"t_minutes": [0, 1]})


# --- per-construct distributions ---------------------------------------------------

from mrnaspect.reporting.figspec import per_construct_histogram_spec  # noqa: E402


def _pc(counts, *, q1, median, q3, width=10.0):
    return {"bin_counts": counts, "bin_min": 0.0, "bin_width": width,
            "n_bins": len(counts), "bin_max": width * len(counts),
            "Q1": q1, "median": median, "Q3": q3, "n": sum(counts)}


def test_each_construct_gets_a_panel_with_only_its_own_series_visible():
    spec = per_construct_histogram_spec(
        [("A", _pc([1, 2, 3], q1=10.0, median=15.0, q3=30.0)),
         ("B", _pc([0, 0, 0, 0, 9, 1], q1=40.0, median=45.0, q3=50.0))],
        x_title="nt")
    assert [t["visible"] for t in spec["data"]] == [True, False]
    buttons = spec["layout"]["updatemenus"][0]["buttons"]
    assert [b["label"] for b in buttons] == ["A", "B"]
    assert buttons[1]["args"][0]["visible"] == [False, True]


def test_the_default_window_follows_the_construct():
    """Each construct has its own Tukey range, and keeping the first one's would show
    every later construct through the wrong window — with no error and a plot that looks
    entirely reasonable."""
    spec = per_construct_histogram_spec(
        [("A", _pc([1, 2, 3], q1=10.0, median=15.0, q3=30.0)),
         ("B", _pc([0, 0, 0, 0, 9, 1], q1=40.0, median=45.0, q3=50.0))],
        x_title="nt")
    buttons = spec["layout"]["updatemenus"][0]["buttons"]
    assert buttons[0]["args"][1]["xaxis.range"] == [0.0, 30.0]
    assert buttons[1]["args"][1]["xaxis.range"] == [40.0, 60.0]


def test_the_quantile_markers_follow_the_construct_too():
    spec = per_construct_histogram_spec(
        [("A", _pc([1, 2, 3], q1=10.0, median=15.0, q3=30.0)),
         ("B", _pc([0, 0, 0, 0, 9, 1], q1=40.0, median=45.0, q3=50.0))],
        x_title="nt")
    buttons = spec["layout"]["updatemenus"][0]["buttons"]
    assert [s["x0"] for s in buttons[0]["args"][1]["shapes"]] == [10.0, 15.0, 30.0]
    assert [s["x0"] for s in buttons[1]["args"][1]["shapes"]] == [40.0, 45.0, 50.0]


def test_the_panel_title_names_the_construct():
    """A run-level figure is named by its caption; one behind a selector is not."""
    spec = per_construct_histogram_spec(
        [("QK-01", _pc([1, 2], q1=5.0, median=10.0, q3=15.0))], x_title="nt")
    assert spec["layout"]["title"]["text"].startswith("QK-01<br>")


def test_no_tracks_is_refused():
    with pytest.raises(ValueError, match="at least one track"):
        per_construct_histogram_spec([], x_title="nt")


def test_a_broken_histogram_is_refused_here_too():
    """Built on histogram_spec rather than beside it, so its validation still applies."""
    bad = _pc([1, 2, 3], q1=1.0, median=2.0, q3=3.0)
    bad["n_bins"] = 5
    with pytest.raises(ValueError, match="bin_counts"):
        per_construct_histogram_spec([("A", bad)], x_title="nt")
