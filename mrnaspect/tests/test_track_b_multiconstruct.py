"""Track B — multi-construct support.

The archived curlcake fixture is the motivating case: 166 primary reads across four
constructs (47/45/41/33), of which only the dominant one reached the reporter. The other
three — **72 % of the mapped reads** — had their consensus and error-hotspot results
computed and then thrown away.

B1 (this file's first section) is the enabling fix the plan calls out as "fix first":
``SharedPileup.snapshot()`` used to omit ``min_depth``/``max_depth`` for a zero-read
construct, so the key set depended on the construct. Harmless while one construct was
surfaced; a KeyError in any N-construct aggregator the first time a panel construct gets no
reads — which is the *normal* case for a panel (not every construct is in every run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pysam
import pytest

from mrnaspect.analysis.engine import AnalysisEngine, MaxConstructsExceeded
from mrnaspect.analysis.modules.mapq import MapqModule
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator
from mrnaspect.analysis.shared_pileup import SharedPileup
from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.reporting.reporter import CoAReporter
from mrnaspect.snapshot_keys import CONSTRUCT_PREFIX, UNASSIGNED, construct_ref


# -- B1: snapshot key set is construct-independent ----------------------------------

def test_zero_read_construct_has_the_same_keys_as_a_covered_one() -> None:
    """The key set must not depend on whether a construct received reads."""
    empty = SharedPileup("cc_empty", "ACGTACGTAC").snapshot()
    covered = SharedPileup("cc_covered", "ACGTACGTAC")
    covered.depth[0] = 7  # stand in for an ingested read; snapshot() only reads the arrays
    assert set(empty) == set(covered.snapshot())
    # and the depth keys specifically, since those were the ones that vanished
    for key in ("mean_depth", "min_depth", "max_depth", "covered_positions",
                "coverage_fraction"):
        assert key in empty, f"{key} missing from a zero-read construct's snapshot"


def test_zero_read_depth_stats_are_honest_zeros() -> None:
    """Zeros here are measured, not fabricated: no read covered any position."""
    snap = SharedPileup("cc_empty", "ACGTACGTAC").snapshot()
    assert snap["reads_processed"] == 0
    assert snap["mean_depth"] == 0.0
    assert snap["min_depth"] == 0
    assert snap["max_depth"] == 0
    assert snap["covered_positions"] == 0
    assert snap["coverage_fraction"] == 0.0


def test_empty_reference_does_not_raise_or_produce_nan() -> None:
    """A zero-length reference: np.min/np.max raise and np.mean returns nan on an empty
    array, so this branch is required rather than defensive."""
    snap = SharedPileup("cc_zero_len", "").snapshot()
    assert snap["ref_length"] == 0
    assert snap["min_depth"] == 0
    assert snap["max_depth"] == 0
    assert snap["mean_depth"] == 0.0  # not nan
    assert snap["coverage_fraction"] == 0.0


def test_covered_construct_values_are_unchanged_by_the_fix_marker() -> None:
    """B1 is strictly additive: no value a covered construct already reported moves."""
    pileup = SharedPileup("cc", "ACGTACGTAC")
    pileup.depth[:4] = [10, 20, 30, 0]
    snap = pileup.snapshot()
    assert snap["min_depth"] == 0          # position 3 onwards is uncovered
    assert snap["max_depth"] == 30
    assert snap["covered_positions"] == 3
    assert snap["coverage_fraction"] == pytest.approx(0.3)
    assert snap["mean_depth"] == pytest.approx(6.0)  # 60 / 10 positions


# -- helpers ------------------------------------------------------------------------

def _engine(tmp_path: Path, reference: Path, **kw) -> AnalysisEngine:
    """An engine wired to a real reporter (which only accumulates until asked to render)."""
    return AnalysisEngine(CoAReporter(tmp_path / "rep", min_reads=1),
                          reference=reference, **kw)


def _synthetic_bam(path: Path, *, contigs, records) -> Path:
    """Write a tiny BAM. ``records`` are ``(name, ref_id_or_None, pos, seq, mapq)``.

    ``ref_id_or_None`` of None makes the read unmapped. Deliberately hand-built rather
    than sliced from the fixture: the routing cases that matter (unmapped, mapped to a
    contig absent from the reference) do not occur in the archived fixture at all.
    """
    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"SN": name, "LN": length} for name, length in contigs]}
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for name, ref_id, pos, seq, mapq in records:
            a = pysam.AlignedSegment(out.header)
            a.query_name = name
            a.query_sequence = seq
            a.query_qualities = pysam.qualitystring_to_array("I" * len(seq))
            if ref_id is None:
                a.is_unmapped = True
                a.reference_id = -1
                a.reference_start = -1
            else:
                a.is_unmapped = False
                a.reference_id = ref_id
                a.reference_start = pos
                a.cigartuples = [(0, len(seq))]
                a.mapping_quality = mapq
            out.write(a)
    return path


# -- B2: every read lands in exactly one bucket ---------------------------------------

def test_all_four_constructs_are_reported_not_just_the_dominant(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The headline Track B claim, on the archived fixture that motivated it."""
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    eng.finalize_all()
    snaps = eng.reporter._snapshots
    refs = {construct_ref(k) for k in snaps if k.startswith(CONSTRUCT_PREFIX)}
    assert refs == {"cc6m_2244_t7_ecorv", "cc6m_2459_t7_ecorv",
                    "cc6m_2595_t7_ecorv", "cc6m_2709_t7_ecorv"}
    # ...and each carries its OWN consensus, not a copy of the primary construct's.
    identities = {r: snaps[CONSTRUCT_PREFIX + r]["consensus"]["consensus_identity_pct"]
                  for r in refs}
    assert len(set(identities.values())) == 4, identities


def test_per_construct_counts_sum_to_the_mapped_total(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The partition, on real data: 45 + 47 + 41 + 33 = 166 primary reads."""
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    acct = eng.verify_partition()
    assert acct["by_construct"] == {"cc6m_2244_t7_ecorv": 45, "cc6m_2459_t7_ecorv": 47,
                                   "cc6m_2595_t7_ecorv": 41, "cc6m_2709_t7_ecorv": 33}
    assert acct["primary_reads"] == 166
    assert acct["bucketed"] == 166
    assert acct["unassigned"] == 0
    assert acct["unaccounted"] == 0


def test_verify_partition_raises_when_a_read_is_routed_twice(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The guard must fire, or it is decoration. Simulate a double-route by hand."""
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    eng._constructs["cc6m_2244_t7_ecorv"].n_assigned += 1  # one read counted twice
    with pytest.raises(ValueError, match="do not partition"):
        eng.verify_partition()


def _bam_with_barcodes(path: Path, *, contig, length, records) -> Path:
    """Write a tiny BAM whose reads carry `BC` tags. ``records`` are ``(name, pos, bc)``.

    Separate from ``_synthetic_bam`` rather than a parameter on it, so the five-tuple
    records every other test in this file passes stay five-tuples.
    """
    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"SN": contig, "LN": length}]}
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for name, pos, bc in records:
            a = pysam.AlignedSegment(out.header)
            a.query_name = name
            a.query_sequence = "ACGTACGTAC"
            a.query_qualities = pysam.qualitystring_to_array("I" * 10)
            a.is_unmapped = False
            a.reference_id = 0
            a.reference_start = pos
            a.cigartuples = [(0, 10)]
            a.mapping_quality = 60
            a.set_tag("BC", bc, value_type="Z")
            out.write(a)
    return path


def test_two_barcodes_on_ONE_construct_are_pooled_and_that_is_the_documented_contract(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    """mRNAspect does not demultiplex. Pinned because the README now promises it.

    Barcode demux is a DECIDED split, not an omission: per-barcode QC
    belongs to a separate, researcher-side workflow (WarpDemuX, signal-based),
    and Core must never depend on it. Core routes on `read.reference_name`
    alone (`AnalysisEngine._construct_for`), so two barcodes carrying the SAME construct
    land in ONE track.

    That case is not hypothetical here: it is exactly a 2-plex old-batch/new-batch
    design, where both barcodes carry the same
    constructs. The README tells the operator this pools silently, and silently is the
    problem — so the behaviour is asserted rather than left to be rediscovered.
    """
    bam = _bam_with_barcodes(
        tmp_path / "two_barcodes.bam",
        contig="cc6m_2244_t7_ecorv", length=2323,
        records=[("old_batch_read", 100, "barcode03"),
                 ("new_batch_read", 200, "barcode05")])
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(bam)
    acct = eng.verify_partition()

    # One bucket, both reads. If a future change routes on BC, this is the test to revisit
    # -- and the README sentence it backs has to change with it.
    assert acct["by_construct"] == {"cc6m_2244_t7_ecorv": 2}, (
        "reads were split by something other than reference contig")
    assert acct["unassigned"] == 0
    assert len(eng._constructs) == 1, (
        f"expected ONE construct track, got {sorted(eng._constructs)}")


def test_unmapped_and_off_reference_reads_go_to_the_unassigned_bucket(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    """Neither is dropped, and the two are distinguished — they mean different things."""
    bam = _synthetic_bam(
        tmp_path / "mixed.bam",
        contigs=[("cc6m_2244_t7_ecorv", 2323), ("some_contaminant", 500)],
        records=[("on_target", 0, 100, "ACGTACGTAC", 60),
                 ("off_target", 1, 50, "ACGTACGTAC", 60),
                 ("no_map", None, 0, "ACGTACGTAC", 0)])
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(bam)
    acct = eng.verify_partition()
    assert acct["by_construct"] == {"cc6m_2244_t7_ecorv": 1}
    assert acct["unassigned"] == 2
    assert acct["bucketed"] == acct["primary_reads"] == 3
    un = eng._unassigned.snapshot()
    assert un["unmapped"] == 1
    assert un["off_reference"] == 1
    # Naming WHERE the off-target reads went is the difference between "29% on-target"
    # and an actionable contamination finding.
    assert un["off_reference_contigs"] == {"some_contaminant": 1}


def test_max_constructs_is_refused_loudly_not_silently_capped(
    tmp_path: Path, curlcake_ref: Path, fixture_bam: Path
) -> None:
    """Refusing beats an OOM at hour 14 — and beats silently tracking the first N."""
    eng = _engine(tmp_path, curlcake_ref, max_constructs=2)
    with pytest.raises(MaxConstructsExceeded, match="max_constructs=2"):
        eng.ingest_bam(fixture_bam)
    # A config error must escape the per-read guard rather than be absorbed as a bad read.
    assert eng._unprocessable == 0


def test_the_primary_construct_choice_is_deterministic_under_a_tie(
    tmp_path: Path, curlcake_ref: Path
) -> None:
    """A tie must not be broken by dict ordering — the flat slot would then depend on
    read arrival order, and two runs of the same data would disagree."""
    bam = _synthetic_bam(
        tmp_path / "tie.bam",
        contigs=[("cc6m_2459_t7_ecorv", 2540), ("cc6m_2244_t7_ecorv", 2323)],
        records=[("a", 0, 10, "ACGTACGTAC", 60), ("b", 1, 10, "ACGTACGTAC", 60)])
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(bam)
    assert eng._primary_construct().ref_name == "cc6m_2459_t7_ecorv"  # name tiebreak


# -- B3: MAPQ ------------------------------------------------------------------------

def test_mapq_median_is_exact_not_binned() -> None:
    module = MapqModule()
    for mapq, count in ((0, 2), (60, 2)):
        for _ in range(count):
            module.update(_fake_read(mapq=mapq))
    snap = module.snapshot()
    assert snap["n"] == 4
    assert snap["median"] == 30.0        # mean of the two middle order statistics
    assert snap["mapq0_fraction"] == 0.5
    assert snap["low_mapq_fraction"] == 0.5
    assert snap["distribution"] == {"0": 2, "60": 2}


def test_mapq_reports_none_not_zero_when_nothing_was_measured() -> None:
    """A construct with no reads has no MAPQ distribution — it does not have a perfect
    one. A fabricated 0.0 here would read as 'every read was ambiguous'."""
    snap = MapqModule().snapshot()
    assert snap["n"] == 0
    assert snap["mean"] is None and snap["median"] is None
    assert snap["mapq0_fraction"] is None and snap["low_mapq_fraction"] is None


def test_unmapped_reads_are_excluded_from_mapq() -> None:
    """pysam reports mapping_quality 0 for an unmapped read, which would otherwise be
    indistinguishable from a genuine multi-mapping tie and inflate the signal."""
    module = MapqModule()
    module.update(_fake_read(mapq=60))
    module.update(_fake_read(mapq=0, unmapped=True))
    snap = module.snapshot()
    assert snap["n"] == 1
    assert snap["mapq0_fraction"] == 0.0
    assert snap["skipped_unmapped"] == 1


def test_real_fixture_surfaces_the_three_ambiguous_reads(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The archived BAM has 3 reads on cc6m_2709 with MAPQ 7/8/42 against 60 everywhere
    else — invisible before Track B, since nothing read MAPQ at all."""
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    mapq = eng._constructs["cc6m_2709_t7_ecorv"].mapq.snapshot()
    assert mapq["distribution"] == {"7": 1, "8": 1, "42": 1, "60": 30}
    assert mapq["low_mapq_count"] == 2      # 7 and 8 are below the default threshold of 10
    assert mapq["mapq0_count"] == 0
    # every other construct is unanimous
    for ref in ("cc6m_2244_t7_ecorv", "cc6m_2459_t7_ecorv", "cc6m_2595_t7_ecorv"):
        assert eng._constructs[ref].mapq.snapshot()["low_mapq_count"] == 0


class _FakeRead:
    """Minimal AlignedSegment stand-in — MapqModule reads four attributes."""

    def __init__(self, mapq: int, unmapped: bool) -> None:
        self.mapping_quality = mapq
        self.is_unmapped = unmapped
        self.is_secondary = False
        self.is_supplementary = False


def _fake_read(*, mapq: int, unmapped: bool = False) -> _FakeRead:
    return _FakeRead(mapq, unmapped)


# -- B4/B5: integrated result and report ----------------------------------------------

def test_integrated_result_carries_every_construct_and_the_accounting(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    eng.finalize_all()
    result = QCResultIntegrator().integrate(eng.reporter._snapshots)
    assert set(result["constructs"]) == set(eng._constructs)
    assert result["construct_accounting"]["partitions"] is True
    assert result["construct_accounting"]["bucketed"] == 166
    # The flat metric names its scope, so a panel headline is not read as the run's.
    # Item 1b inverted this contract. It used to read `primary_construct_of_4` — an honest
    # label on a number that should not have been one construct's. Now the flat block is
    # pooled, so assert BOTH that it says so and that the value really is not the primary
    # construct's: a `scope` string alone could be relabelled without the pooling landing.
    ident = result["metrics"]["sequence_identity"]
    assert ident["scope"] == "pooled_over_4_constructs"
    assert ident["reference"] is None, "a pooled value is not about one reference"
    primary = eng.reporter._snapshots["consensus"]
    assert ident["scored_depth"] > primary["identity_scored_depth"]
    assert ident["consensus_identity_pct"] != pytest.approx(
        primary["consensus_identity_pct"], abs=1e-9)
    assert result["metrics"]["sequence_identity"]["n_constructs"] == 4
    # Per-construct entries use the same metric shapes as the run-level block.
    one = result["constructs"]["cc6m_2595_t7_ecorv"]
    assert one["sequence_identity"]["status"] == "measured"
    assert one["n_reads"] == 41
    assert one["assignment"]["mapq_median"] == 60.0


def test_integrator_flags_a_broken_partition_rather_than_hiding_it(
    tmp_path: Path, fixture_bam: Path, curlcake_ref: Path
) -> None:
    """The integrator re-derives the arithmetic from the serialized snapshots, so it also
    covers a snapshot-shape bug the engine's own live check cannot see."""
    eng = _engine(tmp_path, curlcake_ref)
    eng.ingest_bam(fixture_bam)
    eng.finalize_all()
    bundle = dict(eng.reporter._snapshots)
    tampered = dict(bundle[CONSTRUCT_PREFIX + "cc6m_2244_t7_ecorv"])
    tampered["n_reads"] = 40  # 5 reads vanish between engine and serialized snapshot
    bundle[CONSTRUCT_PREFIX + "cc6m_2244_t7_ecorv"] = tampered
    acct = QCResultIntegrator().integrate(bundle)["construct_accounting"]
    assert acct["partitions"] is False
    assert acct["unaccounted"] == 5


def test_report_renders_a_section_per_construct(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path
) -> None:
    """End to end through the real driver: the HTML gains one block per construct, and
    each thin construct carries its own derived caveat.

    Every construct here has 47 reads or fewer, under the shipped per-construct minimum
    (100), which would list them all; this test is about the sections, so it lowers it."""
    out_dir = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1, "min_reads_per_construct": 0})
    OfflinePipeline(bam_input_dir, out_dir, cfg).run()
    html = (out_dir / "report_run_end.html").read_text(encoding="utf-8")
    for ref in ("cc6m_2244_t7_ecorv", "cc6m_2459_t7_ecorv",
                "cc6m_2595_t7_ecorv", "cc6m_2709_t7_ecorv"):
        assert f'construct-title">{ref}' in html
    # Every construct on this fixture has fewer reads than the 50x depth gate, so every
    # one must say so — the run-level gate passing (min_reads=1) says nothing about them.
    assert html.count("depth gate, so no position can reach it") == 4


def test_result_json_records_the_constructs(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    OfflinePipeline(bam_input_dir, out_dir, cfg).run()
    doc = json.loads((out_dir / "qc_result.json").read_text(encoding="utf-8"))
    assert len(doc["qc_metrics"]["constructs"]) == 4
    assert doc["qc_metrics"]["construct_accounting"]["partitions"] is True
    assert doc["qc_metrics"]["unassigned"]["n_reads"] == 0
    assert UNASSIGNED in doc["snapshots"]
