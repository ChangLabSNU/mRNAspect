"""Identity's breakdown: what the rest of the scored depth is.

Decided 2026-11-04. Substitution and deletion are counted over identity's
own positions (depth > 0, a reference base other than N) with identity's own weighting
(summed depth), so identity + substitution + deletion is exactly 100%. Insertions are not
in depth -- an inserted base has no reference position -- so they are reported beside it,
as events per 100 scored bases. This replaces the error profile's per-position means in
qc_metrics, which weighed every position alike and so could not add up with identity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mrnaspect.analysis.engine import AnalysisEngine
from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule, pooled_identity
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.analysis.shared_pileup import _COL_DEL, _COL_INS, SharedPileup
from mrnaspect.reporting.reporter import CoAReporter


def _pileup() -> SharedPileup:
    """Ref 'ACGTN', depth 10 at every position, 3 insertion events anchored at pos1.

    pos0 ref A: 10 A                 agree 10
    pos1 ref C: 7 C, 2 G, 1 del      agree 7, substitution 2, deletion 1; 3 insertions
    pos2 ref G: 5 G, 5 del           agree 5, deletion 5
    pos3 ref T: 8 T, 2 A             agree 8, substitution 2
    pos4 ref N: 10 A                 not scored: no reference base to agree with
    Scored 40 = agree 30 + substitution 4 + deletion 6.
    """
    p = SharedPileup("synthetic", "ACGTN")
    p.counts[0, 0] = 10
    p.counts[1, 1], p.counts[1, 2], p.counts[1, _COL_DEL] = 7, 2, 1
    p.counts[1, _COL_INS] = 3
    p.counts[2, 2], p.counts[2, _COL_DEL] = 5, 5
    p.counts[3, 3], p.counts[3, 0] = 8, 2
    p.counts[4, 0] = 10
    p.depth[:] = 10
    p._read_count = 10
    return p


def _snap(pileup=None) -> dict:
    return ConsensusIdentityModule(pileup or _pileup()).snapshot()


def test_identity_substitution_and_deletion_add_to_100() -> None:
    snap = _snap()
    assert (snap["identity_agreeing_depth"], snap["identity_substitution_depth"],
            snap["identity_deletion_depth"], snap["identity_scored_depth"]) == (30, 4, 6, 40)
    assert (snap["consensus_identity_pct"], snap["substitution_pct"],
            snap["deletion_pct"]) == (75.0, 10.0, 15.0)


def test_insertions_are_counted_beside_the_depth_not_in_it() -> None:
    snap = _snap()
    assert snap["identity_insertion_events"] == 3
    assert snap["insertions_per_100_bases"] == 7.5
    # They take no share of the depth: identity is the same without them.
    p = _pileup()
    p.counts[1, _COL_INS] = 0
    assert _snap(p)["consensus_identity_pct"] == snap["consensus_identity_pct"]


def test_the_breakdown_is_over_identitys_positions_not_the_depth_gate() -> None:
    """Depth 10 is under the default min_depth 50, which gates the flags and the curve;
    identity and its breakdown are scored wherever depth > 0. The N is in neither."""
    snap = _snap()
    assert snap["mismatch_rate_by_position"] == [None] * 5
    assert snap["identity_scored_depth"] == 40


def test_the_breakdown_weighs_positions_by_depth() -> None:
    """1,000 reads with 10% substituted and 100 reads with 50%: by depth 150/1,100 =
    13.64%, where a mean over the two positions would say 30%."""
    p = SharedPileup("synthetic", "AA")
    p.counts[0, 0], p.counts[0, 2] = 900, 100
    p.counts[1, 0], p.counts[1, 2] = 50, 50
    p.depth[0], p.depth[1] = 1000, 100
    p._read_count = 1000
    snap = _snap(p)
    assert snap["substitution_pct"] == pytest.approx(150 / 1100 * 100, abs=1e-4)
    assert snap["substitution_pct"] != pytest.approx(30.0, abs=1)


def test_nothing_scored_is_none_not_zero() -> None:
    snap = _snap(SharedPileup("synthetic", "AC"))
    for key in ("consensus_identity_pct", "substitution_pct", "deletion_pct",
                "insertions_per_100_bases"):
        assert snap[key] is None, key


def _second() -> dict:
    """Ref 'A' at depth 100: 90 A, 6 G, 4 deleted, 2 insertion events."""
    p = SharedPileup("synthetic", "A")
    p.counts[0, 0], p.counts[0, 2], p.counts[0, _COL_DEL] = 90, 6, 4
    p.counts[0, _COL_INS] = 2
    p.depth[0] = 100
    p._read_count = 100
    return _snap(p)


def test_pooling_sums_before_dividing_and_still_adds_to_100() -> None:
    pooled = pooled_identity([_snap(), _second()])
    assert (pooled["agreeing_depth"], pooled["substitution_depth"],
            pooled["deletion_depth"], pooled["scored_depth"]) == (120, 10, 10, 140)
    assert pooled["insertion_events"] == 5
    assert pooled["substitution_pct"] == pytest.approx(10 / 140 * 100, abs=1e-4)
    assert pooled["insertions_per_100_bases"] == pytest.approx(5 / 140 * 100, abs=1e-4)
    total = pooled["identity_pct"] + pooled["substitution_pct"] + pooled["deletion_pct"]
    assert total == pytest.approx(100, abs=3e-4)       # each rounded to 4 places


def test_pooling_a_snapshot_without_the_parts_leaves_them_unmeasured() -> None:
    """An older snapshot carries identity's pair but no breakdown. Summing its missing
    parts as zero would understate both; they are None instead."""
    old = {"identity_agreeing_depth": 90, "identity_scored_depth": 100}
    pooled = pooled_identity([_snap(), old])
    assert pooled["identity_pct"] == pytest.approx(120 / 140 * 100, abs=1e-4)
    for key in ("substitution_pct", "deletion_pct", "insertions_per_100_bases"):
        assert pooled[key] is None, key


def test_qc_metrics_carry_the_breakdown_and_no_error_profile() -> None:
    bundle = {"construct:A": {"n_reads": 10, "consensus": _snap()},
              "construct:B": {"n_reads": 10, "consensus": _snap()}}
    out = QCResultIntegrator().integrate(bundle)
    ident = out["metrics"]["sequence_identity"]
    assert (ident["consensus_identity_pct"], ident["substitution_pct"],
            ident["deletion_pct"], ident["insertions_per_100_bases"]) == (75.0, 10.0, 15.0, 7.5)
    assert (ident["substitution_depth"], ident["deletion_depth"],
            ident["insertion_events"]) == (8, 12, 6)
    assert "error_profile" not in out["metrics"]
    for construct in out["constructs"].values():
        assert "error_profile" not in construct
        assert construct["sequence_identity"]["substitution_pct"] == 10.0


def test_the_report_tables_no_longer_carry_the_per_position_mean(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    """The row read the error profile's mean, which qc_result.json no longer carries: the
    table and the record must hold the same numbers."""
    reporter = CoAReporter(tmp_path, min_reads=1, min_reads_per_construct=0)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    mapped = reporter._snapshots["run_summary"]["mapping"]["mapped"]
    run_rows = {r["name"] for r in reporter._build_metrics(mapped)}
    sections = reporter._build_constructs()
    assert sections, "no construct section was built, so this checks nothing"
    construct_rows = {r["name"] for s in sections for r in s["rows"]}
    assert "Per-base identity" in run_rows and "Per-base identity" in construct_rows
    assert "Consensus identity" not in run_rows | construct_rows
    assert "Mean per-position error rate" not in run_rows | construct_rows


def test_the_run_table_breaks_identity_down_in_the_row_below_it(
        tmp_path: Path, fixture_bam: Path, curlcake_ref: Path) -> None:
    """2026-11-04: the run's breakdown has no figure to sit in, so it is a row under
    identity -- substitution and deletion, which add up with identity, and the
    insertions in brackets. The same numbers as qc_result.json, through the table's own
    formatting."""
    reporter = CoAReporter(tmp_path, min_reads=1)
    engine = AnalysisEngine(reporter, reference=curlcake_ref)
    engine.ingest_bam(fixture_bam)
    engine.finalize_all()
    rows = reporter._build_metrics(reporter._snapshots["run_summary"]["mapping"]["mapped"])
    names = [r["name"] for r in rows]
    i = names.index("Per-base identity")
    assert names[i + 1] == "Substitution / deletion"
    ident = QCResultIntegrator().integrate(dict(reporter._snapshots))["metrics"][
        "sequence_identity"]
    assert ident["substitution_pct"] is not None, "nothing to compare against"
    assert rows[i + 1]["value"] == (
        f"{CoAReporter._pct(ident['substitution_pct'], already_pct=True)} / "
        f"{CoAReporter._pct(ident['deletion_pct'], already_pct=True)} "
        f"(insertions {ident['insertions_per_100_bases']:.1f} per 100 bases)")
