"""What the vendored plotly bundle can draw, versus what the specs ask it to.

A trace type the bundle does not carry does not raise. plotly logs a warning to the
browser console and draws an empty panel — so the failure surfaces as "the chart is
blank" on someone else's machine, with nothing in the QC output to explain it.

That is not hypothetical: the Manhattan was written with the WebGL scatter variant, which
`plotly-cartesian` cannot render. The cartesian bundle has no `createREGL` factory (the
full build has two), so there is no WebGL context for it to draw into. Caught by probing
the file rather than by reading the docs, which is the house rule for tool behaviour.

Neither `cargo` nor `node` exists on this host, so the browser half is never compiled
here. These checks are the substitute: they hold the Python side to what the pinned file
can actually do.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from mrnaspect.reporting import figspec
from mrnaspect.reporting.figspec import (
    PLOTLY_BUNDLE, PLOTLY_BUNDLE_SHA256, SUPPORTED_TRACE_TYPES,
)

ASSETS = Path(figspec.__file__).resolve().parent / "assets"


def _d(counts, **kw):
    n = len(counts)
    return {"n": sum(counts), "bin_min": 0.0, "bin_max": 10.0 * n, "n_bins": n,
            "bin_width": 10.0, "bin_counts": list(counts), "underflow": 0,
            "overflow": 0, "Q1": kw.get("q1"), "median": kw.get("median"),
            "Q3": kw.get("q3")}


def _every_spec() -> dict[str, dict]:
    """One built figure of every kind, so the check cannot miss a builder."""
    err = {"substitution_rate": [0.1, 0.2, None], "deletion_rate": [0.0, 0.1, 0.0],
           "insertion_rate": [0.0, 0.0, 0.1]}
    joint = {"axes": ["five_prime_short_nt", "three_prime_short_nt"], "n_bins": 4,
             "bin_width": 10.0, "n": 3, "cells": [[0, 0, 3]], "out_of_range": 0}
    return {
        "per_position": figspec.per_position_spec([("A", err, {
            "mismatch_sites": [{"pos": 1, "mismatch_rate": 0.3, "ref_base": "A",
                                "depth": 10, "base_counts": {"A": 7, "G": 3}}],
            "mismatch_threshold": 0.5})]),
        # A palette colour, as every real caller passes. "#000" stood here, and the
        # colour check below only read six-digit hex, so it never saw it; the stated
        # fill (rgba) is read now and would have been flagged.
        "histogram": figspec.histogram_spec(
            [("a", _d([1, 2, 3]), figspec.CATEGORICAL[0])], x_title="x"),
        "per_construct_histogram": figspec.per_construct_histogram_spec(
            [("A", _d([1, 2, 3])), ("B", _d([3, 2, 1]))], x_title="x"),
        "terminal_reach": figspec.terminal_reach_spec([("A", _d([1, 2, 9]))],
                                                      terminus="last"),
        "joint_shortfall": figspec.joint_shortfall_spec([("A", joint, 100)]),
        "ranked_bar": figspec.ranked_bar_spec([("a", 1.0)], x_title="x"),
        "category_bar": figspec.category_bar_spec({"1": 2}, x_title="x"),
        "voltage": figspec.voltage_spec(
            [{"scan": 1, "t_hours": 0.0, "running_mv": -1.0, "offset_mv": 0.0}]),
        "pore_activity": figspec.pore_activity_spec(
            {"t_minutes": [0, 60], "coverage": [1.0, 1.0],
             "fractions": {"strand": [0.9, 0.8]}}),
    }


def test_every_public_builder_is_exercised_here():
    """A builder added without a case here would never be checked against the bundle."""
    builders = {n for n, o in vars(figspec).items()
                if n.endswith("_spec") and inspect.isfunction(o)}
    covered = {f"{k}_spec" for k in _every_spec()}
    assert builders == covered, (
        f"not checked against the bundle: {sorted(builders - covered)}; "
        f"no such builder: {sorted(covered - builders)}")


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_specs_only_use_trace_types_the_bundle_can_draw(name):
    types = {t.get("type") for t in _every_spec()[name]["data"]}
    assert types <= SUPPORTED_TRACE_TYPES, (
        f"{name} uses {sorted(types - SUPPORTED_TRACE_TYPES)}, which "
        f"{PLOTLY_BUNDLE} cannot render — it draws an empty panel and logs only to the "
        f"browser console.")


def test_the_vendored_bundle_is_present():
    assert (ASSETS / PLOTLY_BUNDLE).is_file(), (
        f"{PLOTLY_BUNDLE} is missing from {ASSETS}. The report embeds it, so without it "
        f"every interactive figure is blank.")


def test_the_vendored_bundle_is_the_file_that_was_checked():
    """A swapped or truncated copy is otherwise invisible until a chart stops working."""
    digest = hashlib.sha256((ASSETS / PLOTLY_BUNDLE).read_bytes()).hexdigest()
    assert digest == PLOTLY_BUNDLE_SHA256


def test_the_bundle_really_lacks_webgl_so_the_restriction_is_not_folklore():
    """Pin the reason, not just the rule.

    If a later bundle does carry the factory, this fails and whoever sees it can widen
    SUPPORTED_TRACE_TYPES deliberately rather than guessing.
    """
    text = (ASSETS / PLOTLY_BUNDLE).read_text(encoding="utf-8", errors="replace")
    assert "createREGL" not in text
    assert "scattergl" not in SUPPORTED_TRACE_TYPES


# --- the GUI's copy -------------------------------------------------------------

GUI_PUBLIC = Path(figspec.__file__).resolve().parents[2] / "gui" / "public"


def test_the_gui_serves_the_same_bundle_byte_for_byte():
    """Two copies of a renderer are two renderers unless something says otherwise.

    Core embeds the bundle in the report; the GUI serves it from `public/`. A drift
    between them means the two surfaces draw the same specs with different code, and the
    difference would show up as one chart behaving oddly in one place.
    """
    gui = GUI_PUBLIC / PLOTLY_BUNDLE
    assert gui.is_file(), f"{PLOTLY_BUNDLE} is missing from {GUI_PUBLIC}"
    assert hashlib.sha256(gui.read_bytes()).hexdigest() == PLOTLY_BUNDLE_SHA256


def test_the_gui_page_loads_the_pinned_bundle_by_name():
    """A stale filename in the page is a blank charts panel and nothing else."""
    html = (GUI_PUBLIC.parent / "index.html").read_text(encoding="utf-8")
    assert f'src="/{PLOTLY_BUNDLE}"' in html, (
        f"gui/index.html does not load /{PLOTLY_BUNDLE}")


def test_the_bundle_is_not_deferred_behind_the_module_that_uses_it():
    """main.ts is a deferred module; Plotly has to exist before it runs."""
    html = (GUI_PUBLIC.parent / "index.html").read_text(encoding="utf-8")
    bundle_at = html.index(f'src="/{PLOTLY_BUNDLE}"')
    main_at = html.index('src="/src/main.ts"')
    assert bundle_at < main_at
    tag = html[html.rindex("<script", 0, bundle_at):bundle_at]
    assert "defer" not in tag and "async" not in tag, tag


def test_the_bundle_is_declared_as_package_data():
    """An installed copy must carry it, and only a packaged install can show that it does not.

    `_PLOTLY_PATH.read_text()` runs on the terminal render. If setuptools does not ship
    `assets/`, the wheel is missing the file and the run fails at the very last step —
    after the sequencing, the basecalling and the whole analysis. In the repo everything
    works, because the file is simply there.
    """
    import re
    import tomllib

    pyproject = Path(figspec.__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    patterns = data["tool"]["setuptools"]["package-data"]["mrnaspect.reporting"]
    # Translate the glob the way setuptools does, rather than trusting a literal match:
    # "assets/*.js" and "assets/plotly-cartesian-3.7.0.min.js" both ship the file.
    rel = f"assets/{PLOTLY_BUNDLE}"
    assert any(re.fullmatch(re.escape(p).replace(r"\*", "[^/]*"), rel) for p in patterns), (
        f"{rel} matches none of {patterns}; an installed mrnaspect would have no renderer.")


# --- presentation, applied to every figure ----------------------------------------

from mrnaspect.reporting.figspec import FONT_FAMILY  # noqa: E402


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_every_figure_uses_the_shared_font(name):
    """The GUI's CSS and the report's screen view both take figspec.FONT_FAMILY.

    A chart that hard-codes a face, or leaves plotly's own default, looks foreign in one
    of the two places it appears.
    """
    font = _every_spec()[name]["layout"].get("font") or {}
    assert font.get("family") == FONT_FAMILY, (name, font)


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_only_a_figure_with_a_construct_selector_draws_its_own_name(name):
    """The HTML caption already names every figure.

    Repeating it inside the plot at title weight was half of why the titles read as too
    long. The other half was detail — positions, medians, escaped counts — living on the
    title line; that is now a smaller second line. A figure whose subject changes with a
    dropdown is the exception: its name cannot come from a static caption.
    """
    spec = _every_spec()[name]
    has_selector = bool(spec["layout"].get("updatemenus"))
    text = (spec["layout"].get("title") or {}).get("text", "")
    first_line = text.split("<br>")[0]
    if has_selector:
        assert first_line and not first_line.startswith("<span"), (name, text)
    else:
        assert not first_line or first_line.startswith("<span"), (name, text)


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_no_figure_labels_a_quantile_on_the_axis(name):
    """Axis annotations sit at the value's x and collide as soon as the range narrows.

    Which is exactly when a reader is looking closely. The numbers live in the subtitle.
    """
    annotations = _every_spec()[name]["layout"].get("annotations") or []
    assert not any("Q1" in (a.get("text") or "") or "median" in (a.get("text") or "")
                   for a in annotations), (name, annotations)


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_a_log_axis_is_labelled_one_decade_at_a_time(name):
    """plotly's default log labels are SI prefixes — "100µ" for a rate, and worse."""
    for axis in ("xaxis", "yaxis"):
        cfg = _every_spec()[name]["layout"].get(axis) or {}
        if cfg.get("type") == "log":
            assert cfg.get("dtick") == 1, (name, axis, cfg)
            assert cfg.get("exponentformat") == "none", (name, axis, cfg)


def test_the_stacked_error_figure_is_not_on_a_log_axis():
    """Stacked segments do not add to their total on a log axis.

    The picture would be arithmetically false, which is a worse failure than hard to read.
    """
    spec = _every_spec()["per_position"]
    assert spec["layout"]["barmode"] == "stack"
    assert (spec["layout"]["yaxis"] or {}).get("type") in (None, "linear")


# --- colour ------------------------------------------------------------------------

def _colours(node) -> set:
    """Every colour anywhere in a spec, however deeply nested: a hex, and an rgba() as the
    hex it is, so a stated fill is held to the palette like the line it sits under."""
    out = set()
    if isinstance(node, str):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", node):
            out.add(node.lower())
        m = re.fullmatch(r"rgba\((\d+), (\d+), (\d+), [\d.]+\)", node)
        if m:
            out.add("#%02x%02x%02x" % tuple(int(v) for v in m.groups()))
    elif isinstance(node, dict):
        for v in node.values():
            out |= _colours(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            out |= _colours(v)
    return out


def _allowed() -> set:
    allowed = {c.lower() for c in figspec.CATEGORICAL}
    allowed |= {figspec.INK_PRIMARY, figspec.INK_SECONDARY, figspec.INK_MUTED,
                figspec.GRIDLINE, figspec.AXIS_LINE}
    # The status colour, judged by the criteria for a status colour rather than for a
    # fifth series: contrast 10.0 on white, and never told apart by colour alone — it
    # carries its own marker shape, its own place in the plot and its own legend entry.
    # Since P1 (2026-10-31) it also clears the 15-deltaE normal-vision floor against
    # every series (15.3 against the plum, its nearest; test_palette_separation holds
    # it). Against the raspberry #a8325f before, no red did. In the print theme the
    # series are grey and the red is the only chroma on the page.
    allowed |= {figspec.EMPHASIS}
    allowed |= {step[1].lower() for step in figspec.SEQUENTIAL_BLUE}
    return {c.lower() for c in allowed}


@pytest.mark.parametrize("name", sorted(_every_spec()))
def test_no_figure_invents_a_colour(name):
    """Colour comes from the tokens or not at all.

    The palette is validated as a SET — lightness band, chroma floor, colourblind
    separation, contrast — so a hue added straight into a chart is a hue nobody checked.
    Three candidates failed that validator while looking perfectly reasonable: the GUI
    palette entire, a darkened amber, and the heatmap's original multi-hue ramp.
    """
    import re as _re
    globals().setdefault("re", _re)
    stray = _colours(_every_spec()[name]) - _allowed()
    assert not stray, (
        f"{name} uses {sorted(stray)}, which is not in CATEGORICAL, the ink tokens or "
        f"the sequential ramp. Add it to the palette and re-run validate_palette.py, or "
        f"use a token.")


def test_every_filled_series_states_the_one_fill_rule():
    """A filled distribution is its line colour at FILL_ALPHA under a SERIES_LINE_PX line,
    stated in the spec so the screen and the PDF read the same two values. Unstated,
    plotly fills at the line colour's 0.5 and figrender drew 0.25 (2026-10-31)."""
    checked = 0
    for name, spec in _every_spec().items():
        for tr in spec["data"]:
            if not tr.get("fill") or tr.get("stackgroup"):
                continue
            if tr.get("fillcolor") == figspec.GRIDLINE:
                continue   # the consensus figure's grey band: page furniture, not a series
            line = tr.get("line") or {}
            checked += 1
            assert tr.get("fillcolor") == figspec.fill_of(line["color"]), (name, tr)
            assert line.get("width") == figspec.SERIES_LINE_PX, (name, tr)
    assert checked >= 3, f"only {checked} filled series found; the scan is wrong"


def test_every_bar_states_the_one_fill_rule():
    """A bar is filled and outlined as a distribution is. The MAPQ bar was a solid block of
    the line colour beside translucent histograms, in print as on screen
    (2026-10-31). The per-position profile is exempt; see the next test."""
    checked = 0
    for name, spec in _every_spec().items():
        if name == "per_position":
            continue
        for tr in spec["data"]:
            if tr.get("type") != "bar":
                continue
            marker = tr.get("marker") or {}
            line = marker.get("line") or {}
            checked += 1
            assert marker.get("color") == figspec.fill_of(line.get("color")), (name, tr)
            assert line.get("width") == figspec.SERIES_LINE_PX, (name, tr)
    assert checked >= 2, f"only {checked} bars found; the scan is wrong"


def test_the_per_position_bars_stay_solid():
    """A bar a pixel or two wide is all edge: outlined, it would be nothing but outline."""
    bars = [t for t in _every_spec()["per_position"]["data"] if t.get("type") == "bar"]
    assert bars, "no per-position bars; the exemption is guarding nothing"
    for tr in bars:
        assert tr["marker"]["color"] in figspec.CATEGORICAL, tr
        assert tr["marker"]["line"]["width"] == 0, tr


def test_the_categorical_order_is_fixed_and_not_cycled():
    """A series keeps its hue no matter how many others are shown.

    Colour that follows rank instead of identity repaints the survivors whenever a
    filter changes the series count, and the reader reads that as the data moving.
    """
    assert len(set(figspec.CATEGORICAL)) == len(figspec.CATEGORICAL)
    parts = (*figspec.MISMATCH_PARTS, figspec.INSERTION)
    assert [c for _, _, c in parts] == list(figspec.CATEGORICAL[:3])


def test_the_heatmap_ramp_is_one_hue_light_to_dark():
    """A multi-hue ramp reads as categories, not as more-and-less."""
    steps = figspec.SEQUENTIAL_BLUE
    assert [p for p, _ in steps] == sorted(p for p, _ in steps)
    # Monotonically darkening: luminance must fall from first step to last.
    def lum(hexstr):
        r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    lums = [lum(c) for _, c in steps]
    assert lums == sorted(lums, reverse=True), lums
