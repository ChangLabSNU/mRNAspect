"""Ingest-integrity fixes: duplicate reads, ambiguous reference bases, indel magnitude,
and tool provenance.

What ties these together is that each one produced a plausible-looking wrong number rather
than an error — the failure mode the whole package is built to refuse.
"""

from __future__ import annotations

import numpy as np
import pysam
import pytest

from mrnaspect.analysis.shared_pileup import (
    SharedPileup, _COL_DEL, _COL_INS, _COL_INS_BASES,
)
from mrnaspect.analysis.modules.error_hotspot import ErrorHotspotModule

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 40}]}
)


def _read(seq, cigar, *, start=0, name="r", flag=0):
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = start
    r.query_sequence = seq
    r.cigartuples = cigar
    return r


# --- #9 insertion magnitude, and the deletion asymmetry ---------------------

def test_insertion_records_events_and_bases_separately():
    """A 12 nt slippage and a 1 nt stutter are one EVENT each but differ 12-fold in size.

    An insertion consumes no reference coordinate, so it anchors at a single position with
    no span to spread over — counting events alone discards the magnitude entirely.
    """
    p = SharedPileup("ref", "ACGT" * 10)
    p.update(_read("AC" + "T" * 12 + "GT", [(0, 2), (1, 12), (0, 2)], name="big"))
    p.update(_read("ACTGT", [(0, 2), (1, 1), (0, 2)], name="small"))

    assert p.counts[2, _COL_INS] == 2          # two events
    assert p.counts[2, _COL_INS_BASES] == 13   # 12 + 1 bases — the part that was lost


def test_deletions_already_carry_their_length():
    """The asymmetry the fix rests on: deletions need no equivalent column.

    A deletion DOES consume reference positions, so the D branch increments every skipped
    position and its length is already fully represented — 12 nt deleted is 12 increments.
    """
    p = SharedPileup("ref", "ACGT" * 10)
    p.update(_read("AC" + "GT", [(0, 2), (2, 12), (0, 2)], name="del12"))

    assert int(p.counts[2:14, _COL_DEL].sum()) == 12
    assert all(p.counts[pos, _COL_DEL] == 1 for pos in range(2, 14))
    assert int(p.depth[2]) == 1                # deletions count toward depth


def test_hotspot_reports_insertion_magnitude(tmp_path):
    p = SharedPileup("ref", "ACGT" * 10)
    for i in range(20):
        p.update(_read("AC" + "T" * 10 + "GT", [(0, 2), (1, 10), (0, 2)], name=f"r{i}"))
    snap = ErrorHotspotModule(p, config={"min_depth": 1, "hotspot_min_depth": 1}).snapshot()
    # Event rate says "every read inserted here"; the base rate says how much.
    assert snap["mean_inserted_bases_rate"] > snap["mean_insertion_rate"]
    assert snap["max_mean_insertion_length"] == 10.0


# --- #10 local-depth denominator --------------------------------------------

def test_base_fractions_insertion_uses_local_depth():
    """Was ins/global_read_count while its five siblings were ins/local_depth, so the
    insertion fraction was understated wherever coverage was thin."""
    p = SharedPileup("ref", "ACGT" * 10)
    # One read covers pos 0-3 and inserts there; four more reads cover a DISTANT region,
    # so they raise the global read count without covering pos 2.
    p.update(_read("ACTGT", [(0, 2), (1, 1), (0, 2)], name="ins"))
    for i in range(4):
        p.update(_read("ACGT", [(0, 4)], start=20, name=f"far{i}"))

    assert p._read_count == 5 and int(p.depth[2]) == 1
    # 1 event over local depth 1 -- not 1/5 = 0.2
    assert p.counts[2, _COL_INS] / p.depth[2] == 1.0
    assert p.counts[2, _COL_INS_BASES] / p.depth[2] == 1.0


# --- #8 ambiguous reference bases -------------------------------------------

def test_ambiguous_ref_excluded_from_error_rates(tmp_path):
    """An N reference base has nothing to disagree with, so `mismatch` is undefined there.

    ref_base_counts stays 0 at an N, so the old formula scored it ~100 % mismatch and
    dragged every reported mean up. ConsensusIdentityModule already excluded these.
    """
    ref = "ACGTN" + "ACGT" * 8          # exactly one N, at position 4
    p = SharedPileup("ref", ref)
    for i in range(20):                # every read matches the reference perfectly
        p.update(_read(ref.replace("N", "A"), [(0, len(ref))], name=f"r{i}"))

    snap = ErrorHotspotModule(p, config={"min_depth": 1, "hotspot_min_depth": 1}).snapshot()
    assert snap["ambiguous_ref_positions"] == 1
    assert snap["substitution_rate"][4] is None        # not scored
    assert snap["total_error_rate"][4] is None
    # The decisive one: with the N excluded, a perfectly matching pileup has no
    # substitutions.
    # Before the fix this mean was dragged up by the single 100 %-mismatch N position.
    assert snap["mean_substitution_rate"] == 0.0
    assert 4 not in [h["pos"] for h in snap["top_positions"]]


# --- #7 duplicate primary reads ---------------------------------------------

def test_duplicate_primary_read_is_dropped_not_double_counted(tmp_path, curlcake_ref):
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter

    eng = AnalysisEngine(CoAReporter(tmp_path, min_reads=1), reference=curlcake_ref)
    assert eng._note_primary("read-A") is True
    assert eng._note_primary("read-B") is True
    assert eng._note_primary("read-A") is False        # duplicate
    assert eng.dedup_accounting["duplicate_primary_reads"] == 1
    assert eng.dedup_accounting["status"] == "active"
    assert eng.dedup_accounting["tracked_read_ids"] == 2


def test_dedup_cap_is_reported_not_silent(tmp_path, curlcake_ref):
    """At capacity, tracking stops and SAYS so — a 0 duplicate count under 'capped' is not
    a clean bill of health, and declining to track must never become declining to count."""
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter

    eng = AnalysisEngine(CoAReporter(tmp_path, min_reads=1), reference=curlcake_ref,
                         max_tracked_reads=2)
    for name in ("a", "b", "c", "d"):
        assert eng._note_primary(name) is True         # never rejects past the cap
    assert eng.dedup_accounting["status"] == "capped"


def test_dedup_can_be_disabled(tmp_path, curlcake_ref):
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter

    eng = AnalysisEngine(CoAReporter(tmp_path, min_reads=1), reference=curlcake_ref,
                         max_tracked_reads=0)
    assert eng._note_primary("x") is True and eng._note_primary("x") is True
    assert eng.dedup_accounting["status"] == "disabled"


def test_whole_bam_ingested_twice_does_not_double_the_reads(
        tmp_path, fixture_bam, curlcake_ref):
    """The realistic failure: the same aligned BAM handed over under two names."""
    import shutil
    from mrnaspect.main import OfflinePipeline, load_config
    import json

    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})

    def run(names: list[str], where: str) -> dict:
        src = tmp_path / where / "input"
        src.mkdir(parents=True)
        for name in names:
            shutil.copy(fixture_bam, src / name)
        OfflinePipeline(src, tmp_path / where / "out", cfg).run()
        return json.loads((tmp_path / where / "out" / "qc_result.json")
                          .read_text(encoding="utf-8"))

    once, doc = run(["a.bam"], "once"), run(["a.bam", "b.bam"], "twice")
    assert doc["qc_metrics"]["summary"]["total_reads"] == 166        # not 332
    assert doc["run_accounting"]["dedup"]["duplicate_primary_reads"] == 166
    # Depth-derived numbers are unchanged by the duplicate — the point of the fix.
    identity = doc["qc_metrics"]["metrics"]["sequence_identity"]["consensus_identity_pct"]
    assert identity == once["qc_metrics"]["metrics"]["sequence_identity"][
        "consensus_identity_pct"]


# --- #11 tool provenance from the BAM's own header --------------------------

def test_tool_provenance_comes_from_the_bam_header(tmp_path, fixture_bam, curlcake_ref):
    """Read from @PG, not by shelling out to --version.

    In bam mode this pipeline never ran dorado or minimap2, so querying whatever binaries
    sit on this host would record a plausible lie. The header states what actually ran.
    """
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter

    eng = AnalysisEngine(CoAReporter(tmp_path, min_reads=1), reference=curlcake_ref)
    eng.ingest_bam(fixture_bam)
    tools = eng.tool_provenance

    assert "minimap2" in tools
    assert tools["minimap2"]["version"]
    # The command line pins the flags the config only describes — this fixture was aligned
    # with -k14, which nothing else in the report would have recorded.
    assert "-k14" in tools["minimap2"]["command_line"]


def test_tool_provenance_reaches_qc_result_json(tmp_path, bam_input_dir, curlcake_ref):
    import json
    from mrnaspect.main import OfflinePipeline, load_config

    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    OfflinePipeline(bam_input_dir, tmp_path / "out", cfg).run()
    doc = json.loads((tmp_path / "out" / "qc_result.json").read_text(encoding="utf-8"))

    tools = doc["provenance"]["tools"]
    assert "minimap2" in tools and "samtools" in tools
