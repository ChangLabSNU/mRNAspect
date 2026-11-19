"""Read-length distribution — the data behind the report/GUI read-length figure.

`read_length` (Welford) has always carried mean/variance; it cannot carry a SHAPE. These
guards cover the histogram added alongside it, and in particular the two ways a length
histogram goes wrong quietly: a read counted in one accumulator but not the other, and a
long read folded into the top bin instead of being reported as escaped.
"""

import pysam
import pytest

from mrnaspect.analysis.read_stats import DEFAULTS, RunSummaryModule

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 20000}]})


def _read(seq, *, flag=0, name="r"):
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = 0
    r.query_sequence = seq
    r.cigartuples = [(0, len(seq))] if seq else None
    return r


def _mod(**over):
    return RunSummaryModule(config={"length_histogram": {**DEFAULTS["length_histogram"],
                                                         **over}})


def test_happy_path_lengths_land_in_the_right_bins():
    """10 nt bins: 1144 nt -> bin 114, 1850 nt -> bin 185 — the two construct modes stay
    ~70 bins apart, which is the whole reason for the measured geometry."""
    rs = RunSummaryModule()
    for i in range(3):
        rs.update(_read("A" * 1144, name=f"short{i}"))
    rs.update(_read("A" * 1850, name="long"))

    d = rs.snapshot()["read_length_distribution"]
    assert d["bin_width"] == 10.0 and d["n_bins"] == 500
    assert d["bin_counts"][114] == 3
    assert d["bin_counts"][185] == 1
    assert d["n"] == 4
    assert d["median"] == pytest.approx(1144, abs=10)


def test_counts_reconcile_with_n_and_escapes_are_not_folded():
    """Edge case: a read longer than bin_max is REPORTED as overflow, never dropped into
    the last bin. The measured m1-psi run's longest read is 311,200 nt — if that were
    folded in, the top bar would read as a real population of 4,990-5,000 nt reads."""
    rs = _mod(bin_max=100.0, n_bins=10)
    rs.update(_read("A" * 50))                   # in range -> bin 5
    rs.update(_read("A" * 311200, name="giant"))  # overflow

    d = rs.snapshot()["read_length_distribution"]
    assert d["overflow"] == 1
    assert d["bin_counts"][-1] == 0               # NOT folded into the top bin
    assert sum(d["bin_counts"]) + d["underflow"] + d["overflow"] == d["n"] == 2


def test_histogram_and_welford_see_the_same_reads():
    """The figure and the summary must describe one population. A primary record with no
    SEQ feeds neither (it has no length), and a non-primary record feeds neither."""
    rs = RunSummaryModule()
    rs.update(_read("A" * 1100, name="primary"))
    rs.update(_read(None, name="no_seq"))                      # primary, SEQ absent
    rs.update(_read("A" * 1100, flag=0x100, name="secondary"))  # non-primary

    snap = rs.snapshot()
    assert snap["read_length"]["n"] == snap["read_length_distribution"]["n"] == 1
    assert snap["mapping"]["total"] == 2        # the SEQ-less primary still maps-counts
    assert snap["non_primary"]["secondary"] == 1


def test_geometry_is_present_before_any_read():
    """An interim snapshot is rendered after every POD5, including the first. Geometry with
    no data must still be drawable rather than absent."""
    d = RunSummaryModule().snapshot()["read_length_distribution"]
    assert d["n"] == 0 and d["median"] is None
    assert len(d["bin_counts"]) == 500 and set(d["bin_counts"]) == {0}


# --- what the panel reports -------------------------------------------------

def test_the_integrator_carries_both_a_median_and_an_exact_mean() -> None:
    """The panel shows the median; the mean stays in the JSON.

    Read lengths are right-skewed, so the two genuinely differ and the mean describes a
    population that is not there — this builds a skew big enough that reporting the mean
    as "read length" would be wrong by more than an order of magnitude.
    """
    from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator

    rs = RunSummaryModule()
    for i in range(99):
        rs.update(_read("A" * 1000, name=f"ordinary{i}"))
    rs.update(_read("A" * 300000, name="concatemer"))   # one enormous read

    summary = QCResultIntegrator().integrate({"run_summary": rs.snapshot()})["summary"]
    assert summary["median_read_length"] == pytest.approx(1000, abs=10)
    assert summary["mean_read_length"] > 3900, "the mean should be dragged by the tail"
    # The point of carrying both: they disagree by ~4x on 1 read in 100.
    assert summary["mean_read_length"] > 3 * summary["median_read_length"]


def test_the_median_is_absent_not_fabricated_when_no_read_has_a_length() -> None:
    from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator

    summary = QCResultIntegrator().integrate(
        {"run_summary": RunSummaryModule().snapshot()})["summary"]
    assert summary["median_read_length"] is None


# --- the range follows the reference ----------------------------------------

def test_the_range_is_derived_from_the_reference_when_one_is_known() -> None:
    """A fixed range cannot fit both a 1.2 kb construct and a 9 kb one.

    Measured over 105,146 real reads on 15 constructs: p50/ref_length is 0.93-0.98 on
    every one, so the reference is the right scale, and 3x covers all but 0.0171%.
    """
    assert RunSummaryModule(ref_length=1200).snapshot()[
        "read_length_distribution"]["bin_max"] == 3600.0
    assert RunSummaryModule(ref_length=9000).snapshot()[
        "read_length_distribution"]["bin_max"] == 27000.0
    # No reference to derive from -> the measured fixed default, not a crash.
    assert RunSummaryModule().snapshot()[
        "read_length_distribution"]["bin_max"] == 5000.0


def test_an_explicit_setting_beats_the_derivation() -> None:
    """The rule `resolve_tool` applies to a configured path, applied here: a user who
    writes `length_histogram` means it, and silently resizing it from the reference would
    make their setting look broken rather than overridden."""
    m = RunSummaryModule(config={"length_histogram": {"bin_max": 777.0}}, ref_length=9000)
    assert m.snapshot()["read_length_distribution"]["bin_max"] == 777.0


def test_a_long_construct_is_measured_rather_than_all_overflowing() -> None:
    """The case this exists for. Against the old fixed [0, 5000) a 7.2 kb construct put
    every read in overflow — and the reported median was then 5000.0, a boundary value
    1,716 nt below the shortest read in the run."""
    m = RunSummaryModule(ref_length=7200)
    for i in range(50):
        m.update(_read("A" * 6700, name=f"r{i}"))
    d = m.snapshot()["read_length_distribution"]
    assert d["overflow"] == 0
    assert d["median"] == pytest.approx(6700, abs=d["bin_width"])


def test_a_quantile_inside_the_escaped_mass_is_absent_not_a_boundary() -> None:
    """A1-class. With every read outside the range the median is unknown, not bin_max.

    The boundary value was indistinguishable from a measurement: it reached
    `qc_metrics.summary.median_read_length`, the CoA and the GUI panel looking ordinary.
    """
    m = RunSummaryModule()                      # fixed [0, 5000)
    for i in range(50):
        m.update(_read("A" * 6700, name=f"r{i}"))
    d = m.snapshot()["read_length_distribution"]
    assert d["n"] == 50 and d["overflow"] == 50
    assert d["median"] is None and d["Q1"] is None and d["Q3"] is None
    # The exact mean is unaffected — it never used bins.
    assert m.snapshot()["read_length"]["mean"] == pytest.approx(6700)
