"""The integrated QC result, built from the modules' snapshots.

One run summary, and one entry per QC metric for the run and for each construct. A
metric is ``not_measured`` when its module did not run and ``no_data`` when it ran on
nothing, never a fabricated zero. No pass/fail is applied: acceptance thresholds are
not set, so ``acceptance`` is ``deferred``.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from ...snapshot_keys import (  # noqa: F401  (re-exported: callers import them from here)
    CONSENSUS,
    CONSTRUCT_PREFIX,
    ERROR_HOTSPOT,
    POLYA,
    RUN_SUMMARY,
    UNASSIGNED,
)

# The pooling arithmetic lives with each module, so the report and this result agree.
from .consensus import pooled_identity
from .terminal_reach import pooled_envelope

logger = logging.getLogger(__name__)


def _get(snapshot: Optional[Mapping[str, Any]], *keys: str) -> Any:
    """A nested value from a snapshot, or None (shown as null) if any key is missing."""
    cur: Any = snapshot
    for key in keys:
        if not isinstance(cur, Mapping) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _metric(
    present: bool,
    reason_absent: str,
    *,
    has_data: bool = True,
    reason_nodata: str = "module ran but produced no measurable data",
    nodata_fields: Optional[Mapping[str, Any]] = None,
    **fields: Any,
) -> dict[str, Any]:
    """One metric entry, in one of three states.

    ``not_measured``: the module did not run. ``no_data``: it ran with nothing to
    measure, so the values are withheld and only ``nodata_fields`` are shown.
    ``measured``: the values.
    """
    if not present:
        return {"status": "not_measured", "reason": reason_absent}
    if not has_data:
        return {"status": "no_data", "reason": reason_nodata, **(nodata_fields or {})}
    return {"status": "measured", **fields}


def _identity_metric(cons: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The sequence-identity entry, from a consensus snapshot."""
    return _metric(
        cons is not None,
        "consensus module did not run (no reads mapped to a tracked construct)",
        has_data=_get(cons, "consensus_identity_pct") is not None,
        reason_nodata="no reference positions were covered",
        nodata_fields={"reference": _get(cons, "ref_name"),
                       "covered_positions": _get(cons, "covered_positions")},
        reference=_get(cons, "ref_name"),
        consensus_identity_pct=_get(cons, "consensus_identity_pct"),
        # The rest of the scored depth, and the insertions beside it.
        substitution_pct=_get(cons, "substitution_pct"),
        deletion_pct=_get(cons, "deletion_pct"),
        insertions_per_100_bases=_get(cons, "insertions_per_100_bases"),
        # The positions at mismatch rate >= mismatch_threshold, and the rule.
        mismatch_site_count=_get(cons, "mismatch_site_count"),
        mismatch_threshold=_get(cons, "mismatch_threshold"),
        covered_positions=_get(cons, "covered_positions"),
        total_positions=_get(cons, "total_positions"),
    )


def _polya_metric(pa: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The poly(A) entry, from a polya snapshot."""
    # Say so when every read was excluded by rule.
    left_out = sum((_get(pa, "excluded") or {}).values())
    reason = ("every read was left out: reverse-strand, chimeric or dorado-split"
              if left_out and not _get(pa, "reads_processed")
              else "no reads carried a pt (poly-A) tag")
    return _metric(
        pa is not None,
        "polya module did not run",
        has_data=_get(pa, "polya_distribution", "median") is not None,
        reason_nodata=reason,
        nodata_fields={"reads_processed": _get(pa, "reads_processed"),
                       "reads_excluded": _get(pa, "excluded"),
                       "missing_pt": _get(pa, "missing_pt")},
        median_length=_get(pa, "polya_distribution", "median"),
        q1_length=_get(pa, "polya_distribution", "Q1"),
        q3_length=_get(pa, "polya_distribution", "Q3"),
        underflow=_get(pa, "polya_distribution", "underflow"),
        overflow=_get(pa, "polya_distribution", "overflow"),
        mean_length=_get(pa, "polya_length", "mean"),
        std_length=_get(pa, "polya_length", "std"),
        # The lengths cover only reads with a pt estimate; these give the denominator.
        reads_processed=_get(pa, "reads_processed"),
        # Reverse-strand, chimeric (SA) and dorado-split (pi): not in reads_processed.
        reads_excluded=_get(pa, "excluded"),
        n_with_pt=_get(pa, "n_with_pt"),
        missing_pt=_get(pa, "missing_pt"),
        missing_pt_breakdown=_get(pa, "missing_pt_breakdown"),
        n_zero_with_border=_get(pa, "n_zero_with_border"),
        zero_with_border_ratio=_get(pa, "zero_with_border_ratio"),
        model_offset_correction_nt=_get(pa, "model_offset_correction_nt"),
        pt_coverage=_get(pa, "pt_coverage"),
        short_tail_status=_get(pa, "short_tail", "status"),
        short_tail_ratio=_get(pa, "short_tail", "ratio"),
        short_tail_ratio_lower=_get(pa, "short_tail", "ratio_lower"),
        short_tail_ratio_upper=_get(pa, "short_tail", "ratio_upper"),
        short_tail_threshold=_get(pa, "short_tail", "threshold"),
        short_tail_target_length=_get(pa, "short_tail", "target_length"),
        short_tail_judged_on=_get(pa, "short_tail", "judged_on"),
        short_tail_count_zero_with_border=_get(pa, "short_tail", "count_zero_with_border"),
        short_tail_ratio_denominator=_get(pa, "short_tail", "ratio_denominator"),
        # Beside raw, never instead: has_data stays keyed to the raw median.
        median_length_corrected=_get(pa, "polya_distribution_corrected", "median"),
        q1_length_corrected=_get(pa, "polya_distribution_corrected", "Q1"),
        q3_length_corrected=_get(pa, "polya_distribution_corrected", "Q3"),
        mean_length_corrected=_get(pa, "polya_length_corrected", "mean"),
        std_length_corrected=_get(pa, "polya_length_corrected", "std"),
        correction_status=_get(pa, "correction", "status"),
        correction_applied=_get(pa, "correction", "applied"),
        correction_factor=_get(pa, "correction", "factor"),
        correction_shift_nt=_get(pa, "correction", "shift_nt"),
        correction_arm=_get(pa, "correction", "arm"),
        correction_delta_pct=_get(pa, "correction", "delta_pct"),
        correction_range_flags=_get(pa, "correction", "range_flags"),
        correction_range_edges=_get(pa, "correction", "range_edges"),
        correction_model_resolved=_get(pa, "correction", "run_model_resolved"),
        correction_run_model=_get(pa, "correction", "run_model"),
        correction_dorado_speed=_get(pa, "correction", "dorado_speed_correction"),
        correction_dorado_offset=_get(pa, "correction", "dorado_offset_correction"),
        correction_reason=_get(pa, "correction", "reason"),
    )


def _read_length_metric(rs: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The read-length entry, from a run-summary-shaped snapshot."""
    return _metric(
        rs is not None,
        "run_summary module did not run",
        has_data=_get(rs, "read_length", "mean") is not None,
        reason_nodata="no primary reads with a resolvable length",
        mean=_get(rs, "read_length", "mean"),
        std=_get(rs, "read_length", "std"),
        min=_get(rs, "read_length", "min"),
        max=_get(rs, "read_length", "max"),
        n=_get(rs, "read_length", "n"),
    )


def _construct_full_length_lower(
    tr: Optional[Mapping[str, Any]], n_reads: Optional[int]
) -> Optional[float]:
    """One construct's full-length fraction over ALL its reads, admitted or not.

    The per-construct form of the run-level lower bound (``pooled_envelope``): every
    read terminal_reach did not admit counts as a molecule that fell short.

    Args:
        tr: The construct's terminal_reach snapshot.
        n_reads: Primary reads assigned to the construct (the track's ``n_reads``).

    Returns:
        ``full_length_count / n_reads``, rounded to 6 dp. None when either is missing,
        when ``n_reads`` is 0, and, with a warning, when terminal_reach saw more reads
        (admitted + excluded) than the construct was assigned. The track counts a read
        before any module sees it, so that last case means the snapshot is broken; the
        value is withheld rather than raised, as ``_accounting`` reports a broken
        partition rather than raising, so the rest of the result still reaches disk.
    """
    hit = _get(tr, "full_length_count")
    if hit is None or not n_reads:
        return None
    seen = int(_get(tr, "n_admitted") or 0) + sum((_get(tr, "excluded") or {}).values())
    if seen > n_reads:
        logger.warning(
            "full_length_fraction_lower withheld: terminal_reach saw %d reads "
            "(admitted + excluded) but the construct has only %d assigned, so the "
            "lower end would exceed the fraction", seen, n_reads)
        return None
    return round(hit / n_reads, 6)


def _full_length_metric(
    tr: Optional[Mapping[str, Any]], n_reads: Optional[int] = None
) -> dict[str, Any]:
    """Full-length (5' reach) entry for one construct, from a terminal_reach snapshot.

    ``full_length_fraction`` is over the construct's admitted reads: ``SA``, ``pi`` and
    reverse-strand reads are excluded and counted, which pushes the fraction up.
    ``full_length_fraction_lower`` is over all ``n_reads`` the construct was assigned,
    so the two are the ends of the construct's range. The run-level bounds are in
    ``_pooled_full_length_metric``.
    """
    return _metric(
        tr is not None,
        "terminal_reach module did not run",
        has_data=_get(tr, "full_length_fraction") is not None,
        reason_nodata="no admitted reads (all excluded, or none assigned)",
        nodata_fields={
            "n_admitted": _get(tr, "n_admitted"),
            "excluded": _get(tr, "excluded"),
            "denominator": _get(tr, "denominator"),
        },
        full_length_fraction=_get(tr, "full_length_fraction"),
        # The range's low end: each read not admitted counts as one that fell short.
        full_length_fraction_lower=_construct_full_length_lower(tr, n_reads),
        full_length_count=_get(tr, "full_length_count"),
        n_admitted=_get(tr, "n_admitted"),
        denominator=_get(tr, "denominator"),
        denominator_lower="all_reads_assigned_to_construct",
        # The 5' shortfall a read may have and still count as full length.
        five_prime_max_short_nt=_get(tr, "full_length_5p_max_short_nt"),
        ref_length=_get(tr, "ref_length"),
        five_prime_bins=_get(tr, "five_prime", "counts"),
        five_prime_fractions=_get(tr, "five_prime", "fractions"),
        # 3' reach is a sanity check: a DRS read starts at the 3' end.
        three_prime_bins=_get(tr, "three_prime", "counts"),
        three_prime_fractions=_get(tr, "three_prime", "fractions"),
        excluded=_get(tr, "excluded"),
        # A high rate usually means the reference is reverse-complemented.
        reverse_strand_rate=_get(tr, "reverse_strand_rate"),
    )


def _share_of_reads(
    ref: str, n_reads: Optional[int], total: Optional[int]
) -> Optional[float]:
    """A bucket's reads as a fraction of the run's primary reads, mapped or not.

    The denominator is the count the summary reports as ``total_reads``, so a share can
    be checked against the two numbers beside it. The report's share figure divides by
    the same count.

    Args:
        ref: The construct's reference name (or ``unassigned``), for the warning.
        n_reads: Primary reads in the bucket.
        total: The run's primary reads (``run_summary.mapping.total``).

    Returns:
        ``n_reads / total``, rounded to 6 dp. None when either is missing, when
        ``total`` is 0, and, with a warning, when the bucket holds more reads than the
        run. Every read in a bucket is a primary read that run_summary counted first,
        so that last case means the snapshot is broken; the value is withheld rather
        than raised, as in ``_accounting``.
    """
    if n_reads is None or not total:
        return None
    if n_reads > total:
        logger.warning(
            "share_of_reads withheld for %r: %d reads but the run has only %d, so its "
            "share would exceed 1", ref, n_reads, total)
        return None
    return round(n_reads / total, 6)


def _pooled_identity_metric(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Sequence identity pooled over every construct.

    ``reference`` is None: a pooled value is about no single reference. Each
    construct's own identity is under ``constructs``.
    """
    tracks = [bundle[k] for k in sorted(bundle) if k.startswith(CONSTRUCT_PREFIX)]
    cons = [_get(tr, "consensus") for tr in tracks]
    cons = [c for c in cons if c is not None]
    if not cons:
        return _metric(False, "no construct reported a consensus snapshot (no reads "
                              "mapped to a tracked construct)")
    pooled = pooled_identity(cons)
    if not pooled["ingredients_present"] and any(
            _get(c, "consensus_identity_pct") is not None for c in cons):
        # An older snapshot (e.g. resumed) holds only the rounded percentage, which
        # cannot be pooled.
        return _metric(
            False,
            "snapshot is from an older version: it carries consensus_identity_pct but "
            "not identity_agreeing_depth / identity_scored_depth, so a pooled "
            "run-level identity cannot be reconstructed from it. Re-run to regenerate "
            "the snapshot.")
    return _metric(
        True,
        "",
        has_data=pooled["identity_pct"] is not None,
        reason_nodata="no reference position was covered in any construct",
        nodata_fields={"scored_depth": pooled["scored_depth"],
                       "n_constructs": pooled["n_constructs"]},
        reference=None,
        consensus_identity_pct=pooled["identity_pct"],
        # The rest of the scored depth, pooled the same way, so the three add to 100.
        substitution_pct=pooled["substitution_pct"],
        deletion_pct=pooled["deletion_pct"],
        insertions_per_100_bases=pooled["insertions_per_100_bases"],
        # So the percentages can be re-derived from the JSON.
        agreeing_depth=pooled["agreeing_depth"],
        scored_depth=pooled["scored_depth"],
        substitution_depth=pooled["substitution_depth"],
        deletion_depth=pooled["deletion_depth"],
        insertion_events=pooled["insertion_events"],
        denominator=pooled["denominator"],
        mismatch_site_count=pooled["mismatch_site_count"],
        mismatch_threshold=pooled["mismatch_threshold"],
        covered_positions=pooled["covered_positions"],
        total_positions=pooled["total_positions"],
    )


def _pooled_full_length_metric(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """The run's full-length fraction as two bounds, not one value.

    ``upper`` leaves every read that was not admitted out of the denominator (as
    non-analyte); ``lower`` counts each as a failed molecule. The gap is that
    assumption, not sampling error. DRS captures molecules by their poly(A) tail, so
    neither bound covers molecules without one.
    """
    tracks = [bundle[k] for k in sorted(bundle) if k.startswith(CONSTRUCT_PREFIX)]
    reaches = [_get(tr, "terminal_reach") for tr in tracks]
    reaches = [r for r in reaches if r is not None]
    total_primary = _get(bundle.get(RUN_SUMMARY), "mapping", "total")

    if not reaches:
        return _metric(False, "no construct reported a terminal_reach snapshot")

    env = pooled_envelope(reaches, total_primary)
    return _metric(
        True,
        "",
        has_data=env["fraction_upper"] is not None,
        reason_nodata="no admitted reads in any construct",
        nodata_fields={k: env[k] for k in
                       ("n_admitted", "n_excluded_from_reach", "primary_reads")},
        # The point estimate is the upper bound, so it is never reported alone.
        full_length_fraction_upper=env["fraction_upper"],
        full_length_fraction_lower=env["fraction_lower"],
        **{k: env[k] for k in (
            "full_length_count", "n_admitted", "n_excluded_from_reach", "primary_reads",
            "denominator_upper", "denominator_lower", "bound_type", "population",
            "five_prime_max_short_nt", "n_constructs")},
    )


class QCResultIntegrator:
    """Consolidate a module snapshot bundle into one QC result object."""

    def integrate(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        """The integrated QC result for a ``{module_name: snapshot}`` bundle."""
        rs = bundle.get(RUN_SUMMARY)
        pa = bundle.get(POLYA)
        # The flat CONSENSUS / ERROR_HOTSPOT slots hold one construct, so they are not
        # read here.

        summary = {
            "total_reads": _get(rs, "mapping", "total"),
            "mapped_reads": _get(rs, "mapping", "mapped"),
            "mapping_rate": _get(rs, "mapping", "mapping_rate"),
            # Both: read lengths are right-skewed, so the median is the one to show.
            "mean_read_length": _get(rs, "read_length", "mean"),
            "median_read_length": _get(rs, "read_length_distribution", "median"),
            "mean_qscore": _get(rs, "quality_score", "mean"),
        }

        metrics = {
            "sequence_identity": _pooled_identity_metric(bundle),
            "polya_tail": _polya_metric(pa),
            "full_length": _pooled_full_length_metric(bundle),
            # Supporting metrics, not core QC ones.
            "read_length": _read_length_metric(rs),
        }

        constructs = self._constructs(bundle)
        # Pooled identity names its scope: summed over constructs, weighted by depth.
        if constructs:
            scope = ("pooled_over_%d_constructs" % len(constructs) if len(constructs) > 1
                     else "single_construct")
            metrics["sequence_identity"]["n_constructs"] = len(constructs)
            metrics["sequence_identity"]["scope"] = scope

        return {
            "summary": summary,
            "metrics": metrics,
            "constructs": constructs,
            "unassigned": self._unassigned(bundle),
            "construct_accounting": self._accounting(bundle, constructs),
            "modules_present": sorted(bundle.keys()),
            "acceptance": {
                "status": "deferred",
                "note": "pass/fail thresholds pending domain sign-off; "
                        "integrator reports measured values only",
            },
        }

    # -- Per-construct sections -------------------------------------------------------

    @staticmethod
    def _constructs(bundle: Mapping[str, Any]) -> dict[str, Any]:
        """One entry per construct, keyed by reference name.

        The same metric shapes as the run-level ``metrics``, over that construct's
        reads. Per-position arrays stay in the snapshots.
        """
        out: dict[str, Any] = {}
        total = _get(bundle.get(RUN_SUMMARY), "mapping", "total")
        for key in sorted(bundle):
            if not key.startswith(CONSTRUCT_PREFIX):
                continue
            track = bundle[key]
            ref = key[len(CONSTRUCT_PREFIX):]
            n_reads = _get(track, "n_reads")
            out[ref] = {
                "n_reads": n_reads,
                # Over every primary read in the run, so the constructs' shares and the
                # unassigned reads' share add to 1.
                "share_of_reads": _share_of_reads(ref, n_reads, total),
                # Assigned but not usable by the pileup (malformed CIGAR/SEQ); nonzero
                # means the identity rests on fewer reads than n_reads.
                "n_not_in_pileup": _get(track, "n_not_in_pileup"),
                "mean_depth": _get(track, "pileup", "mean_depth"),
                "coverage_fraction": _get(track, "pileup", "coverage_fraction"),
                "ref_length": _get(track, "pileup", "ref_length"),
                "sequence_identity": _identity_metric(_get(track, "consensus")),
                "polya_tail": _polya_metric(_get(track, "polya")),
                "full_length": _full_length_metric(_get(track, "terminal_reach"),
                                                   n_reads),
                "read_length": _read_length_metric(_get(track, "read_stats")),
                "mean_qscore": _get(track, "read_stats", "quality_score", "mean"),
                # With --secondary=no, MAPQ is the only sign of an ambiguous assignment.
                "assignment": {
                    "mapq_median": _get(track, "mapq", "median"),
                    "mapq_mean": _get(track, "mapq", "mean"),
                    "mapq0_fraction": _get(track, "mapq", "mapq0_fraction"),
                    "low_mapq_fraction": _get(track, "mapq", "low_mapq_fraction"),
                    "low_mapq_threshold": _get(track, "mapq", "low_mapq_threshold"),
                },
            }
        return out

    @staticmethod
    def _unassigned(bundle: Mapping[str, Any]) -> dict[str, Any]:
        """Reads matching no construct: unmapped, or mapped off-reference."""
        track = bundle.get(UNASSIGNED)
        if track is None:
            return {"status": "not_measured",
                    "reason": "engine did not report an unassigned bucket"}
        return {
            "status": "measured",
            "n_reads": _get(track, "n_reads"),
            # Over the run's primary reads, like each construct's share_of_reads, so
            # the GUI's construct table adds up without computing a ratio itself.
            "share_of_reads": _share_of_reads(
                "unassigned", _get(track, "n_reads"),
                _get(bundle.get(RUN_SUMMARY), "mapping", "total")),
            "unmapped": _get(track, "unmapped"),
            "off_reference": _get(track, "off_reference"),
            "off_reference_contigs": _get(track, "off_reference_contigs"),
            "read_length": _read_length_metric(_get(track, "read_stats")),
            "mean_qscore": _get(track, "read_stats", "quality_score", "mean"),
            "polya_tail": _polya_metric(_get(track, "polya")),
        }

    @staticmethod
    def _accounting(
        bundle: Mapping[str, Any], constructs: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Whether the constructs and the unassigned reads add up to the primary reads.

        Recomputed from the serialized snapshots, independently of the engine's own
        check, and reported rather than raised.
        """
        total = _get(bundle.get(RUN_SUMMARY), "mapping", "total")
        by_construct = {ref: c.get("n_reads") for ref, c in constructs.items()}
        unassigned = _get(bundle.get(UNASSIGNED), "n_reads")
        counted = [v for v in list(by_construct.values()) + [unassigned] if v is not None]
        bucketed = sum(counted)
        # Reads the per-read guard rejected may be missing from the buckets.
        unprocessable = _get(bundle.get(UNASSIGNED), "unprocessable") or 0
        residual = None if total is None else total - bucketed
        return {
            "primary_reads": total,
            "bucketed": bucketed,
            "unaccounted": residual,
            "unprocessable": unprocessable,
            "partitions": residual is not None and 0 <= residual <= unprocessable,
            "by_construct": by_construct,
            "unassigned": unassigned,
        }
