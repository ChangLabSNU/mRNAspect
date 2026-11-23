"""The PDF's figures are vector, not raster (2026-11-12).

Until then figrender drew every panel as a 200 dpi PNG: a 15-construct run's PDF held
194 raster images, and every figure turned to pixels when zoomed or printed large. Now
each panel is SVG, which WeasyPrint draws as vector paths:

- text as glyph outlines, so the page shows the font matplotlib laid the panel out in,
  on any machine (live text would be set by WeasyPrint in whatever font that machine
  resolves, and a different width moves labels into each other);
- a heatmap as one rectangle per filled cell, and its colour bar as vector steps
  (embedded as an image inside the SVG, a heatmap's empty cells drew dark fringes);
- a numeric bar trace as ONE path, not one per bar: WeasyPrint draws SVG node by node in
  Python, and the per-position figure's ~3,700 nodes cost it over a second each.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.reporting import figrender, figspec
from mrnaspect.reporting.reporter import CoAReporter

from .test_print_prose import _print_html
from .test_report_first_page import POLYA, SAMPLE

pytest.importorskip("matplotlib")

SVG_NS = "{http://www.w3.org/2000/svg}"
PRINTED_URL = "data:image/svg+xml;base64,"


def _joint(cells, n_bins=6, width=10.0):
    return {"axes": ["five_prime_short_nt", "three_prime_short_nt"], "n_bins": n_bins,
            "bin_width": width, "n": sum(c[2] for c in cells),
            "cells": [list(c) for c in cells], "out_of_range": 0}


def _specs() -> dict:
    """One spec per kind of mark the renderer draws, the first two built by figspec."""
    hist = {"bin_counts": [1, 2, 3], "bin_min": 0.0, "bin_width": 10.0, "n_bins": 3,
            "bin_max": 30.0, "n": 6}
    many = list(range(1, 1201))
    return {
        "histogram": figspec.histogram_spec([("s", hist, figspec.CATEGORICAL[0])],
                                            x_title="x"),
        "heatmap": figspec.joint_shortfall_spec(
            [("A", _joint([(0, 0, 5), (1, 0, 2), (0, 2, 1), (3, 3, 9)]), 100)]),
        "numeric bars": {"data": [{"type": "bar", "x": many, "y": [0.5] * len(many),
                                   "marker": {"color": figspec.CATEGORICAL[1]},
                                   "name": "b"}], "layout": {}},
        "named bars": {"data": [{"type": "bar", "x": ["QK-01", "HA-WT"], "y": [1, 2],
                                 "marker": {"color": figspec.CATEGORICAL[1]},
                                 "name": "c"}], "layout": {}},
        "lines and markers": {"data": [{"type": "scatter", "mode": "lines+markers",
                                        "x": [0, 1, 2], "y": [1, 3, 2], "name": "s"}],
                              "layout": {}},
    }


@pytest.mark.parametrize("name", sorted(_specs()))
def test_every_panel_is_svg_with_no_image_and_no_live_text(name) -> None:
    panels = figrender.render_spec(_specs()[name], theme=figrender.GRAYSCALE)
    assert panels, "the spec rendered no panels"
    for panel in panels:
        root = ET.fromstring(panel.svg)
        assert root.tag == f"{SVG_NS}svg"
        assert not list(root.iter(f"{SVG_NS}image")), (
            "a raster image inside the SVG is a raster figure in the PDF")
        assert not list(root.iter(f"{SVG_NS}text")), (
            "live text is set by WeasyPrint in the machine's font, not matplotlib's")


def test_a_spec_always_gives_the_same_bytes() -> None:
    """matplotlib salts its SVG ids at random and stamps a date unless told otherwise;
    a report drawn twice from one snapshot should not differ."""
    spec = _specs()["heatmap"]
    first = [p.svg for p in figrender.render_spec(spec, theme=figrender.GRAYSCALE)]
    again = [p.svg for p in figrender.render_spec(spec, theme=figrender.GRAYSCALE)]
    assert first == again


def test_a_heatmap_is_a_rectangle_per_filled_cell_and_none_for_an_empty_one() -> None:
    import matplotlib.pyplot as plt

    z = [[5, None, None], [None, 2, None], [1, None, 9]]
    fig, ax = figrender.draw_panel(
        [{"type": "heatmap", "x": [5, 15, 25], "y": [5, 15, 25], "z": z,
          "colorscale": figspec.SEQUENTIAL_BLUE}], {}, theme=figrender.GRAYSCALE)
    try:
        assert not ax.images, "the heatmap is a raster image again"
        (mesh,) = [c for c in ax.collections if c.get_array() is not None]
        # A path per filled cell, row-major; row 0 is the bottom (plotly's y order). The
        # array keeps the grid, the empty cells masked.
        assert mesh.get_array().compressed().tolist() == [5, 2, 1, 9]
        cells = [tuple(p.get_extents().get_points().ravel()) for p in mesh.get_paths()]
        assert cells == pytest.approx(
            [(0, 0, 10, 10), (10, 10, 20, 20), (0, 20, 10, 30), (20, 20, 30, 30)])
        assert mesh.colorbar is not None
        assert not mesh.colorbar.solids.get_rasterized(), (
            "matplotlib rasterises a colour bar of 50 or more steps unless told not to")
    finally:
        plt.close(fig)


def test_a_numeric_bar_trace_is_one_path_however_many_bars() -> None:
    """The geometry of 1,200 bars, each its own closed sub-path, in one SVG node."""
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.path import Path

    spec = _specs()["numeric bars"]
    fig, ax = figrender.draw_panel(spec["data"], {}, theme=figrender.GRAYSCALE)
    try:
        (coll,) = ax.collections
        (path,) = coll.get_paths()
        assert int((path.codes == Path.CLOSEPOLY).sum()) == 1200
        bars = path.to_polygons()
        assert len(bars) == 1200
        first = np.asarray(bars[0])
        # ax.bar's geometry: centred on x = 1, width 1 (no bargap), from 0 to 0.5.
        assert first[:, 0].min() == pytest.approx(0.5)
        assert first[:, 0].max() == pytest.approx(1.5)
        assert first[:, 1].min() == pytest.approx(0.0)
        assert first[:, 1].max() == pytest.approx(0.5)
    finally:
        plt.close(fig)
    (panel,) = figrender.render_spec(spec, theme=figrender.GRAYSCALE)
    assert panel.svg.count(b"<path") < 100, (
        "the bars are one SVG node each again; WeasyPrint pays per node")


# --------------------------------------------------------------------------------------
# End to end: a real run's PDF
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def printed(tmp_path_factory, variant_bam, qk_ha_ref):
    out = tmp_path_factory.mktemp("vector_pdf")
    reporter = CoAReporter(out, min_reads=1)
    engine = AnalysisEngine(reporter, reference=qk_ha_ref, sample=SAMPLE,
                            module_params=POLYA)
    engine.ingest_bam(variant_bam)
    engine.finalize_all()
    return _print_html(reporter), out / "report_run_end.pdf"


def test_every_printed_figure_is_an_svg(printed) -> None:
    html, _ = printed
    sources = re.findall(r'<img src="([^"]+)"', html)
    assert len(sources) > 5, f"only {len(sources)} figures reached the print HTML"
    assert all(src.startswith(PRINTED_URL) for src in sources), (
        sorted({src[:30] for src in sources}))


def _rasters(reader) -> list[str]:
    """Every image XObject a page can draw, through forms and patterns."""
    found, seen = [], set()

    def walk(resources) -> None:
        resources = resources.get_object() if resources is not None else None
        if not resources:
            return
        for kind in ("/XObject", "/Pattern"):
            group = resources.get(kind)
            group = group.get_object() if group is not None else {}
            for name, ref in group.items():
                key = getattr(ref, "idnum", None) or id(ref)
                if key in seen:
                    continue
                seen.add(key)
                obj = ref.get_object()
                if obj.get("/Subtype") == "/Image":
                    found.append(f"{name} {obj.get('/Width')}x{obj.get('/Height')}")
                walk(obj.get("/Resources"))

    for page in reader.pages:
        walk(page.get("/Resources"))
    return found


def test_the_pdf_holds_no_raster_image(printed) -> None:
    import pypdf  # not importorskip: without the reader this must fail, not skip
    _html, pdf = printed
    found = _rasters(pypdf.PdfReader(str(pdf)))
    assert not found, f"{len(found)} raster images in the PDF, e.g. {found[:3]}"
