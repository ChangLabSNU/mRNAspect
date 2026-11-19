"""Every figure builder, against the inputs that are empty, degenerate or inconsistent.

A render failure is the worst-timed failure in the product: the QC is already computed,
the run is over, and the exception escapes `_emit_report`, which catches only
ModuleNotFoundError. Two of these were real crashes found by sweeping the boundaries
rather than by reading the code, and both are reachable from the product rather than
contrived:

* every read outside the histogram's range — `n > 0` with every bin empty. Read length
  ships `[0, 5000)`, so a 6-10 kb gene-therapy mRNA puts the whole run in `overflow`, and
  trimming the axis to the occupied bins then indexed an empty list.
* the two terminal-position histograms disagreeing on bin count, which a snapshot resumed
  under a different `position_histogram_bins` produces.

The figures are drawn from specs now, so each case is swept through BOTH halves: the
builder must either produce a spec or decline with a ValueError the reporter turns into a
logged skip, and a spec it produced must then draw. A spec that builds and cannot be drawn
is the worse failure of the two — it passes every check in figspec and dies in the render,
which is the moment this file exists to protect.

The assertion is deliberately weak — drawn, or declined. What the figure LOOKS like in
these cases is a separate question; this file only pins that the report still gets written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mrnaspect.reporting import figrender, figspec
from mrnaspect.reporting.reporter import CoAReporter

pytest.importorskip("matplotlib")


def _hist(n: int, counts: list[int], **over) -> dict:
    d = {"n": n, "median": None, "underflow": 0, "overflow": 0,
         "bin_min": 0.0, "bin_max": 100.0, "n_bins": len(counts),
         "bin_width": 100.0 / max(len(counts), 1), "bin_counts": counts}
    d.update(over)
    return d


def _survives(build) -> None:
    """Either declined cleanly, or drawn. Never an exception the report cannot catch."""
    try:
        spec = build()
    except (ValueError, KeyError, TypeError):
        return  # declined; `_interactive_figures` logs and skips
    if spec is None:
        return
    for theme in (figrender.SCREEN, figrender.GRAYSCALE):
        panels = figrender.render_spec(spec, theme=theme)
        for panel in panels:
            assert ET.fromstring(panel.svg).tag == "{http://www.w3.org/2000/svg}svg"


@pytest.mark.parametrize("name,block", [
    ("no observations", _hist(0, [0] * 10)),
    ("every read overflowed", _hist(9, [0] * 10, overflow=9)),
    ("every read underflowed", _hist(9, [0] * 10, underflow=9)),
    ("a single bin", _hist(1, [1])),
    ("one occupied bin in the middle", _hist(1, [0, 0, 1, 0, 0])),
    ("block empty", {}),
    ("counts absent", {"n": 5}),
])
def test_distribution_figure_survives(name, block) -> None:
    _survives(lambda: figspec.histogram_spec([("s", block, figspec.CATEGORICAL[0])],
                                             x_title="x"))


def test_distribution_figure_survives_when_everything_escaped() -> None:
    """The case that crashed: with no bin occupied, trimming the axis to the occupied
    bins indexed an empty list."""
    _survives(lambda: figspec.histogram_spec(
        [("s", _hist(5000, [0] * 500, overflow=5000), figspec.CATEGORICAL[0])],
        x_title="nt"))


@pytest.mark.parametrize("name,block", [
    ("no observations", _hist(0, [0] * 10)),
    ("a single bin", _hist(1, [1])),
    ("block empty", {}),
])
def test_the_per_construct_distribution_survives_the_same_inputs(name, block) -> None:
    """It delegates to histogram_spec, and a wrapper that swallowed the refusal would
    turn a declined figure into a drawn-but-wrong one."""
    _survives(lambda: figspec.per_construct_histogram_spec([("A", block)], x_title="x"))


@pytest.mark.parametrize("name,dist", [
    ("no admitted reads", _hist(0, [0] * 5)),
    ("a single bin", _hist(1, [1])),
    ("block empty", {}),
])
def test_terminal_reach_survives(name, dist) -> None:
    _survives(lambda: figspec.terminal_reach_spec([("A", dist)], terminus="last"))


@pytest.mark.parametrize("name,joint", [
    ("no occupied cells", {"cells": [], "n_bins": 60, "bin_width": 20.0}),
    ("one cell at the origin", {"cells": [[0, 0, 1]], "n_bins": 60, "bin_width": 20.0}),
    ("one cell at the last index",
     {"cells": [[59, 59, 7]], "n_bins": 60, "bin_width": 20.0}),
    ("geometry absent", {"cells": [[0, 0, 1]]}),
])
def test_joint_figure_survives(name, joint) -> None:
    _survives(lambda: figspec.joint_shortfall_spec([("A", joint, 100)]))


@pytest.mark.parametrize("name,series", [
    ("every position uncovered", [None] * 50),
    ("no positions at all", []),
    ("a single position", [0.5]),
])
def test_per_position_figure_survives(name, series) -> None:
    _survives(lambda: figspec.per_position_spec([("A", {
        "substitution_rate": series, "deletion_rate": series, "insertion_rate": series},
        None)]))


@pytest.mark.parametrize("name,cons", [
    ("nothing flagged", {"mismatch_sites": [], "mismatch_threshold": 0.5}),
    ("no threshold recorded", {"mismatch_sites": [{"pos": 0, "mismatch_rate": 0.9,
                                                   "ref_base": "A", "depth": 10,
                                                   "base_counts": {"A": 1, "G": 9}}]}),
    ("no consensus snapshot", None),
])
def test_per_position_flags_survive(name, cons) -> None:
    _survives(lambda: figspec.per_position_spec([("A", {
        "substitution_rate": [0.9], "deletion_rate": [0.0], "insertion_rate": [0.0]},
        cons)]))


def test_a_report_over_degenerate_snapshots_is_still_written(tmp_path: Path) -> None:
    """The end the others defend. Every figure may decline; the report may not."""
    r = CoAReporter(tmp_path, min_reads=1)
    r.update("run_summary", {
        "mapping": {"total": 9, "mapped": 9, "unmapped": 0, "mapping_rate": 1.0},
        "read_length": {"n": 9, "mean": 0.0},
        "read_length_distribution": _hist(9, [0] * 10, overflow=9),
        "quality_score_distribution": {},
    })
    r.update("polya", {"polya_distribution": {}})
    r.update("construct:A", {
        "ref_name": "A", "n_reads": 9,
        "error_hotspot": {"substitution_rate": [None] * 4, "deletion_rate": [None] * 4,
                          "insertion_rate": [None] * 4},
        "read_stats": {"read_length_distribution": {}},
        "terminal_reach": {},
    })
    r.finalize("run_end", force=True)
    assert (tmp_path / "report_run_end.html").exists()
