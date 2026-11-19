"""The two per-construct ratios the GUI Overview draws.

The shell picks values and computes none, so a ratio it shows has to arrive from Core:

* ``full_length.full_length_fraction_lower``: a construct's full-length reads over ALL
  its reads, beside ``full_length_fraction`` over its admitted reads. The pair is the
  construct's range, the per-construct form of the run-level envelope.
* ``share_of_reads``: a construct's reads over the run's reads, mapped or not, as the
  Constructs figure draws it. Until 2026-11-16 the field was ``share_of_mapped``, so the
  figure and the table under it gave one construct two shares.

The real-BAM test points the fixture BAM at a reference holding two of its four
contigs, so the other two land off-reference and the shares have a remainder to add up
with.
"""

from __future__ import annotations

from pathlib import Path

import pysam
import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.snapshot_keys import construct_key


def _reach(hit: int, admitted: int, *, excluded: int = 0) -> dict:
    """A terminal_reach snapshot with the fields the integrator reads."""
    return {
        "full_length_count": hit,
        "n_admitted": admitted,
        "full_length_fraction": round(hit / admitted, 6) if admitted else None,
        "full_length_5p_max_short_nt": 50,
        "denominator": "reads_assigned_to_construct_admitted",
        "excluded": {"reverse_strand": 0, "split_mapped_sa": excluded,
                     "split_child_pi": 0, "no_alignment_end": 0},
    }


def _bundle(constructs: dict[str, tuple[int, dict]], *, mapped, off_reference=0,
            unmapped=0):
    """``constructs`` maps a name to ``(n_reads, terminal_reach snapshot)``."""
    b: dict = {"run_summary": {"mapping": {"total": mapped + unmapped, "mapped": mapped,
                                           "unmapped": unmapped}},
               "unassigned": {"n_reads": off_reference + unmapped, "unmapped": unmapped,
                              "off_reference": off_reference}}
    for name, (n_reads, tr) in constructs.items():
        b[construct_key(name)] = {"n_reads": n_reads, "terminal_reach": tr}
    return b


# --- full_length_fraction_lower -------------------------------------------------

def test_the_lower_end_divides_by_every_read_the_construct_was_assigned() -> None:
    """80 full-length of 100 admitted, 5 more excluded: 80/105 low, 80/100 high."""
    res = QCResultIntegrator().integrate(
        _bundle({"A": (105, _reach(80, 100, excluded=5))}, mapped=105))
    fl = res["constructs"]["A"]["full_length"]
    assert fl["status"] == "measured"
    assert fl["full_length_fraction"] == 0.8
    assert fl["full_length_fraction_lower"] == round(80 / 105, 6)
    assert fl["full_length_fraction_lower"] < fl["full_length_fraction"]
    assert fl["denominator_lower"] == "all_reads_assigned_to_construct"


def test_with_nothing_excluded_the_two_ends_meet() -> None:
    res = QCResultIntegrator().integrate(
        _bundle({"A": (50, _reach(20, 50))}, mapped=50))
    fl = res["constructs"]["A"]["full_length"]
    assert fl["full_length_fraction_lower"] == fl["full_length_fraction"] == 0.4


def test_a_missing_read_count_gives_no_lower_end_not_a_zero() -> None:
    res = QCResultIntegrator().integrate(
        _bundle({"A": (None, _reach(20, 50))}, mapped=50))
    fl = res["constructs"]["A"]["full_length"]
    assert fl["status"] == "measured"
    assert fl["full_length_fraction_lower"] is None


def test_no_admitted_read_withholds_both_ends() -> None:
    """Every read excluded: the fraction is no_data, and so is its lower end."""
    res = QCResultIntegrator().integrate(
        _bundle({"A": (4, _reach(0, 0, excluded=4))}, mapped=4))
    fl = res["constructs"]["A"]["full_length"]
    assert fl["status"] == "no_data"
    assert "full_length_fraction" not in fl
    assert "full_length_fraction_lower" not in fl


def test_more_reads_seen_than_assigned_withholds_the_lower_end_loudly(caplog) -> None:
    """A lower end above the fraction is impossible, so it is never printed. It is
    withheld with a warning rather than raised: the integrator reports a broken
    snapshot (as `construct_accounting` does) instead of losing the whole result."""
    with caplog.at_level("WARNING"):
        res = QCResultIntegrator().integrate(
            _bundle({"A": (90, _reach(80, 100))}, mapped=100))
    fl = res["constructs"]["A"]["full_length"]
    assert fl["full_length_fraction_lower"] is None
    assert fl["full_length_fraction"] == 0.8      # the admitted-read value still stands
    assert "full_length_fraction_lower withheld" in caplog.text


# --- share_of_reads --------------------------------------------------------------

def test_the_share_is_over_every_read_and_rounded_like_its_neighbours() -> None:
    """1 and 2 reads of the run's 4, not of the 3 that mapped; the unassigned reads take
    the rest, so the shares add to 1."""
    res = QCResultIntegrator().integrate(_bundle(
        {"A": (1, _reach(1, 1)), "B": (2, _reach(2, 2))}, mapped=3, unmapped=1))
    assert res["constructs"]["A"]["share_of_reads"] == 0.25
    assert res["constructs"]["B"]["share_of_reads"] == 0.5
    assert res["unassigned"]["share_of_reads"] == 0.25


def test_a_share_is_rounded_to_six_places() -> None:
    res = QCResultIntegrator().integrate(_bundle(
        {"A": (1, _reach(1, 1)), "B": (2, _reach(2, 2))}, mapped=3))
    assert res["constructs"]["A"]["share_of_reads"] == round(1 / 3, 6)
    assert res["constructs"]["B"]["share_of_reads"] == round(2 / 3, 6)
    assert res["unassigned"]["share_of_reads"] == 0.0


def test_one_share_is_written_not_two() -> None:
    """The share of the mapped reads is gone from the result: a reader of qc_result.json
    must not find a second number under a name the app no longer shows."""
    res = QCResultIntegrator().integrate(_bundle({"A": (1, _reach(1, 1))}, mapped=1))
    assert "share_of_mapped" not in res["constructs"]["A"]
    assert "off_reference_share_of_mapped" not in res["unassigned"]


@pytest.mark.parametrize("total", [0, None])
def test_a_run_with_no_reads_gives_no_share(total) -> None:
    b = _bundle({"A": (0, _reach(0, 0))}, mapped=0)
    b["run_summary"]["mapping"]["total"] = total
    res = QCResultIntegrator().integrate(b)
    assert res["constructs"]["A"]["share_of_reads"] is None
    assert res["unassigned"]["share_of_reads"] is None


def test_a_construct_with_more_reads_than_the_run_has_no_share(caplog) -> None:
    """A share above 1 is impossible; withheld with a warning, never printed."""
    with caplog.at_level("WARNING"):
        res = QCResultIntegrator().integrate(
            _bundle({"A": (11, _reach(5, 11))}, mapped=10))
    assert res["constructs"]["A"]["share_of_reads"] is None
    assert "share_of_reads withheld" in caplog.text


# --- both, on a real BAM ---------------------------------------------------------

def _two_contig_reference(src: Path, dst: Path) -> list[str]:
    """Copy two of ``src``'s contigs to ``dst``; reads on the rest go off-reference."""
    with pysam.FastaFile(str(src)) as fa:
        kept = list(fa.references[:2])
        dst.write_text("".join(f">{name}\n{fa.fetch(name)}\n" for name in kept))
    pysam.faidx(str(dst))
    return kept


def test_on_the_fixture_bam_both_fields_hold_and_the_shares_add_to_one(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    ref = tmp_path / "two_contigs.fasta"
    kept = _two_contig_reference(curlcake_ref, ref)
    rep = CoAReporter(tmp_path / "out", min_reads=1)
    engine = AnalysisEngine(rep, reference=ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    res = QCResultIntegrator().integrate(dict(rep._snapshots))

    assert sorted(res["constructs"]) == sorted(kept)
    un = res["unassigned"]
    # The identity holds exactly only when the per-read guard rejected nothing: a read
    # rejected after run_summary counted it is mapped but in no bucket.
    assert res["construct_accounting"]["unprocessable"] == 0
    assert un["off_reference"] > 0, "fixture no longer exercises off-reference reads"

    total = res["summary"]["total_reads"]
    shares = [c["share_of_reads"] for c in res["constructs"].values()]
    assert all(s is not None and 0 < s <= 1 for s in shares)
    # Core states the unassigned share itself, so the GUI computes no ratio.
    assert un["share_of_reads"] == round(un["n_reads"] / total, 6)
    # Each share is rounded to 6 dp, so the sum may be off by 5e-7 per term.
    assert sum(shares) + un["share_of_reads"] == pytest.approx(1.0, abs=2e-6)

    for name, c in res["constructs"].items():
        fl = c["full_length"]
        assert fl["status"] == "measured", name
        assert fl["full_length_fraction_lower"] is not None, name
        assert fl["full_length_fraction_lower"] <= fl["full_length_fraction"], name
        assert fl["full_length_fraction_lower"] == round(
            fl["full_length_count"] / c["n_reads"], 6)
