"""The consensus flag markers, and the red they turn in print, drawn from a real run.

This path had no end-to-end cover at all. `figrender` has unit tests for the theme and
`figspec` has them for the spec, but every test that built an actual report used
``fixture_bam`` -- four curlcake constructs on which nothing is flagged. With nothing
flagged the marker traces are empty, so a report could have been rendered with the
emphasis layer silently broken and the suite would have been green: the thing
asked for by name (black and white, with only the emphasis in red) was the one thing
never checked on a page.

``variant_bam`` is 270 reads of fully m1Psi-substituted IVT called by a basecaller that
is not, which is why its positions flag without anything being contrived.
"""

from __future__ import annotations

import re

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("jinja2")
pytest.importorskip("matplotlib")


@pytest.fixture(scope="module")
def built(tmp_path_factory, variant_bam, qk_ha_ref):
    out = tmp_path_factory.mktemp("flags")
    rep = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(rep, reference=qk_ha_ref)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    return rep


def _consensus(rep):
    """The per-position figure, which carries the consensus flags since 2026-11-04."""
    figs = [f for f in rep._interactive_figures() if f["id"] == "per-position"]
    assert figs, "no per-position figure was built"
    return figs[0]


def test_the_fixture_actually_flags_positions(built):
    """Stated first, because everything below is vacuous without it -- which is exactly
    the failure the other fixture had."""
    totals = {}
    for name, track in built._construct_snapshots():
        cons = track.get("consensus") or {}
        totals[name] = len(cons.get("mismatch_sites") or [])
    assert sum(totals.values()) > 50, totals
    assert all(n > 0 for n in totals.values()), f"a construct with none: {totals}"


def test_the_spec_marks_the_flags_as_the_point_of_the_figure(built):
    """`meta.emphasis` is how a SPEC says "this mark is what the figure is for" without
    naming a colour. The print theme reads it; plotly ignores it."""
    spec = _consensus(built)["spec"]
    marked = [t for t in spec["data"] if (t.get("meta") or {}).get("emphasis")]
    assert marked, "no trace is flagged as the emphasis"
    drawn = [t for t in marked if t.get("x")]
    assert drawn, "the emphasis traces are all empty, so nothing would be drawn"
    for trace in drawn:
        assert trace["mode"] == "markers"
        assert trace["marker"]["symbol"] in ("circle", "square", "triangle-up")


#: A fill or stroke colour as the SVG writes it, in a style or as an attribute.
_PAINT = re.compile(r'(?:fill|stroke)\s*[:=]\s*"?\s*(#[0-9a-fA-F]{6})')


def _paints(spec) -> list[str]:
    """Every fill and stroke colour the greyscale panels' SVG paints with, one per use.

    The PDF embeds these files as they are (vector, since 2026-11-12), so what they
    declare is what the page shows -- read off the file, not off the theme object.
    """
    from mrnaspect.reporting import figrender

    panels = figrender.render_spec(spec, theme=figrender.GRAYSCALE)
    assert panels, "the spec rendered no panels"
    return [c.lower() for panel in panels for c in _PAINT.findall(panel.svg.decode())]


def _coloured_hues(spec):
    """Every paint on the greyscale panels that kept a hue, as degrees.

    Greyscale is not desaturation: the renderer maps each series colour to a grey, and
    only a trace the spec marked keeps a hue at all. So "black and white with only the
    emphasis in red" is a statement about what the page paints, and this is how to read
    it off the file.
    """
    import colorsys
    from collections import Counter

    hues: Counter = Counter()
    for paint in _paints(spec):
        rgb = [int(paint[i:i + 2], 16) for i in (1, 3, 5)]
        if max(rgb) - min(rgb) > 30:
            hues[colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360] += 1
    return hues


def test_print_is_grey_everywhere_except_the_emphasis(built):
    """The instruction, read off the file the PDF embeds rather than off the theme
    object.

    Measured on this fixture (2026-11-12, the per-position figure's 2 panels, 138
    flagged positions at the default 0.5): 282 of 497 fills and strokes coloured, all
    the print red #c62828 (in PNG pixels, 2026-11-04: 21,915 coloured, 100% red). Not
    "mostly" -- a single series hue surviving the map would show up here as a second
    cluster, which is the failure this is for.
    """
    import colorsys

    from mrnaspect.reporting import figrender, figspec

    hues = _coloured_hues(_consensus(built)["spec"])
    assert hues, "nothing on the page is coloured, so the emphasis did not survive"

    want = figrender.GRAYSCALE(figspec.EMPHASIS).lstrip("#")
    want_hue = colorsys.rgb_to_hsv(*(int(want[i:i + 2], 16) / 255
                                     for i in (0, 2, 4)))[0] * 360

    def off(h):
        return min(abs(h - want_hue), 360 - abs(h - want_hue))

    stray = {h: n for h, n in hues.items() if off(h) > 20}
    total = sum(hues.values())
    assert not stray, (
        f"{sum(stray.values()) / total:.2%} of the coloured pixels are not the print "
        f"emphasis (hue {want_hue:.0f} deg): {sorted(stray)[:5]}")


def test_the_emphasis_is_the_print_red_and_not_the_screen_one(built):
    """Which red: the screen's EMPHASIS is #7f1d1d, too dark to read as an accent on
    paper, so the print theme maps it to its own. Taking the screen value through would
    look almost right and be wrong."""
    from mrnaspect.reporting import figrender, figspec

    assert figrender.GRAYSCALE(figspec.EMPHASIS) != figspec.EMPHASIS, (
        "the print theme is passing the screen emphasis through unmapped")
    # And it is the mapped one that reaches the page: `_coloured_hues` above would pass
    # for either, since both are red, so the value itself is checked here.
    paints = _paints(_consensus(built)["spec"])
    want = figrender.GRAYSCALE(figspec.EMPHASIS).lower()
    assert want in paints, "the print red is painted nowhere"
    assert figspec.EMPHASIS.lower() not in paints, (
        f"the screen red is painted {paints.count(figspec.EMPHASIS.lower())} times")


def test_the_flags_are_one_marked_trace_per_construct(built):
    """One kind of mark, so one trace per construct: the hover, not a colour, says what
    the reads carry. It keeps a symbol, or in print it would be colour alone."""
    spec = _consensus(built)["spec"]
    marked = [t for t in spec["data"] if (t.get("meta") or {}).get("emphasis")]
    constructs = [t for t in spec["data"] if t["name"] == "substitution"]
    assert len(marked) == len(constructs) > 0
    assert {t["name"] for t in marked} == {"flagged"}
    assert all(t["marker"]["symbol"] == "circle" for t in marked)


def test_every_flag_stands_on_its_own_bar(built):
    """On real reads, not a unit fixture. ``pos`` is 0-based and the bars 1-based, and the
    marker layer once plotted ``pos`` as it was, so each call stood over the position
    before its own. The marker is the consensus rate; the bar under it is error_hotspot's
    substitution + deletion at the same position, the same quantity."""
    data = _consensus(built)["spec"]["data"]
    groups = [data[i:i + 4] for i in range(0, len(data), 4)]
    assert all([t["name"] for t in g] == ["substitution", "deletion", "flagged",
                                          "insertion"] for g in groups)
    for sub, dele, flag, _ins in groups:
        assert flag["x"], "a construct with nothing flagged proves nothing here"
        top = {x: s + d for x, s, d in zip(sub["x"], sub["y"], dele["y"])
               if s is not None and d is not None}
        assert all(top[x] == pytest.approx(y, abs=1e-6)
                   for x, y in zip(flag["x"], flag["y"]))
