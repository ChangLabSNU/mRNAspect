"""Read-level run coverage — the check the file-level fraction cannot make.

``fraction_covered`` credits a POD5 its full instrument read count the moment the file is
ingested, so it scores 100 % for a POD5 that was ingested and then lost most of its reads.
That is not a hypothetical: ``run_basecall_align.sh`` once aligned only dorado's first
4000-read chunk per POD5 and dropped ~37 % of the reads silently. These tests pin that the
file-level figure still passes that bug and the read-level one catches it.
"""

from __future__ import annotations

import pytest

from mrnaspect.realtime.sealing import (
    RunCoverage,
    build_run_accounting,
    check_read_coverage,
)


def _coverage(**per_pod5):
    return RunCoverage(reads_per_pod5=dict(per_pod5), source=__import__(
        "pathlib").Path("sequencing_summary.txt"))


# --- the historical bug -----------------------------------------------------

def test_catches_the_4000_read_chunk_loss():
    """A POD5 with 7000 reads of which only the first 4000-read chunk was aligned."""
    cov = _coverage(**{"a.pod5": 7000, "b.pod5": 7000})
    rep = check_read_coverage(cov, {"a.pod5": 4000, "b.pod5": 4000})

    assert rep["status"] == "measured"
    assert rep["n_shortfall_pod5s"] == 2
    assert rep["overall_ratio"] == pytest.approx(8000 / 14000)
    assert {s["pod5"] for s in rep["shortfalls"]} == {"a.pod5", "b.pod5"}


def test_file_level_fraction_is_blind_to_that_same_loss():
    """The contrast that justifies the new check: same data, file-level says 100 %."""
    cov = _coverage(**{"a.pod5": 7000, "b.pod5": 7000})
    acc = build_run_accounting(
        observed=["a.pod5", "b.pod5"], ingested=["a.pod5", "b.pod5"],
        failed=[], unstable=[], coverage=cov,
        primary_by_pod5={"a.pod5": 4000, "b.pod5": 4000})

    assert acc["coverage"]["fraction_of_run_reads"] == 1.0        # blind
    assert acc["read_coverage"]["n_shortfall_pod5s"] == 2         # not blind


# --- healthy runs -----------------------------------------------------------

def test_full_yield_reports_no_shortfall():
    cov = _coverage(**{"a.pod5": 5000})
    rep = check_read_coverage(cov, {"a.pod5": 5000})
    assert rep["n_shortfall_pod5s"] == 0 and rep["overall_ratio"] == 1.0


def test_small_loss_inside_tolerance_is_not_flagged():
    # A handful of guard-rejected / unbasecallable reads is a real loss but not a bug.
    cov = _coverage(**{"a.pod5": 1000})
    rep = check_read_coverage(cov, {"a.pod5": 990})
    assert rep["n_shortfall_pod5s"] == 0
    assert rep["per_pod5"]["a.pod5"]["ratio"] == 0.99


def test_tolerance_is_configurable():
    cov = _coverage(**{"a.pod5": 1000})
    assert check_read_coverage(cov, {"a.pod5": 990},
                               min_ratio=0.999)["n_shortfall_pod5s"] == 1


# --- honest degradation -----------------------------------------------------

def test_no_summary_is_not_measured_not_zero():
    rep = check_read_coverage(None, {"a.pod5": 10})
    assert rep["status"] == "not_measured"
    assert "overall_ratio" not in rep      # never a fabricated 0.0


def test_pod5_absent_from_summary_is_named_not_averaged():
    # A file the instrument never recorded is a different anomaly from a read shortfall,
    # so it must not silently drag the overall ratio around.
    cov = _coverage(**{"a.pod5": 100})
    rep = check_read_coverage(cov, {"a.pod5": 100, "ghost.pod5": 50})
    assert rep["per_pod5"]["ghost.pod5"]["note"] == "not in sequencing_summary"
    assert rep["per_pod5"]["ghost.pod5"]["ratio"] is None
    assert rep["overall_ratio"] == 1.0     # ghost excluded from the denominator
    assert rep["n_shortfall_pod5s"] == 0


def test_a_summary_that_names_none_of_the_inputs_is_not_measured():
    """BAMs basecalled outside MinKNOW and put in a run folder: the summary has "-" for
    every read's BAM, so no input has an instrument count. It read `measured` with an
    expected total of 0 and no ratio."""
    cov = _coverage(**{"other_0.bam": 100})
    rep = check_read_coverage(cov, {"mine_0.bam": 60, "mine_1.bam": 40})
    assert rep["status"] == "not_measured"
    assert "none of this run's 2 input file(s)" in rep["reason"]
    assert "sequencing_summary.txt" in rep["reason"]
    assert "overall_ratio" not in rep      # never a fabricated 0.0
    assert rep["per_pod5"]["mine_0.bam"] == {
        "ours": 60, "instrument": None, "ratio": None, "note": "not in sequencing_summary"}
    # An empty summary (every row "-") is the same case.
    assert check_read_coverage(_coverage(), {"mine_0.bam": 60})["status"] == "not_measured"
    # No input yet is not this case: nothing is missing from the summary.
    assert check_read_coverage(cov, {})["status"] == "measured"


def test_zero_read_pod5_does_not_divide_by_zero():
    cov = _coverage(**{"empty.pod5": 0})
    rep = check_read_coverage(cov, {"empty.pod5": 0})
    assert rep["per_pod5"]["empty.pod5"]["ratio"] is None
    assert rep["n_shortfall_pod5s"] == 0


def test_bad_tolerance_fails_loud():
    with pytest.raises(ValueError, match="min_ratio"):
        check_read_coverage(_coverage(**{"a.pod5": 1}), {"a.pod5": 1}, min_ratio=0)
