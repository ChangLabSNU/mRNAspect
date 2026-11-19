"""Item 11 regression — richer distribution output + honest consensus method text.

  * HistogramAccumulator.to_dict now carries the bin geometry (bin_min/max/width/n_bins) and
    the per-bin counts, so a distribution figure is reconstructable from JSON alone. The counts
    plus under/overflow reconstruct n exactly (no bin double-counts an out-of-range value).
  * The CoA identity row's method text (now "Per-base identity") is derived from the mode's actual U handling
    instead of a fixed "(U positions excluded)" that was wrong for unmodified / m1psi_ft.
"""

from __future__ import annotations

from mrnaspect.analysis.streaming_interface import HistogramAccumulator
from mrnaspect.reporting.reporter import CoAReporter


def test_histogram_to_dict_carries_bins_and_geometry() -> None:
    h = HistogramAccumulator(bin_min=0.0, bin_max=10.0, n_bins=10)  # bin_width 1.0
    for v in (0.5, 0.7, 3.2, -1.0, 99.0):   # bin0 x2, bin3 x1, 1 underflow, 1 overflow
        h.update(v)
    d = h.to_dict()

    assert (d["bin_min"], d["bin_max"], d["n_bins"], d["bin_width"]) == (0.0, 10.0, 10, 1.0)
    assert len(d["bin_counts"]) == 10
    assert d["bin_counts"][0] == 2          # 0.5, 0.7
    assert d["bin_counts"][3] == 1          # 3.2
    assert d["underflow"] == 1 and d["overflow"] == 1
    # In-range bin counts + escaping mass reconstruct n exactly — an out-of-range value is
    # counted in under/overflow, NOT folded into a boundary bin.
    assert sum(d["bin_counts"]) + d["underflow"] + d["overflow"] == d["n"] == 5


def test_histogram_empty_still_reports_geometry() -> None:
    d = HistogramAccumulator(bin_min=0.0, bin_max=5.0, n_bins=5).to_dict()
    assert d["n"] == 0 and d["Q1"] is None          # no fabricated quantile
    assert d["bin_counts"] == [0, 0, 0, 0, 0]        # geometry present even with no data
    assert d["bin_width"] == 1.0


def test_identity_method_text_names_its_scope() -> None:
    """Scope, not a U-mask: every position is scored now, so the text says so once."""
    text = CoAReporter._identity_method({"n_constructs": 4, "scored_depth": 9995})
    assert "4 constructs" in text
    assert "9,995 base observations" in text
    assert "every position scored" in text

    empty = CoAReporter._identity_method({"n_constructs": 0})
    assert "no construct tracked" in empty
