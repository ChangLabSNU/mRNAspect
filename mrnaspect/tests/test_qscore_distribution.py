"""The qscore distribution — a shape that was computed on every run and then discarded.

`_qs_hist` has existed since the module did: it is where `quality_score`'s Q1, median and
Q3 come from. But only those three scalars were emitted, so every run built a 100-bin
histogram of read quality and threw it away at serialization. Three quantiles cannot be
turned back into a distribution, so no renderer could draw the figure pycoQC leads with.

Same failure as `pore_activity`, which reads per-minute telemetry and emits only run
totals: the module measures the shape, the snapshot keeps the summary.

These guards cover the emission and the two ways a quality histogram misleads quietly —
reads missing the tag silently shrinking the denominator, and out-of-range values folded
into an edge bin instead of being reported as escaped.
"""

import pysam
import pytest

from mrnaspect.analysis.read_stats import DEFAULTS, RunSummaryModule

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 20000}]})


def _read(qs=None, *, seq="ACGT", name="r"):
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = 0
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = seq
    r.cigartuples = [(0, len(seq))]
    if qs is not None:
        r.set_tag("qs", float(qs), value_type="f")
    return r


def _module(reads):
    m = RunSummaryModule()
    for r in reads:
        m.update(r)
    return m.snapshot()


def test_the_distribution_is_emitted_at_all():
    """The regression this file exists for."""
    snap = _module([_read(10.0)])
    assert "quality_score_distribution" in snap, sorted(snap)


def test_geometry_travels_with_the_counts():
    """A renderer must be able to draw the figure from JSON alone."""
    d = _module([_read(10.0)])["quality_score_distribution"]
    for key in ("bin_min", "bin_max", "n_bins", "bin_width", "bin_counts",
                "underflow", "overflow", "n"):
        assert key in d, (key, sorted(d))
    assert len(d["bin_counts"]) == d["n_bins"]


def test_geometry_matches_the_configured_histogram():
    d = _module([_read(10.0)])["quality_score_distribution"]
    cfg = DEFAULTS["histogram"]
    assert (d["bin_min"], d["bin_max"], d["n_bins"]) == (
        cfg["bin_min"], cfg["bin_max"], cfg["n_bins"])


def test_a_score_lands_in_the_bin_that_contains_it():
    d = _module([_read(10.0)])["quality_score_distribution"]
    idx = int((10.0 - d["bin_min"]) / d["bin_width"])
    assert d["bin_counts"][idx] == 1
    assert sum(d["bin_counts"]) == 1


def test_the_histogram_and_the_quantiles_describe_one_population():
    """Q1/median/Q3 are read off this very histogram; they cannot disagree with it."""
    snap = _module([_read(q) for q in (5.0, 10.0, 15.0, 20.0)])
    d = snap["quality_score_distribution"]
    assert d["n"] == 4 == sum(d["bin_counts"]) + d["underflow"] + d["overflow"]
    assert d["median"] == snap["quality_score"]["median"]
    assert d["Q1"] == snap["quality_score"]["Q1"]
    assert d["Q3"] == snap["quality_score"]["Q3"]


def test_reads_without_the_tag_are_counted_not_dropped():
    """`missing_qs` is the denominator honesty: n must not quietly shrink unexplained."""
    snap = _module([_read(10.0), _read(None), _read(None)])
    assert snap["quality_score_distribution"]["n"] == 1
    assert snap["quality_score"]["missing_qs"] == 2


def test_out_of_range_scores_escape_rather_than_pile_on_the_edge():
    """Folding an out-of-range value into the top bin invents a reading inside the axis."""
    hi = DEFAULTS["histogram"]["bin_max"] + 5.0
    d = _module([_read(hi), _read(-1.0)])["quality_score_distribution"]
    assert d["overflow"] == 1
    assert d["underflow"] == 1
    assert sum(d["bin_counts"]) == 0


def test_before_any_read_the_shape_is_empty_not_absent():
    """A run with no reads still has to render: geometry present, quantiles None."""
    d = RunSummaryModule().snapshot()["quality_score_distribution"]
    assert d["n"] == 0
    assert d["median"] is None
    assert len(d["bin_counts"]) == d["n_bins"]


def test_non_primary_records_do_not_reach_the_histogram():
    """Only primary reads carry the run's quality, same gate as every other statistic."""
    sec = _read(10.0, name="s")
    sec.flag = 0x100
    sup = _read(10.0, name="u")
    sup.flag = 0x800
    d = _module([_read(10.0), sec, sup])["quality_score_distribution"]
    assert d["n"] == 1


def test_a_partly_escaped_quantile_is_absent_rather_than_crashing():
    """The reachable half of the same bug: n > 0, but one quantile has no bin.

    Three of four reads sit above bin_max, so Q3 (and the median) fall inside the overflow
    mass and have no in-range value. `n` is 4, so an `if n` guard would call round(None).
    The run must keep its snapshot and simply not claim a number it does not have.
    """
    hi = DEFAULTS["histogram"]["bin_max"] + 5.0
    snap = _module([_read(1.0), _read(hi, name="a"), _read(hi, name="b"),
                    _read(hi, name="c")])
    qs = snap["quality_score"]
    d = snap["quality_score_distribution"]
    assert d["n"] == 4 and d["overflow"] == 3
    assert qs["Q3"] is None
    # The exact mean/min/max come from Welford, which never escapes, so the summary still
    # carries real numbers — the shape is what is unreliable, and it says so.
    assert qs["n"] == 4 and qs["max"] == hi
