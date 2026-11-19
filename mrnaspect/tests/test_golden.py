"""Golden-snapshot regression: default-config run reproduces the frozen baseline.

This is the parity-safe guard the UPDATE_PLAN batch leans on. Waves 0–2 must NOT move
any number at default config, so this test must stay GREEN through them. Waves 3+ change
reported outputs on purpose — when an owning item lands, this test's golden is re-frozen
and the change is recorded as an explained diff (UPDATE_PLAN re-validation step 2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.tests.conftest import GOLDEN, passthrough_align
from mrnaspect.tests.golden_compare import diff_docs


@pytest.fixture(scope="session")
def golden_doc() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# Explained-diff allowlist: every reported-output change the batch has made vs the frozen
# pre_batch golden, each owned by a batch item (mirrors tests/golden/EXPLAINED_DIFF.md).
# The default run's diff must equal this set EXACTLY — a new/unexpected diff is a regression
# and fails the test. Grows as parity-breaking items land; reset to empty when the golden is
# re-frozen at Wave 6.
EXPECTED_DIFFS = {
    # Content audit 2026-10-28: QC metric #3 is not part of mRNAspect, so its
    # not-measured placeholder leaves the output rather than promise a module.
    "qc_metrics.metrics.modified_u_miscall: {'status': 'not_measured', 'reason': "
    "'modified-U/m1Ψ module not in this build; awaits production m1Ψ model lock'} "
    "-> <absent in new>",
    # H1 — deletion-aware pooled identity (95.37 -> 79.92)
    "qc_metrics.metrics.sequence_identity.consensus_identity_pct: 95.3734 -> 79.9857",
    "snapshots.consensus.consensus_identity_pct: 95.3734 -> 79.9226",
    # item 1a — H1's ratio now also publishes its own numerator and denominator, so identity
    # can be POOLED across constructs (consensus.pooled_identity) instead of the run-level
    # slot carrying whichever single construct had the most reads. These two values are the
    # FLAT slot's, i.e. the primary construct's (cc6m_2459, 47 of 166 reads), and
    # 88583/110836 = 79.9226 % reproduces the published percentage exactly. Additive only —
    # no consumer reads them yet; 1b wires the reporter and the integrator.
    "snapshots.consensus.identity_agreeing_depth: <absent in golden> -> 88583",
    "snapshots.consensus.identity_scored_depth: <absent in golden> -> 110836",
    # --- item 1b — the run-level table and qc_metrics stop describing one construct -----
    # The flat `metrics` block is now POOLED over all four constructs. Its previous values
    # were the primary construct's (cc6m_2459, 47 of 166 reads); `snapshots.consensus` and
    # `snapshots.error_hotspot` still hold that construct and are unchanged.
    # sequence_identity: reference is None because a pooled value is not about one
    # reference; agreeing/scored_depth make the percentage re-derivable from JSON alone.
    "qc_metrics.metrics.sequence_identity.reference: 'cc6m_2459_t7_ecorv' -> None",
    "qc_metrics.metrics.sequence_identity.agreeing_depth: <absent in golden> -> 309332",
    "qc_metrics.metrics.sequence_identity.scored_depth: <absent in golden> -> 386734",
    "qc_metrics.metrics.sequence_identity.denominator: <absent in golden> -> "
    "'summed_coverage_over_scored_reference_positions'",
    "qc_metrics.metrics.sequence_identity.covered_positions: 2464 -> 10058",
    "qc_metrics.metrics.sequence_identity.total_positions: 2540 -> 10324",
    # C1 — new non-primary alignment-record counters
    "snapshots.run_summary.non_primary: <absent in golden> -> "
    "{'secondary': 0, 'supplementary': 0}",
    # item 13 — new missing-qs counter
    "snapshots.run_summary.quality_score.missing_qs: <absent in golden> -> 0",
    # The panel shows a MEDIAN read length, not a mean: read lengths are heavily
    # right-skewed (311,200 nt longest against a 1,170 nt median on a real run), so a
    # mean describes a population that is not there. The exact Welford mean stays in the
    # JSON beside it — see qc_integrator.
    #
    # 2541.18 and not the exact 2542.50 because the histogram's range is derived from the
    # reference (3x 2,709 nt here), which widens the bins 10 -> 16.25 nt on THIS fixture.
    # The 1.32 nt is the stated cost of covering constructs the fixed range could not —
    # see LENGTH_HISTOGRAM_REF_MULTIPLE.
    "qc_metrics.summary.median_read_length: <absent in golden> -> 2541.18",
    # C3 — below-threshold split into accumulating vs finish_low_reads. The golden fixture
    # (166 reads, min_reads=1) is comfortably SUFFICIENT, so the new field is None here;
    # the two non-None states are covered by test_c3_low_reads_state.py.
    "report.low_reads_state: <absent in golden> -> None",
    # The U-mask and its selector are gone: every position is scored, so there is no mode
    # to name and nothing excluded. The golden predates the removal and still carries both.
    "snapshots.consensus.mode: 'unmodified' -> <absent in new>",
    "snapshots.consensus.u_positions_excluded: 0 -> <absent in new>",
    "qc_metrics.metrics.sequence_identity.mode: 'unmodified' -> <absent in new>",
    # item 11 — richer poly-A output: Q1/Q3 + under/overflow surfaced into the integrator, and
    # the histogram geometry travels with the distribution so a figure is reconstructable from
    # JSON (bin_counts is the array, registered as an owned prefix below).
    # q1_length read 254.5 until the quartiles became exact (block below).
    "qc_metrics.metrics.polya_tail.q1_length: <absent in golden> -> 253.75",
    "qc_metrics.metrics.polya_tail.q3_length: <absent in golden> -> 317.5",
    "qc_metrics.metrics.polya_tail.underflow: <absent in golden> -> 0",
    "qc_metrics.metrics.polya_tail.overflow: <absent in golden> -> 0",
    # 1 nt bins centred on the integers since 2026-11-09 (denser bins and a curve,
    # 1 nt + 2 nt kernel). Was 0.0 / 500.0 / 5.0 / 100. The range still ends at pt 500, so
    # underflow and overflow are unchanged, and the quartiles are exact either way.
    "snapshots.polya.polya_distribution.bin_min: <absent in golden> -> -0.5",
    "snapshots.polya.polya_distribution.bin_max: <absent in golden> -> 499.5",
    "snapshots.polya.polya_distribution.bin_width: <absent in golden> -> 1.0",
    "snapshots.polya.polya_distribution.n_bins: <absent in golden> -> 500",
    # The figure's curve: the kernel that drew it (smoothed_counts is a prefix below).
    "snapshots.polya.polya_distribution.smoothing: <absent in golden> -> "
    "{'kernel': 'gaussian', 'bandwidth_nt': 2.0}",
    # Exact poly(A) quartiles (2026-10-30): Q1/median/Q3
    # are order statistics of the integer pt, no longer interpolated inside 5-nt bins, which
    # read integer data ~0.5 nt high. Checked against numpy on this fixture's 166 pt values:
    # np.median 286.0, np.percentile 25/75 = 253.75/317.5, so Q3 does not move.
    "qc_metrics.metrics.polya_tail.median_length: 286.67 -> 286.0",
    "snapshots.polya.polya_distribution.median: 286.67 -> 286.0",
    "snapshots.polya.polya_distribution.Q1: 254.5 -> 253.75",
    "snapshots.polya.polya_distribution.quantile_method: <absent in golden> -> 'exact'",
    # poly(A) read rule (2026-11-02): reverse-strand, chimeric (SA) and dorado-split
    # (pi) reads are counted, not measured -- the population the ruler was fitted on. This fixture has none
    # of the three, so only the all-zero counts appear and no number moves.
    "snapshots.polya.excluded: <absent in golden> -> "
    "{'reverse_strand': 0, 'split_mapped_sa': 0, 'split_child_pi': 0}",
    "qc_metrics.metrics.polya_tail.reads_excluded: <absent in golden> -> "
    "{'reverse_strand': 0, 'split_mapped_sa': 0, 'split_child_pi': 0}",
    # item 10, as decided 2026-10-28 (content 6): positions are RANKED, not flagged against a
    # null, and the flat-0.05 dummy null is gone. Nothing on this fixture reaches
    # hotspot_min_depth (max depth 45), so the ranking is empty. Its reads name sup@v5.1.0,
    # and no null is registered for it -- or for any model yet.
    "snapshots.error_hotspot.hotspot_top_n: <absent in golden> -> 20",
    "snapshots.error_hotspot.ranked_by: <absent in golden> -> 'observed_total_error'",
    "snapshots.error_hotspot.null: <absent in golden> -> None",
    "snapshots.error_hotspot.null_reason: <absent in golden> -> 'no null is registered for "
    "rna004_130bps_sup@v5.1.0'",
    "snapshots.error_hotspot.top_positions: <absent in golden> -> []",
    # item 15d — the restart counter is renamed now that it counts real pod5->ubam cache hits
    # (the placeholder n_skipped_restart -> n_basecall_cached). Value is 0 in this fresh bam-mode
    # run either way; only the key name changes.
    "run_accounting.n_basecall_cached: <absent in golden> -> 0",
    "run_accounting.n_skipped_restart: 0 -> <absent in new>",
    # Track B — every construct is now reported instead of only the dominant one. The
    # snapshot bundle gains 4 construct keys + `unassigned` (4 modules -> 9), which is the
    # whole point: on this fixture the discarded constructs held 119 of 166 reads (72 %).
    # The three instrument-telemetry modules then add the tenth through twelfth.
    "modules: list len 4 -> 12",
    "qc_metrics.modules_present: list len 4 -> 12",
    # --- pore occupancy ------------------------------------------------
    # Instrument health from MinKNOW's `pore_activity_*.csv`, the first Core module whose
    # input is not a read. This fixture is a plain directory of BAMs and was never a
    # MinKNOW run, so the honest snapshot is `available: false` WITH THE REASON.
    #
    # The key is registered even then, on purpose: omitting it would make "this build has
    # no such module" and "this run produced no pore data" the same observation, and the
    # second is a fact about the run that a CoA has to carry. Nothing in it names a
    # directory — the path Core was pointed at differs between the offline and real-time
    # entries and across a crash-restart, so a path here would report a changed QC number
    # for an identical run (see test_pore_activity.py).
    "snapshots.pore_activity: <absent in golden> -> {'available': False, "
    "'unavailable_reason': 'no pore_activity_*.csv beside the inputs - not a MinKNOW run "
    "directory, or the run has not yet written one', 'source_file': None}",
    # --- read end reason ----------------------------------------------
    # Same shape and the same reasoning as pore occupancy above: registered even when the
    # file is absent, and nothing in it names a directory. Source is MinKNOW's
    # sequencing_summary rather than the `er:Z` BAM tag, because neither MinKNOW's own
    # bam_pass nor Core's pod5 mode carries that tag.
    "snapshots.end_reason: <absent in golden> -> {'available': False, "
    "'unavailable_reason': 'no sequencing_summary*.txt beside the inputs - not a MinKNOW "
    "run directory, or the run has not yet written one', 'source_file': None}",
    # --- flow-cell bias voltage ---------------------------------------
    # Third and last of the instrument-telemetry modules; same shape and reasoning as the
    # two above. Its absence message differs because absence means something different
    # here: early in a real run the file genuinely does not exist yet, since MinKNOW only
    # writes it at the first mux scan (~93 min in).
    "snapshots.voltage: <absent in golden> -> {'available': False, "
    "'unavailable_reason': 'no other_reports/pore_scan_data_*.csv beside the inputs - not "
    "a MinKNOW run directory, or no mux scan has happened yet', 'source_file': None}",
    # Track B — the flat `metrics` block still describes ONE construct, so it now says
    # which of how many. Without this a panel run's headline identity reads as the run's.
    "qc_metrics.metrics.sequence_identity.n_constructs: <absent in golden> -> 4",
    "qc_metrics.metrics.sequence_identity.scope: <absent in golden> -> "
    "'pooled_over_4_constructs'",
    # --- poly(A) denominator (finding 3) -----------------------------------------------
    # Every length statistic is conditional on dorado having produced a pt estimate, and pt
    # is absent exactly where the estimate FAILED (truncated/degraded reads). The
    # conditioning now travels with the MEASURED numbers instead of only with the no-data
    # state, so a median can no longer be read off a CoA without its denominator. This
    # fixture happens to be 166/166 = 100 % pt coverage, which is why the numbers are clean.
    "qc_metrics.metrics.polya_tail.reads_processed: <absent in golden> -> 166",
    "qc_metrics.metrics.polya_tail.n_with_pt: <absent in golden> -> 166",
    "qc_metrics.metrics.polya_tail.missing_pt: <absent in golden> -> 0",
    "qc_metrics.metrics.polya_tail.pt_coverage: <absent in golden> -> 1.0",
    "snapshots.polya.n_with_pt: <absent in golden> -> 166",
    "snapshots.polya.pt_coverage: <absent in golden> -> 1.0",
    "snapshots.polya.missing_pt_ratio: <absent in golden> -> 0.0",
    # "short" is a judgement against the construct's DESIGNED tail length, so with no
    # target_length configured the metric abstains instead of scoring the run against a
    # self-invented 50 nt cutoff. The old 0.006024 was that invented number.
    "qc_metrics.metrics.polya_tail.short_tail_status: <absent in golden> -> 'not_measured'",
    "qc_metrics.metrics.polya_tail.short_tail_ratio: 0.006024 -> None",
    "qc_metrics.metrics.polya_tail.short_tail_threshold: 50 -> None",
    "qc_metrics.metrics.polya_tail.short_tail_target_length: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.short_tail_ratio_lower: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.short_tail_ratio_upper: <absent in golden> -> None",
    "snapshots.polya.short_tail.status: <absent in golden> -> 'not_measured'",
    "snapshots.polya.short_tail.count: 1 -> None",
    "snapshots.polya.short_tail.ratio: 0.006024 -> None",
    "snapshots.polya.short_tail.threshold: 50 -> None",
    "snapshots.polya.short_tail.target_length: <absent in golden> -> None",
    "snapshots.polya.short_tail.short_fraction: <absent in golden> -> None",
    "snapshots.polya.short_tail.ratio_lower: <absent in golden> -> None",
    "snapshots.polya.short_tail.ratio_upper: <absent in golden> -> None",
    # One flag list (2026-11-04): the variants (one alternative >= 0.5) and item 6's
    # low-consensus sites (purity < 0.9 or consensus != reference, i.e. mismatch > 0.1) were
    # one quantity cut twice. A position is flagged at mismatch rate >= mismatch_threshold
    # (0.5: at most half the reads carry the reference). This supersedes item 6, whose keys
    # are absent again, and the 0.1 -> 0.5 variant default. No position here reaches
    # min_depth 50, so nothing is flagged; the rule is exercised by
    # test_consensus_mismatch_sites.py and, on real reads, test_flag_emphasis_renders.py.
    "snapshots.consensus.variant_freq_threshold: 0.1 -> <absent in new>",
    "snapshots.consensus.variant_count: 0 -> <absent in new>",
    "snapshots.consensus.variants: [] -> <absent in new>",
    "snapshots.consensus.variant_frequencies: [] -> <absent in new>",
    "snapshots.consensus.mismatch_threshold: <absent in golden> -> 0.5",
    "snapshots.consensus.mismatch_site_count: <absent in golden> -> 0",
    "snapshots.consensus.mismatch_sites: <absent in golden> -> []",
    "qc_metrics.metrics.sequence_identity.variant_count: 0 -> <absent in new>",
    "qc_metrics.metrics.sequence_identity.mismatch_site_count: <absent in golden> -> 0",
    "qc_metrics.metrics.sequence_identity.mismatch_threshold: <absent in golden> -> 0.5",
    # Identity's breakdown (2026-11-04): substitution and deletion over identity's own
    # positions and weighting, so with it they add to 100 (here 79.9857 + 12.4957 + 7.5186;
    # 309332 + 48325 + 29077 = 386734 scored), and insertions beside it per 100 scored
    # bases. It replaces qc_metrics' error_profile, whose per-position means weighed every
    # position alike and could not add up with identity; the error_hotspot snapshot keeps
    # them. The flat snapshot is the primary construct's (88583 + 13562 + 8691 = 110836).
    "qc_metrics.metrics.error_profile: {'status': 'measured', 'mean_total_error_rate': "
    "0.231864, 'max_total_error_rate': 1.0, 'max_total_error_pos': 1344, "
    "'mean_mismatch_rate': 0.121823, 'mean_deletion_rate': 0.077772, "
    "'mean_insertion_rate': 0.03227} -> <absent in new>",
    "qc_metrics.metrics.sequence_identity.substitution_pct: <absent in golden> -> 12.4957",
    "qc_metrics.metrics.sequence_identity.deletion_pct: <absent in golden> -> 7.5186",
    "qc_metrics.metrics.sequence_identity.insertions_per_100_bases: <absent in golden> -> "
    "3.2842",
    "qc_metrics.metrics.sequence_identity.substitution_depth: <absent in golden> -> 48325",
    "qc_metrics.metrics.sequence_identity.deletion_depth: <absent in golden> -> 29077",
    "qc_metrics.metrics.sequence_identity.insertion_events: <absent in golden> -> 12701",
    "snapshots.consensus.substitution_pct: <absent in golden> -> 12.2361",
    "snapshots.consensus.deletion_pct: <absent in golden> -> 7.8413",
    "snapshots.consensus.insertions_per_100_bases: <absent in golden> -> 3.3662",
    "snapshots.consensus.identity_substitution_depth: <absent in golden> -> 13562",
    "snapshots.consensus.identity_deletion_depth: <absent in golden> -> 8691",
    "snapshots.consensus.identity_insertion_events: <absent in golden> -> 3731",
    "snapshots.polya.short_tail.reason: <absent in golden> -> "
    "\"no target poly(A) length configured — 'short' is undefined without the designed "
    "tail length. Set module_params.polya.target_length (nt), target_lengths per "
    "construct, or short_threshold for an absolute cutoff.\"",
    # --- error_hotspot: one gate, depth 50 (2026-11-03) ---------------------------
    # min_depth 10 -> 50: positions are reported, per position and in the means, only at
    # the consensus module's depth, and ranked from the same depth. This fixture's max
    # depth is 45, so the whole error profile is no_data here; the rates are exercised on
    # the QK/HA BAM (130/140 reads) in test_item10_hotspot and friends.
    'snapshots.error_hotspot.covered_positions: 2460 -> 0',
    'snapshots.error_hotspot.max_mean_insertion_length: <absent in golden> -> None',
    'snapshots.error_hotspot.max_total_error_pos: 1344 -> -1',
    'snapshots.error_hotspot.max_total_error_rate: 1.0 -> None',
    'snapshots.error_hotspot.mean_deletion_rate: 0.077772 -> None',
    'snapshots.error_hotspot.mean_inserted_bases_rate: <absent in golden> -> None',
    'snapshots.error_hotspot.mean_insertion_rate: 0.03227 -> None',
    # Renamed 2026-11-04: error_hotspot's substitution-only rate is substitution_rate, so
    # that "mismatch" means one thing (substitution + deletion, the consensus module's).
    'snapshots.error_hotspot.mean_mismatch_rate: 0.121823 -> <absent in new>',
    'snapshots.error_hotspot.mean_substitution_rate: <absent in golden> -> None',
    'snapshots.error_hotspot.mean_total_error_rate: 0.231864 -> None',
    'snapshots.error_hotspot.min_depth_threshold: 10 -> 50',
    "snapshots.error_hotspot.hotspot_min_depth: <absent in golden> -> 50",
    "snapshots.error_hotspot.flaggable_positions: <absent in golden> -> 0",
    # --- consensus: per-position emission gated at min_depth -------------
    # The identity DENOMINATOR stays on depth>0 (gating it at 50x drops the thin, high-error
    # 5' end and biases identity up); only the per-position array is gated, so on this
    # max-depth-45 fixture every position emits null. consensus_identity_pct and
    # covered_positions are therefore unchanged from golden and are NOT listed here.
    # --- incomplete-run banner (finding 12) --------------------------------------------
    # `on_error: skip` is the default and exits 0, so a run that lost inputs rendered a
    # clean-looking CoA with the count buried in run.log. The report block now states
    # completeness directly, so a consumer can branch on it without parsing run_accounting.
    # False/0 here: this golden run processes its single input successfully. The banner
    # itself is exercised by test_incomplete_run_banner.py.
    "report.incomplete: <absent in golden> -> False",
    "report.n_failed_inputs: <absent in golden> -> 0",
    # --- ingest integrity (findings 7-11) ----------------------------------------------
    # #7 duplicate primary reads. Zero here (one BAM, ingested once); the detection itself
    # is exercised by test_ingest_integrity.py, which ingests the same BAM twice and
    # asserts 166 reads rather than 332.
    "run_accounting.dedup: <absent in golden> -> {'duplicate_primary_reads': 0, "
    "'status': 'active', 'tracked_read_ids': 166, 'max_tracked_reads': 10000000}",
    # #8 ambiguous reference bases excluded from every error rate. The curlcake reference
    # has no N, so this is 0 and NO existing value moves — the exclusion is a correctness
    # guard for user-supplied references, not a change to this run.
    "snapshots.error_hotspot.ambiguous_ref_positions: <absent in golden> -> 0",
    # --- interim cadence ---------------------------------------------------------------
    # Pre-existing golden staleness, not a code change: the shipped run_config sets
    # cadence.interim_every_n=1, so the default run renders one interim.
    "run_accounting.n_interim_reports: 0 -> 1",
    # poly(A) v2 ruler correction. The golden fixture is sup@v5.1.0, which has no
    # registered constants, so it takes the `uncalibrated` path: raw pt ships and the
    # corrected pair stays empty (A6 -> None, never a copy of raw). NOT ONE EXISTING
    # NUMBER MOVES — the only run whose numbers v2 changes is one on a registered model
    # with a declared arm, which this is not.
    "qc_metrics.metrics.polya_tail.correction_status: <absent in golden> -> 'uncalibrated'",
    "qc_metrics.metrics.polya_tail.correction_applied: <absent in golden> -> False",
    # v2's inputs and its arithmetic, all absent on an uncalibrated run. `model_resolved`
    # is populated because the RG is read regardless — it just resolves to itself here,
    # no alias applying.
    "qc_metrics.metrics.polya_tail.correction_factor: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_shift_nt: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_arm: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_delta_pct: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_range_flags: <absent in golden> -> None",
    # content 5: an f within F_EDGE_MARGIN of the fitted range is said, not flagged. The
    # run-level module has no reference, so no f.
    "qc_metrics.metrics.polya_tail.correction_range_edges: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_model_resolved: <absent in golden> -> "
    "'rna004_130bps_sup@v5.1.0'",
    "qc_metrics.metrics.polya_tail.correction_reason: <absent in golden> -> \"model "
    "'rna004_130bps_sup@v5.1.0' has no registered poly(A) constants, so this is dorado's "
    "raw pt. Registered: ['m1psi_tuned_v1', 'rna004_130bps_hac@v5.3.0']. A constant "
    "fitted on one model does not describe another.\"",
    # Gone with the scalar: speed_correction, calibration_key/_model/_overlap,
    # overlap_verified, source, applies_to. Dropped rather than reported as None —
    # `applies_to: m1psi_only` on a run that corrected nothing described a ruler that was
    # not used. v2 will add its own names.
    #
    # Populated on every run, with or without a correction: the RG is read because the
    # model fixes dorado's OWN poly(A) constants (and so the censoring boundary). v2 needs
    # both — its `offset` IS this offset_correction. The fixture is sup@v5.1.0, a model
    # with no [poly_a] section at all, so it is the unknown-model path and both stay None.
    "qc_metrics.metrics.polya_tail.correction_run_model: <absent in golden> -> "
    "'rna004_130bps_sup@v5.1.0'",
    "qc_metrics.metrics.polya_tail.correction_dorado_speed: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.correction_dorado_offset: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.mean_length_corrected: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.median_length_corrected: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.q1_length_corrected: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.q3_length_corrected: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.std_length_corrected: <absent in golden> -> None",
    # One short-tail count, and this names the ruler that judged it. Raw here, no correction.
    "qc_metrics.metrics.polya_tail.short_tail_judged_on: <absent in golden> -> 'raw_pt'",
    "snapshots.polya.short_tail.judged_on: <absent in golden> -> 'raw_pt'",
    # pt==0 adjudication — dorado writes pt:i:0 with pa=-1 when its poly-A estimate FAILS,
    # so a zero is only a length when the pa boundaries are present. The fixture's 166 reads
    # all carry pt AND pa with real boundaries and none is zero, so no existing number moves;
    # only the new breakdown appears, all zero. The behaviour itself is covered by
    # test_polya_zero_pt.py (the fixture cannot exercise it).
    "snapshots.polya.missing_pt_breakdown: <absent in golden> -> "
    "{'no_tag': 0, 'no_border': 0, 'no_pa': 0}",
    "qc_metrics.metrics.polya_tail.missing_pt_breakdown: <absent in golden> -> "
    "{'no_tag': 0, 'no_border': 0, 'no_pa': 0}",
    # The four scalar-correction registry fields that stood here (correction_calibration_key, _source,
    # _calibration_overlap, _overlap_verified) went with the scalar ruler on 2026-10-23.
    # They are not listed as `-> None` any more because they are no longer emitted at all.
    # Left-censoring axis — a pt of 0 WITH pa boundaries means the formula
    # (window/(spb*speed_correction) - offset_correction) clamped, so the tail is <= 10.88 nt:
    # censored, not missing and not a measured zero. The fixture has no pt==0 read at all, so
    # the counts are 0 and the boundary is the constant read from both model config.tomls.
    "snapshots.polya.n_zero_with_border: <absent in golden> -> 0",
    "snapshots.polya.zero_with_border_ratio: <absent in golden> -> 0.0",
    "snapshots.polya.model_offset_correction_nt: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.n_zero_with_border: <absent in golden> -> 0",
    "qc_metrics.metrics.polya_tail.zero_with_border_ratio: <absent in golden> -> 0.0",
    "qc_metrics.metrics.polya_tail.model_offset_correction_nt: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.short_tail_count_zero_with_border: <absent in golden> -> None",
    "qc_metrics.metrics.polya_tail.short_tail_ratio_denominator: <absent in golden> -> None",
}

# Owned path PREFIXES for parity-breaking items whose diff is array-valued — too many
# elements to enumerate as exact `path: old -> new` strings. A diff is owned when its path
# equals a prefix or continues it with '[' (array element) or '.' (sub-key). Each prefix
# names its owning item; a prefix matching NO diff is stale and fails the test (mirrors the
# EXPECTED_DIFFS "missing" check). Values are intentionally not pinned here — the exact
# scalar summaries that ride the same change stay in EXPECTED_DIFFS above.
OWNED_PREFIXES = {
    # item 9 — local-depth insertion denominator rewrites the insertion-rate array and,
    # through the sum, the total-error-rate array. (item 5 also nulls their uncovered
    # positions; same arrays, same prefix.)
    "snapshots.error_hotspot.insertion_rate": "item 9 + item 5",
    "snapshots.error_hotspot.total_error_rate": "item 9 + item 5",
    # item 5 — uncovered positions (depth==0) now emit JSON null, not a fabricated 0.0. Both
    # modules reconcile on the same depth>0 boundary, so all three arrays null the identical
    # set of no-coverage positions.
    "snapshots.error_hotspot.mismatch_rate": "item 5",
    # ...and renamed substitution_rate on 2026-11-04 (the old key is absent now).
    "snapshots.error_hotspot.substitution_rate": "item 5 + substitution_rate rename",
    "snapshots.error_hotspot.deletion_rate": "item 5",
    "snapshots.consensus.mismatch_rate_by_position": "item 5",
    # item 11 — per-bin poly-A counts for the distribution figure (500 elements since the
    # 1 nt bins; 100 before).
    "snapshots.polya.polya_distribution.bin_counts": "item 11",
    # poly(A) curve — the bins through a fixed 2 nt Gaussian kernel, one value per bin.
    # Drawn, never reported.
    "snapshots.polya.polya_distribution.smoothed_counts": "poly(A) curve",
    # read-length distribution — the data behind the report/GUI read-length figure. The
    # whole subtree is new (absent in golden), so one prefix owns it rather than a pinned
    # 500-element array. Per-construct copies ride the "snapshots.construct:*" prefixes
    # below. Geometry is measured, not chosen: see read_stats.DEFAULTS.
    "snapshots.run_summary.read_length_distribution": "read-length distribution figure",
    # qscore distribution — the accumulator behind quality_score's Q1/median/Q3 has always
    # existed; only those three scalars were emitted, so the SHAPE was computed on every run
    # and discarded. Same contract and same reason for a prefix as read_length_distribution
    # above: the whole subtree is new and it carries a 100-element bin_counts array.
    "snapshots.run_summary.quality_score_distribution": "qscore distribution figure",
    # finding 3 — consensus per-position emission is now gated at min_depth, so on this
    # max-depth-45 fixture the whole array turns null (2540 elements).
    "snapshots.consensus.mismatch_rate_by_position": "min_depth emission gate",
    # #9 — per-position inserted-bases array (new, ref_length elements).
    "snapshots.error_hotspot.inserted_bases_rate": "finding 9 (insertion magnitude)",
    # Track B — one composite snapshot per construct, plus the unassigned bucket and the
    # integrated per-construct sections. Listed per construct rather than as one wildcard
    # prefix on purpose: a FIFTH construct appearing on this fixed 4-record fixture would
    # then be an unexpected diff, which is exactly what it should be.
    "snapshots.construct:cc6m_2244_t7_ecorv": "Track B",
    "snapshots.construct:cc6m_2459_t7_ecorv": "Track B",
    "snapshots.construct:cc6m_2595_t7_ecorv": "Track B",
    "snapshots.construct:cc6m_2709_t7_ecorv": "Track B",
    "snapshots.unassigned": "Track B",
    # QC #4 (terminal reach) — a NEW metric, so the whole subtree is new: the pooled
    # envelope at run level plus one block per construct. Registered as prefixes rather
    # than pinned values because the fixture's numbers are not the point; what is pinned
    # elsewhere (test_full_length_metric.py) is that the run-level entry stays a RANGE with
    # two differently-named denominators. Note this fixture collapses the range to a point
    # (upper == lower == 0.692771) because it has no unmapped reads and no exclusions, so
    # both denominators equal 166 — correct behaviour, and a useful reminder that a
    # collapsed envelope means "nothing was excluded", not "no uncertainty".
    "qc_metrics.metrics.full_length": "QC #4 terminal reach",
    "qc_metrics.constructs": "Track B",
    "qc_metrics.unassigned": "Track B",
    "qc_metrics.construct_accounting": "Track B",
    # scalar correction — the three new poly(A) subtrees (corrected Welford, corrected
    # histogram incl. its 100-bin array, and the correction provenance block). Claimed by
    # prefix rather than enumerated: each is a whole object, and on the default run each is
    # the empty/disabled state.
    "snapshots.polya.polya_length_corrected": "scalar correction",
    "snapshots.polya.polya_distribution_corrected": "scalar correction",
    "snapshots.polya.correction": "scalar correction",
}


def _owning_prefix(path: str) -> str | None:
    """Return the OWNED_PREFIXES key that claims ``path`` (exact / '[' / '.'), else None."""
    for prefix in OWNED_PREFIXES:
        if path == prefix or path.startswith(prefix + "[") or path.startswith(prefix + "."):
            return prefix
    return None


def test_default_run_diff_is_only_owned_changes(
    bam_input_dir: Path, curlcake_ref: Path, tmp_path: Path, golden_doc: dict
) -> None:
    """Default run's diff vs pre_batch golden == exactly the owned/explained changes.

    Green while only expected changes are present; fails loudly if an UNEXPECTED diff
    appears (an unintended regression) — the explained-diff discipline as a test. A diff is
    "owned" if it is an exact EXPECTED_DIFFS string OR its path falls under an OWNED_PREFIXES
    subtree (for array-valued rewrites too large to enumerate).
    """
    out_dir = tmp_path / "out"
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam",
                             "min_reads": 1})
    # The golden numbers were fixed on the fixture's own -k14 alignment (minimap2 2.28),
    # which Core's alignment (-k7 -w5, 2.30) would replace.
    pipe = OfflinePipeline(bam_input_dir, out_dir, cfg, aligner=passthrough_align)
    pipe.run()
    produced = json.loads((out_dir / "qc_result.json").read_text(encoding="utf-8"))
    diffs = set(diff_docs(golden_doc, produced))

    matched_prefixes: set[str] = set()
    unexpected: list[str] = []
    for d in diffs:
        if d in EXPECTED_DIFFS:
            continue
        prefix = _owning_prefix(d.split(": ", 1)[0])
        if prefix is not None:
            matched_prefixes.add(prefix)
            continue
        unexpected.append(d)

    missing = EXPECTED_DIFFS - diffs
    stale_prefixes = set(OWNED_PREFIXES) - matched_prefixes
    assert not unexpected, "UNEXPECTED diff(s) vs golden (regression?):\n" + \
        "\n".join(sorted(unexpected))
    assert not missing, "expected diff(s) no longer present (re-check the fix):\n" + \
        "\n".join(sorted(missing))
    assert not stale_prefixes, "OWNED_PREFIXES entry matched no diff (stale claim?):\n" + \
        "\n".join(sorted(stale_prefixes))


def test_comparator_strips_volatiles_only(golden_doc: dict) -> None:
    """A doc compares equal to itself; provenance/timing/path/reason are ignored."""
    import copy
    other = copy.deepcopy(golden_doc)
    other["provenance"]["git_commit"] = "deadbeef"
    other["timing"]["total_s"] = 999.0
    other["report"]["path"] = "/somewhere/else.pdf"
    other["report"]["reason"] = "user_abort"
    assert diff_docs(golden_doc, other) == []


def test_comparator_catches_metric_change(golden_doc: dict) -> None:
    """A changed QC number IS reported (guards against a comparator that strips too much)."""
    import copy
    other = copy.deepcopy(golden_doc)
    other["qc_metrics"]["metrics"]["sequence_identity"]["consensus_identity_pct"] = -1.0
    diffs = diff_docs(golden_doc, other)
    assert any("consensus_identity_pct" in d for d in diffs)
