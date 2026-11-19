"""Wave-1 correctness guards — unit tests that actually TRIGGER each M-series fix.

The fixture BAM is clean (all-primary ACGT reads, no U/N/malformed CIGAR), so it proves
these fixes don't break the happy path but never exercises the guards themselves. These
synthetic tests fire each guard directly:

  M1  shared_pileup — RNA U->T normalization + alphabet validation + unknown-base counter
  M5  shared_pileup — transactional pre-walk CIGAR/SEQ-length validation (no partial mutation)
  M6  shared_pileup — fail-loud on a splice (N) CIGAR op
  C1  read_stats    — secondary/supplementary counted separately, never in the primary total
  M4  qc_integrator — not_measured / no_data instead of a fabricated 0.0
"""

from __future__ import annotations

import numpy as np
import pysam
import pytest

from mrnaspect.analysis.shared_pileup import SharedPileup
from mrnaspect.analysis.read_stats import RunSummaryModule
from mrnaspect.analysis.streaming_interface import (
    WelfordAccumulator, HistogramAccumulator,
)
from mrnaspect.analysis.modules.qc_integrator import QCResultIntegrator


# --- pysam read helper ------------------------------------------------------

_HEADER = pysam.AlignmentHeader.from_dict(
    {"HD": {"VN": "1.6"}, "SQ": [{"SN": "ref", "LN": 100}]}
)


def _read(seq, cigar, *, flag=0, start=0, name="r", qs=None):
    """Build a minimal aligned read against reference 'ref' (id 0)."""
    r = pysam.AlignedSegment(_HEADER)
    r.query_name = name
    r.flag = flag
    r.reference_id = 0
    r.reference_start = start
    r.query_sequence = seq
    r.cigartuples = cigar
    if qs is not None:
        r.set_tag("qs", float(qs), "f")
    return r


# --- M1: reference alphabet -------------------------------------------------

def test_m1_rna_u_normalized_to_t():
    assert SharedPileup("ref", "ACGU").ref_seq == "ACGT"
    assert SharedPileup("ref", "acgu").ref_seq == "ACGT"  # lowercase too


def test_m1_ambiguous_n_allowed():
    assert SharedPileup("ref", "ACGN").ref_seq == "ACGN"  # no raise


def test_m1_bad_alphabet_raises():
    with pytest.raises(ValueError, match="non-ACGTU/N"):
        SharedPileup("ref", "ACGX")


def test_m1_unknown_read_base_counted_not_silent():
    pileup = SharedPileup("ref", "ACGGT")
    # Read whose 3rd base is 'N' (no ACGT column) over 5M: counted, not silently dropped.
    pileup.update(_read("ACNGT", [(0, 5)]))
    assert pileup.snapshot()["unknown_read_bases"] == 1


# --- M5: transactional CIGAR/SEQ validation ---------------------------------

def test_m5_cigar_seq_length_mismatch_raises():
    pileup = SharedPileup("ref", "A" * 20)
    # CIGAR consumes 10 query bases but SEQ is only 8 long -> malformed.
    with pytest.raises(ValueError, match="query-consumption"):
        pileup.update(_read("ACGTACGT", [(0, 10)]))


def test_m5_no_partial_mutation_on_bad_read():
    pileup = SharedPileup("ref", "ACGTACGT")
    pileup.update(_read("ACGTACGT", [(0, 8)]))          # one good read
    depth_before = pileup.depth.copy()
    reads_before = pileup._read_count
    with pytest.raises(ValueError):
        pileup.update(_read("ACGT", [(0, 10)]))          # malformed -> raises pre-walk
    # Transactional: the bad read left counts/depth and the read count untouched.
    assert np.array_equal(pileup.depth, depth_before)
    assert pileup._read_count == reads_before


# --- M6: fail-loud on splice N ----------------------------------------------

def test_m6_splice_n_op_raises():
    pileup = SharedPileup("ref", "A" * 30)
    # 5M 10N 5M: query length 10 (N does not consume query), so the length check passes
    # and the N check fires.
    with pytest.raises(ValueError, match="N .*splice"):
        pileup.update(_read("ACGTACGTAC", [(0, 5), (3, 10), (0, 5)]))


# --- C1: non-primary counters ----------------------------------------------

def test_c1_secondary_supplementary_counted_separately():
    rs = RunSummaryModule()
    rs.update(_read("ACGT", [(0, 4)], flag=0, qs=10))            # primary
    rs.update(_read("ACGT", [(0, 4)], flag=0x100, qs=10))       # secondary (0x100)
    rs.update(_read("ACGT", [(0, 4)], flag=0x800, qs=10))       # supplementary (0x800)
    snap = rs.snapshot()
    assert snap["mapping"]["total"] == 1                         # only the primary
    assert snap["non_primary"] == {"secondary": 1, "supplementary": 1}


def test_c1_missing_qs_counted():
    rs = RunSummaryModule()
    rs.update(_read("ACGT", [(0, 4)], qs=10))                    # has qs
    rs.update(_read("ACGT", [(0, 4)]))                          # no qs tag
    assert rs.snapshot()["quality_score"]["missing_qs"] == 1


# --- A6: accumulators report None on empty (not a fabricated 0.0) -----------

def test_a6_welford_empty_is_none():
    d = WelfordAccumulator().to_dict()
    assert d["n"] == 0
    assert d["mean"] is None and d["min"] is None and d["max"] is None


def test_a6_histogram_empty_is_none():
    d = HistogramAccumulator(0.0, 10.0, 10).to_dict()
    assert d["n"] == 0
    assert d["median"] is None and d["Q1"] is None and d["Q3"] is None


def test_a6_welford_nonempty_reports_real_values():
    acc = WelfordAccumulator()
    acc.update(5.0)
    d = acc.to_dict()
    assert d["mean"] == 5.0 and d["min"] == 5.0 and d["max"] == 5.0


# --- M4: integrator honesty (no fabricated 0.0) -----------------------------

def test_m4_empty_bundle_all_not_measured():
    out = QCResultIntegrator().integrate({})
    for name in ("sequence_identity", "polya_tail", "read_length"):
        assert out["metrics"][name]["status"] == "not_measured"


def _identity_bundle(**consensus):
    """A bundle shaped the way a real run produces one.

    Item 1b: the run-level identity is POOLED over the `construct:<ref>` tracks, so the flat
    `consensus` slot is no longer what the integrator reads. These M4 fixtures used to be
    keyed on that slot; the guarantee under test (three honest states) is unchanged.
    """
    return {"construct:ref": {"n_reads": 1, "consensus": {"ref_name": "ref", **consensus}}}


def test_m4_no_coverage_consensus_is_no_data():
    # A real no-coverage snapshot carries consensus_identity_pct=None (not 0.0) AND a zero
    # scored depth — the ingredients are present, they just sum to nothing.
    bundle = _identity_bundle(covered_positions=0, consensus_identity_pct=None,
                              identity_agreeing_depth=0, identity_scored_depth=0)
    entry = QCResultIntegrator().integrate(bundle)["metrics"]["sequence_identity"]
    assert entry["status"] == "no_data"
    assert "consensus_identity_pct" not in entry          # withheld, not a fabricated 0.0


def test_m4_genuine_zero_identity_is_measured_not_no_data():
    # A construct that is 100% wrong but COVERED has identity 0.0 with coverage>0: this is
    # a real, catastrophic measurement and must stay 'measured', distinct from no_data.
    bundle = _identity_bundle(covered_positions=500, consensus_identity_pct=0.0,
                              identity_agreeing_depth=0, identity_scored_depth=500)
    entry = QCResultIntegrator().integrate(bundle)["metrics"]["sequence_identity"]
    assert entry["status"] == "measured"
    assert entry["consensus_identity_pct"] == 0.0         # the real 0.0 is reported


def test_a_pre_item_1a_snapshot_is_not_reported_as_no_data():
    """An OLD snapshot is a fact about the record, not about the run.

    A snapshot written before item 1a carries `consensus_identity_pct` but not the
    numerator/denominator pooling needs, and a pooled value cannot be rebuilt from a rounded
    percentage. Calling that `no_data` would assert the run measured nothing. Reachable via
    `--resume-from-snapshot` over a pre-1a `latest_snapshot.json`.
    """
    legacy = _identity_bundle(covered_positions=500, consensus_identity_pct=79.9226)
    entry = QCResultIntegrator().integrate(legacy)["metrics"]["sequence_identity"]
    assert entry["status"] == "not_measured"
    assert "from an older version" in entry["reason"]
    assert "consensus_identity_pct" not in entry          # never echo an unpoolable value


def test_m4_no_pt_polya_is_no_data_with_missing_count():
    bundle = {"polya": {"polya_length": {"n": 0}, "reads_processed": 100,
                        "missing_pt": 100}}
    entry = QCResultIntegrator().integrate(bundle)["metrics"]["polya_tail"]
    assert entry["status"] == "no_data"
    assert entry["missing_pt"] == 100                     # honest count surfaced


# --- Wave-1 lows: A7 / A2 / D3 / D6 / L1 ------------------------------------

def test_a7_quantile_rejects_out_of_range_q():
    h = HistogramAccumulator(0.0, 10.0, 10)
    h.update(5.0)
    for bad_q in (-0.1, 1.5, 50):                          # sign error, fraction>1, percentage
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            h.quantile(bad_q)
    assert h.quantile(0.0) is not None and h.quantile(1.0) is not None  # bounds allowed


def test_d3_non_dict_config_section_raises_clearly():
    from mrnaspect.main import load_config
    with pytest.raises(ValueError, match="cadence.*must be a mapping"):
        # A YAML that sets a nested section to a scalar would otherwise fail later with an
        # opaque "'int' object is not subscriptable" when main() subscripts it.
        load_config(None, {"cadence": 5})


def test_interims_share_single_min_reads_floor(tmp_path, curlcake_ref):
    from mrnaspect.main import OfflinePipeline, load_config
    # 15e: interim_min_reads was collapsed into the single `min_reads` diagnostic floor — there is
    # no separate interim floor attribute, and interims gate on min_reads.
    # `reference` is explicit because the shipped config no longer answers it (2026-09-19).
    cfg = load_config(None, {"reference": str(curlcake_ref),
                             "mode": "bam", "min_reads": 4200})
    pipe = OfflinePipeline(tmp_path, tmp_path / "o1", cfg)
    assert not hasattr(pipe, "interim_min_reads")
    assert pipe.min_reads == 4200


def test_config_as_source_fail_loud_and_precedence(tmp_path):
    from mrnaspect.main import load_config, _load_yaml_mapping
    # Fail loud when a required config file is missing (the shipped-config packaging guard).
    with pytest.raises(FileNotFoundError, match="not found"):
        _load_yaml_mapping(tmp_path / "nope.yaml", what="test config")
    # CLI overrides the shipped YAML; without an override the shipped value stands.
    # A placeholder path suffices: load_config does not check that the reference EXISTS —
    # that is `reference_check.py`'s job at pipeline construction — it only refuses an
    # ABSENT one. Keeping it a bare string keeps this config-resolution test free of data.
    ref = {"reference": "/placeholder/ref.fasta"}
    assert load_config(None, {**ref, "min_reads": 42})["min_reads"] == 42
    assert load_config(None, ref)["min_reads"] == 1000          # shipped run_config.yaml value


def test_a_BLANK_reference_stays_blank_through_BOTH_parsers():
    """`--reference ""` must still read as "not supplied", in both drivers.

    It did not. Both parsers declared `type=Path`, and `Path("")` is `Path(".")`, which is
    truthy -- so a blank argument sailed past the guard below and died further down as
    "Reference FASTA is not a file: .", a message about the current directory for a field
    nobody filled in. The GUI now opens that field empty by design, which turns this
    from an edge case into the first error the product is likely to produce.

    Both drivers, because they carry the identical line: a test that exercised only the
    offline one would have left the realtime one broken, which is this project's most
    repeated defect shape.
    """
    from mrnaspect.main import _parse_args as _offline_args
    from mrnaspect.realtime.main import _parse_args as _realtime_args

    for parse in (_offline_args, _realtime_args):
        args = parse(["--input-dir", "/tmp", "--output-dir", "/tmp", "--reference", ""])
        assert not args.reference, (
            f"{parse.__module__}: a blank --reference survived as {args.reference!r}, so "
            f'the "no reference was supplied" guard will not see it')
        # The same expression the drivers use to fold the flag into the config.
        assert (str(args.reference) if args.reference else None) is None


def test_no_reference_is_refused_at_load_with_the_reason(tmp_path):
    """The shipped config answers every driver key EXCEPT the reference — on purpose.

    A default reference can only be someone else's construct, and getting it wrong is
    SILENT: a valid FASTA of the wrong construct passes every check `reference_check.py`
    makes and then maps 0.00 %, which reads as a dead flow cell (measured 0/19,992 on
    IVT_260407). So the shipped YAML carries `reference: null` — the key is documented
    without being answered — and the run must stop at LOAD, naming the cause, rather than
    at `Path(None)` further down.
    """
    from mrnaspect.main import load_config, OfflinePipeline

    with pytest.raises(ValueError, match="no reference was supplied"):
        load_config(None, {})
    # ...and the message has to be actionable, not just present.
    try:
        load_config(None, {})
    except ValueError as exc:
        assert "--reference" in str(exc) and "almost no read would map" in str(exc)

    # Happy path: supplying one gets through load, and is what the pipeline then uses.
    cfg = load_config(None, {"reference": "/placeholder/ref.fasta", "mode": "bam"})
    assert cfg["reference"] == "/placeholder/ref.fasta"


def test_the_engine_has_no_default_reference():
    """Omitting it is a TypeError, not a quiet run against a lab file.

    `AnalysisEngine` used to default to a curlcake reference on one lab filesystem, with a
    comment conceding "always pass this explicitly" — a default that must never be used.
    """
    import inspect
    from mrnaspect.analysis.engine import AnalysisEngine
    import mrnaspect.analysis.engine as engine_mod

    assert not hasattr(engine_mod, "DEFAULT_REFERENCE")
    param = inspect.signature(AnalysisEngine.__init__).parameters["reference"]
    assert param.default is inspect.Parameter.empty


def test_l1_set_from_signal_sets_flag_without_logging(caplog):
    from mrnaspect.runtime.lifecycle import AbortFlag
    flag = AbortFlag()
    with caplog.at_level("DEBUG"):
        flag.set_from_signal()                             # the signal-handler path: flag only
    assert flag.is_set()
    assert caplog.records == []                            # no logging inside the handler path


# --- Wave-2 item 2: histogram saturation warning ----------------------------

def test_item2_saturation_warns_once_and_preserves_values(caplog):
    h = HistogramAccumulator(0.0, 10.0, 10)               # tracks [0, 10)
    for _ in range(97):
        h.update(5.0)                                     # in-range
    for _ in range(3):
        h.update(100.0)                                   # overflow: 3/100 = 3% > 1% threshold
    with caplog.at_level("WARNING"):
        d1 = h.to_dict()
        d2 = h.to_dict()                                  # second call must NOT re-warn
    warnings = [r for r in caplog.records if "saturation" in r.getMessage()]
    assert len(warnings) == 1                             # warned exactly once
    assert d1["overflow"] == 3 and d1["median"] == d2["median"]  # values unchanged by warning


def test_item2_no_warning_below_threshold(caplog):
    h = HistogramAccumulator(0.0, 10.0, 10)
    for _ in range(1000):
        h.update(5.0)                                     # all in-range: 0% escape
    with caplog.at_level("WARNING"):
        h.to_dict()
    assert not [r for r in caplog.records if "saturation" in r.getMessage()]


# --- Wave-2 items 1+8: per-module config (resolve_params / validate_histogram) ---

def test_resolve_params_deep_merge_keeps_siblings():
    from mrnaspect.analysis.streaming_interface import resolve_params
    defaults = {"short_threshold": 50,
                "histogram": {"bin_min": 0.0, "bin_max": 500.0, "n_bins": 100}}
    # Override one nested key -> siblings AND the other top-level key survive (deep merge).
    merged = resolve_params({"histogram": {"bin_max": 800.0}}, defaults)
    assert merged == {"short_threshold": 50,
                      "histogram": {"bin_min": 0.0, "bin_max": 800.0, "n_bins": 100}}
    assert resolve_params(None, defaults) == defaults          # None -> full copy of defaults


def test_resolve_params_fails_loud_on_unknown_or_mistyped_key():
    from mrnaspect.analysis.streaming_interface import resolve_params
    with pytest.raises(ValueError, match="unknown parameter key"):
        resolve_params({"min_dpeth": 10}, {"min_depth": 50})    # typo must not silently no-op
    hist_defaults = {"histogram": {"bin_min": 0.0, "bin_max": 500.0, "n_bins": 100}}
    with pytest.raises(ValueError, match="must be a mapping"):
        resolve_params({"histogram": 5}, hist_defaults)        # scalar where a block is expected


def test_validate_histogram_geometry():
    from mrnaspect.analysis.streaming_interface import validate_histogram
    assert validate_histogram({"bin_min": 0.0, "bin_max": 500.0, "n_bins": 100}) == (0.0, 500.0, 100)
    with pytest.raises(ValueError, match="must exceed"):
        validate_histogram({"bin_min": 10.0, "bin_max": 5.0, "n_bins": 100})  # bin_width <= 0
    with pytest.raises(ValueError, match="n_bins"):
        validate_histogram({"bin_min": 0.0, "bin_max": 5.0, "n_bins": 0})


def test_module_defaults_reproduce_hardcoded_geometry():
    # DEFAULTS must reproduce the exact pre-refactor histogram + threshold values (parity).
    from mrnaspect.analysis.read_stats import RunSummaryModule
    from mrnaspect.analysis.modules.polya import PolyAModule
    from mrnaspect.analysis.modules.consensus import DEFAULTS as CONS_D
    from mrnaspect.analysis.modules.error_hotspot import DEFAULTS as EH_D
    qs = RunSummaryModule()._qs_hist
    assert (qs.bin_min, qs.bin_max, qs.n_bins) == (0.0, 30.0, 100)
    pt = PolyAModule()._pt_hist
    # Changed on purpose 2026-11-09: 1 nt bins centred on the integers, ending at
    # pt 500 as the 5 nt bins (0.0, 500.0, 100) did, so the same reads overflow.
    assert (pt.bin_min, pt.bin_max, pt.n_bins) == (-0.5, 499.5, 500)
    assert PolyAModule()._smoothing_nt == 2.0
    # polya's short cutoff is no longer a module default: it derives from the user's
    # target_length (see test_polya_denominator.py), so with the shipped defaults there is
    # deliberately NO threshold and the short-tail metric abstains.
    assert PolyAModule()._short_threshold is None
    # min_depth locked (rule #5). mismatch_threshold replaced low_consensus_threshold (item
    # 6, DQ3) and variant_freq_threshold (content 7) on 2026-11-04: one flag list.
    assert CONS_D == {"min_depth": 50, "mismatch_threshold": 0.5}
    # error_hotspot: one depth, 50, both to report a position (per position and in the
    # means) and to rank it -- the consensus module's gate. The cost: a run below 50x
    # reports no error profile at all. No threshold and no null path: positions are
    # ranked, and a null is chosen by model and arm, not configured.
    assert EH_D == {"min_depth": 50, "hotspot_min_depth": 50, "hotspot_top_n": 20}


def test_module_config_override_reaches_module():
    from mrnaspect.analysis.modules.polya import PolyAModule
    # 999.5 keeps the bins a whole number of nt (2), which poly(A) requires.
    pt = PolyAModule(config={"histogram": {"bin_max": 999.5}})._pt_hist
    assert (pt.bin_min, pt.bin_max, pt.n_bins) == (-0.5, 999.5, 500)  # sibling keys kept
    assert PolyAModule(config={"short_threshold": 20})._short_threshold == 20


def test_threshold_override_reaches_pileup_consumers():
    from mrnaspect.analysis.shared_pileup import SharedPileup
    from mrnaspect.analysis.modules.consensus import ConsensusIdentityModule
    from mrnaspect.analysis.modules.error_hotspot import ErrorHotspotModule
    pileup = SharedPileup("ref", "ACGT")
    assert ConsensusIdentityModule(pileup, config={"min_depth": 77}).min_depth == 77
    assert ErrorHotspotModule(pileup, config={"min_depth": 3}).min_depth == 3


def test_engine_threads_module_params(tmp_path, curlcake_ref):
    # A REAL reference: the engine validates it at construction (the reference is required,
    # not optional — a missing one used to disable the pileup modules and silently drop
    # sequence identity from the report). This test is about module-param threading, so it
    # just needs the reference to be valid.
    from mrnaspect.analysis.engine import AnalysisEngine
    from mrnaspect.reporting.reporter import CoAReporter
    reporter = CoAReporter(tmp_path, min_reads=1)
    eng = AnalysisEngine(reporter, reference=curlcake_ref,
                         module_params={"polya": {"short_threshold": 33},
                                        "run_summary": {"histogram": {"bin_max": 60.0}}})
    assert eng.polya._short_threshold == 33
    assert eng.run_summary._qs_hist.bin_max == 60.0
