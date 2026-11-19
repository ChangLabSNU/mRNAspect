"""Poly(A) tail length from dorado's per-read ``pt`` tag.

Reports the raw length distribution, the same distribution through the per-model
correction in ``polya_calibration``, and, when a designed tail length is configured, the
short-tail rate.

``pt == 0`` is not a length. dorado writes it both when it found no poly(A) region
(``pa`` boundaries of -1) and when its estimate clamped at zero inside a region it did
find. Such reads are counted, kept out of the length statistics, and widen the
short-tail bounds instead of being scored short.
"""

import difflib
import logging
import math
from typing import Any, Iterable, Mapping, Optional

import pysam

from ..polya_calibration import (
    CONSTANTS, DELTA_FIT_FLAGS, DeltaAccumulator, affine_terms, correct_pt,
    delta_flags_match,
    range_edges, range_flags, reference_composition, resolve_model)
from ..streaming_interface import (
    StreamingModule,
    WelfordAccumulator,
    HistogramAccumulator,
    IntegerQuantileAccumulator,
    gaussian_smooth,
    resolve_params,
    validate_histogram,
)

logger = logging.getLogger(__name__)


class MixedModelError(ValueError):
    """The reads name two basecaller models; a run takes one. Never a "bad read"."""

# dorado's own poly(A) constants per model, from each model's config.toml [poly_a]:
# pt = window / (spb * speed_correction) - offset_correction, clamped at 0.
# A model not listed gets none, and what depends on them is withheld, not defaulted.
DORADO_POLYA_CONSTANTS = {
    "rna004_130bps_fast@v5.2.0": {"speed_correction": 0.94, "offset_correction": 8.98},
    "rna004_130bps_fast@v5.3.0": {"speed_correction": 0.961, "offset_correction": 9.5561},
    "rna004_130bps_hac@v5.2.0": {"speed_correction": 0.93, "offset_correction": 9.00},
    "rna004_130bps_hac@v5.3.0": {"speed_correction": 0.92, "offset_correction": 10.88},
    "rna004_130bps_sup@v5.2.0": {"speed_correction": 0.87, "offset_correction": 8.99},
    "rna004_130bps_sup@v5.3.0": {"speed_correction": 0.86, "offset_correction": 10.82},
    "rna004_fast@v6.0.0": {"speed_correction": 0.9297, "offset_correction": 9.8429},
    "rna004_hac@v6.0.0": {"speed_correction": 0.9151, "offset_correction": 11.1852},
    "rna004_sup@v6.0.0": {"speed_correction": 0.8518, "offset_correction": 10.7664},
    # The released fine-tune: the same values as hac@v5.3.0, which it was tuned from.
    # Older names a BAM may carry for it resolve through polya_calibration.ALIASES.
    "m1psi_tuned_v1": {"speed_correction": 0.92, "offset_correction": 10.88},
}


def dorado_constants(model: Optional[str]) -> Optional[dict[str, float]]:
    """dorado's speed/offset constants for a model name (aliases resolved), or None."""
    name, _ = resolve_model(model)
    return DORADO_POLYA_CONSTANTS.get(name or "")


DEFAULTS = {
    "target_length": None,      # nt; the construct's designed poly(A) length
    "short_fraction": None,     # cutoff = target_length * this; no default (it is a spec)
    "short_threshold": None,    # nt; an absolute cutoff, overrides the one above
    # {construct: nt}: the designed length of the named constructs, in place of
    # target_length for them. With any set, no whole-run rate is computed.
    "target_lengths": None,
    # The run's minimap2 flags; delta_pct is used only under -k7 -w5 --secondary=no.
    "minimap2_flags": None,
    # The figure's bins. pt is a whole number of nt, so 1 nt bins centred on the integers
    # hold one value each and bin nothing away. The range ends where the 5 nt bins' did
    # (pt >= 500 is overflow), so underflow and overflow count the same reads.
    "histogram": {"bin_min": -0.5, "bin_max": 499.5, "n_bins": 500},
    # nt; the SD of the Gaussian kernel the figure's curve is drawn with, or None for no
    # curve. Fixed and never below a bin: a rule that narrows with n would sharpen a live
    # curve as reads arrive with no change in the sample. 2 nt is the robust Silverman
    # width of ~4,500-read constructs (1.7-2.4 nt on run 1) and the manuscript's kernel.
    "smoothing_bandwidth_nt": 2.0,
}


def _check_whole_nt_bins(bin_width: float) -> None:
    """Refuse poly(A) bins that are not a whole number of nt.

    pt is an integer, so a bin of any other width holds alternately more and fewer of
    the possible values, and the bars would draw a comb that is not in the reads.
    """
    if bin_width < 1 or abs(bin_width - round(bin_width)) > 1e-9:
        raise ValueError(
            f"polya histogram bins must be a whole number of nt (1 or more), got "
            f"{bin_width:g} nt (bin_max - bin_min over n_bins): pt is an integer, so bins "
            f"of any other width hold unequal numbers of possible values and draw a comb "
            f"that is not in the reads.")


def _resolve_smoothing(value: Any, bin_width: float) -> Optional[float]:
    """The kernel's SD in nt, or None for no curve.

    Raises:
        ValueError: not a number, or narrower than a bin, where it would draw the bins'
            own noise back as shape.
    """
    if value is None:
        return None
    try:
        nt = float(value)
    except (TypeError, ValueError):
        nt = float("nan")
    if isinstance(value, bool) or not (math.isfinite(nt) and nt >= bin_width):
        raise ValueError(
            f"polya smoothing_bandwidth_nt must be a number of nt at least the bin width "
            f"({bin_width:g} nt), or null for no curve; got {value!r}. A kernel narrower "
            f"than a bin draws the bins' own sampling noise as shape.")
    return nt


def _resolve_short_threshold(cfg: Mapping[str, Any]) -> Optional[float]:
    """Resolve the short-tail cutoff (nt), or None when none is configured.

    ``short_threshold`` wins when set; otherwise ``target_length * short_fraction``, which
    needs both: "short" is the manufacturer's spec, so neither has a default.
    """
    target = cfg["target_length"]
    fraction = cfg["short_fraction"]
    absolute = cfg["short_threshold"]

    if target is not None:
        target = float(target)
        if target <= 0:
            raise ValueError(f"polya target_length must be > 0 nt, got {target}")
    if fraction is not None:
        fraction = float(fraction)
        if not 0 < fraction <= 1:
            raise ValueError(
                f"polya short_fraction must be in (0, 1] — a cutoff at or above the target "
                f"would flag a correctly-tailed construct as short; got {fraction}")
    if absolute is not None:
        absolute = float(absolute)
        if absolute < 0:
            raise ValueError(f"polya short_threshold must be >= 0 nt, got {absolute}")
        return absolute
    if target is None or fraction is None:
        return None
    return target * fraction


def resolve_target_lengths(cfg: Optional[Mapping[str, Any]],
                           contigs: Iterable[str]) -> dict[str, float]:
    """Per-construct designed tail lengths from the polya config, validated.

    Raises:
        ValueError: ``target_lengths`` is not a mapping, names a contig the reference
            lacks (a typo would leave that construct on the run-wide target), or gives a
            length that is not a positive number.
    """
    targets = (cfg or {}).get("target_lengths") or {}
    if not isinstance(targets, Mapping):
        raise ValueError(
            f"module_params.polya.target_lengths must be a mapping of construct -> nt, "
            f"e.g. {{construct_B: 90}}; got {type(targets).__name__}")
    known = sorted(str(c) for c in contigs)
    out: dict[str, float] = {}
    for name, value in targets.items():
        name = str(name)
        if name not in known:
            hint = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            suggestion = f" Did you mean {hint[0]!r}?" if hint else ""
            raise ValueError(
                f"module_params.polya.target_lengths names {name!r}, which is not a contig "
                f"in this reference.{suggestion} Known: "
                f"{known if len(known) <= 12 else known[:12] + ['...']}.")
        try:
            nt = float(value)
        except (TypeError, ValueError):
            nt = float("nan")
        if not nt > 0:
            raise ValueError(
                f"module_params.polya.target_lengths[{name}] must be a length > 0 nt, "
                f"got {value!r}")
        out[name] = nt
    return out


class PolyAModule(StreamingModule):
    """Poly(A) tail length over a stream of reads: one construct, or the whole run."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None, *,
                 arm: Optional[str] = None, ref_seq: Optional[str] = None) -> None:
        super().__init__()
        cfg = resolve_params(config, DEFAULTS)

        # 'U' or 'm1psi', from sample.arm / sample.arms; None withholds the correction.
        self._arm = arm
        self._minimap2_flags = cfg["minimap2_flags"]
        self._delta_ok = delta_flags_match(self._minimap2_flags)
        # f and U12, from the construct's reference; None for a run-level instance.
        self._composition = reference_composition(ref_seq) if ref_seq is not None else None

        # With per-construct targets, only the construct instances judge: the engine
        # hands each its own target and no target_lengths, so an instance that still
        # holds them spans constructs of different designs.
        self._per_construct_targets = bool(cfg["target_lengths"])
        self._short_threshold = (None if self._per_construct_targets
                                 else _resolve_short_threshold(cfg))
        self._target_length = cfg["target_length"]
        self._short_fraction = (None if cfg["short_fraction"] is None
                                else float(cfg["short_fraction"]))

        # Resolved once, from the first read that names a model.
        self._constants: Optional[dict[str, Any]] = None
        self._correction_factor: Optional[float] = None
        self._correction_status = "pending"
        self._correction_reason = (
            "no read has named a basecaller model yet, so the constants are unresolved")
        self._run_model: Optional[str] = None
        self._run_model_canonical: Optional[str] = None
        self._model_resolved = False
        self._dorado_speed: Optional[float] = None
        self._dorado_offset: Optional[float] = None

        self._pt_acc = WelfordAccumulator()

        # The bins draw the figure; the quartiles are exact (a count per integer pt).
        pt_min, pt_max, pt_bins = validate_histogram(cfg["histogram"])
        _check_whole_nt_bins((pt_max - pt_min) / pt_bins)
        self._pt_hist = HistogramAccumulator(bin_min=pt_min, bin_max=pt_max, n_bins=pt_bins)
        self._pt_exact = IntegerQuantileAccumulator()
        # The figure's curve: drawn from the bins, so it changes no reported number.
        self._smoothing_nt = _resolve_smoothing(cfg["smoothing_bandwidth_nt"],
                                                self._pt_hist.bin_width)

        # The correction is applied to the summary statistics at snapshot, not per read:
        # it is affine in pt, and delta_pct is a mean over all of the construct's reads.
        self._delta = DeltaAccumulator()

        self._short_count = 0

        # Reads with no poly(A) measurement, by cause.
        self._missing_no_tag = 0     # no pt tag
        self._missing_no_border = 0  # pt == 0 and pa boundaries are -1
        self._missing_no_pa = 0      # pt == 0 and no usable pa tag
        self._warned_no_pa = False

        # pt == 0 inside located pa boundaries: dorado's estimate clamped at 0.
        self._zero_with_border = 0
        self._warned_threshold_below_offset = False

        # Excluded by rule (see update), and in no denominator.
        self._n_reverse = 0
        self._n_split_mapped = 0
        self._n_split_child = 0

    def update(self, read: pysam.AlignedSegment) -> None:
        """Add one read.

        Only forward primary alignments are measured. Reverse-strand, chimeric
        (``SA``) and dorado-split (``pi``) reads are counted under ``excluded``
        instead: their tail cannot be attributed to a specific construct.
        """
        self._check_not_finalized()

        if read.is_supplementary or read.is_secondary:
            return

        # Before the exclusions: a second model stops the run whichever read names it.
        self._track_read_group(read)

        if read.is_reverse:
            self._n_reverse += 1
            return
        if read.has_tag("SA"):
            self._n_split_mapped += 1
            return
        if read.has_tag("pi"):
            self._n_split_child += 1
            return

        self._read_count += 1

        try:
            pt = float(read.get_tag("pt"))
        except KeyError:
            self._missing_no_tag += 1
            return

        # Not a real length; see the module docstring.
        if pt == 0.0:
            self._classify_zero(read)
            return

        # First, so a non-integer pt raises before any accumulator has moved.
        self._pt_exact.update(pt)
        self._pt_acc.update(pt)
        self._pt_hist.update(pt)
        self._delta.update(read)

        # The raw-scale count; _short_tail counts on the corrected scale when it can.
        pt_spec = pt

        if self._short_threshold is not None and pt_spec < self._short_threshold:
            self._short_count += 1

    def _classify_zero(self, read: pysam.AlignedSegment) -> str:
        """Classify a ``pt == 0`` read by cause; it is never a real length.

        ``pa`` boundaries of -1: dorado found no poly(A) region.
        Boundaries present: the region was found and the estimate clamped at 0.
        No usable ``pa`` tag: missing.
        """
        try:
            pa = read.get_tag("pa")
        except KeyError:
            self._missing_no_pa += 1
            if not self._warned_no_pa:
                self._warned_no_pa = True
                logger.warning(
                    "reads carry pt but no pa tag, so a pt of 0 cannot be told apart from a "
                    "failed poly-A estimate. Counting these as missing (not as short tails); "
                    "they are surfaced as missing_pt.no_pa.")
            return "no_pa"
        if len(pa) < 3:
            self._missing_no_pa += 1
            return "no_pa"
        if pa[1] < 0 or pa[2] < 0:
            self._missing_no_border += 1
            return "no_border"
        self._zero_with_border += 1
        return "zero_with_border"

    @property
    def run_model_resolved(self) -> Optional[str]:
        """The run's model name after aliases, or None until a read names one."""
        return self._run_model_canonical

    @property
    def _missing_pt(self) -> int:
        """Total reads with no usable poly-A measurement, whatever the reason."""
        return self._missing_no_tag + self._missing_no_border + self._missing_no_pa

    @staticmethod
    def _model_of(read: pysam.AlignedSegment) -> Optional[str]:
        """Extract the model name from the read group.

        dorado writes RG as ``<uuid>_<model>``. An RG tag that does not split that way
        is returned whole and matches no constants.
        """
        try:
            rg = str(read.get_tag("RG"))
        except KeyError:
            return None
        _uuid, sep, model = rg.partition("_")
        return model if sep and model else rg

    def _track_read_group(self, read: pysam.AlignedSegment) -> None:
        """Take the model from the first read; raise if a later read names another.

        Raises:
            MixedModelError: a read names a model other than the first read's.
        """
        model = self._model_of(read)
        if not self._model_resolved:
            self._model_resolved = True
            self._run_model = model
            known = dorado_constants(model)
            if known:
                self._dorado_speed = known["speed_correction"]
                self._dorado_offset = known["offset_correction"]
            self._resolve_correction(model)
            if not known:
                logger.warning(
                    "model %r is not in DORADO_POLYA_CONSTANTS, so its own speed_correction "
                    "and offset_correction are unknown; the left-censoring boundary is "
                    "withheld. The poly(A) length correction is unaffected — it "
                    "carries its own offset.", model)
            return
        # Compared after aliases: one model's earlier folder name is the same model.
        if model != self._run_model and resolve_model(model)[0] != self._run_model_canonical:
            raise MixedModelError(
                f"the reads name two basecaller models, {self._run_model!r} and "
                f"{model!r}. Every metric depends on the model (the poly(A) correction "
                f"is fitted per model), so a run takes one: run each model's BAMs "
                f"separately.")

    def _resolve_correction(self, model: Optional[str]) -> None:
        """Decide once whether the correction applies; a refusal records why."""
        name, constants = resolve_model(model)
        self._run_model_canonical = name
        comp = self._composition or {}

        if constants is None:
            self._correction_status = "uncalibrated"
            self._correction_reason = (
                f"model {model!r} has no registered poly(A) constants, so this is dorado's "
                f"raw pt. Registered: {sorted(CONSTANTS)}. A constant fitted on one model "
                f"does not describe another.")
            return
        if comp.get("f") is None:
            self._correction_status = "withheld"
            self._correction_reason = (
                "no reference sequence reached this module, so f is unknown")
            return
        if self._arm is None:
            self._correction_status = "withheld"
            self._correction_reason = (
                "no modification declared for this construct (m1\u03a8-substituted or "
                "canonical U): the terminus term applies "
                "on m1\u03a8 and is zero on U, so guessing is wrong by up to ~7 nt either way. "
                "Set sample.arm, or sample.arms for this construct.")
            return
        if self._arm != "U" and comp.get("u12") is None:
            self._correction_status = "withheld"
            self._correction_reason = (
                f"the terminus term needs U12: {comp.get('reason')}")
            return
        if self._arm != "U" and constants["m_delta_prime_per_pct"] and not self._delta_ok:
            self._correction_status = "withheld"
            self._correction_reason = (
                f"model {name!r} has form {constants['form']!r}, so its m1psi factor needs "
                f"delta_pct — and delta_pct was fitted under {' '.join(DELTA_FIT_FLAGS)}, "
                f"which this run's minimap2_flags ({self._minimap2_flags!r}) do not match. "
                "Measured under other flags it is a different quantity with the same name.")
            return

        self._constants = dict(constants)
        self._correction_factor = 1.0
        self._correction_status = "applied"
        self._correction_reason = None

    def _affine(self) -> Optional[tuple[float, float]]:
        """``(F, c)`` for this construct, or None when the correction is not available."""
        if self._constants is None:
            return None
        delta = self._delta.value if self._constants["m_delta_prime_per_pct"] else None
        if self._constants["m_delta_prime_per_pct"] and self._arm != "U" and delta is None:
            return None
        return affine_terms(arm=self._arm, f=self._composition["f"],
                            u12=self._composition["u12"],
                            constants=self._constants, delta_pct=delta)

    def _distribution(self) -> dict[str, Any]:
        """The figure's bins and curve, with the exact quartiles."""
        dist = self._pt_hist.to_dict()
        dist.update(self._pt_exact.to_dict())
        self._add_smoothed(dist, self._pt_hist.bin_width)
        return dist

    def _add_smoothed(self, dist: dict[str, Any], bin_width: float) -> None:
        """Put the figure's curve beside ``dist``'s bins: ``smoothed_counts``, one value
        per bin in reads per bin, and the kernel that drew it.

        ``bin_width`` is in the distribution's own nt, so a corrected distribution, whose
        bins are stretched by F, is smoothed with the same bandwidth on its own axis.
        """
        if self._smoothing_nt is None:
            return
        dist["smoothing"] = {"kernel": "gaussian", "bandwidth_nt": self._smoothing_nt}
        dist["smoothed_counts"] = gaussian_smooth(dist["bin_counts"], bin_width,
                                                  self._smoothing_nt)

    def _corrected_pair(self) -> dict[str, Any]:
        """The raw statistics through ``F * x + c`` (spreads through ``F`` alone).

        Bin counts are unchanged; only the edges move. Zero-filled when no correction
        applies.
        """
        terms = self._affine()
        raw_len = self._pt_acc.to_dict()
        raw_dist = self._distribution()
        if terms is None or raw_len["n"] == 0:
            # Same shape as an empty accumulator: the figure needs the bin list.
            empty_len = {k: (0 if k == "n" else None) for k in raw_len}
            empty_dist = dict(raw_dist)
            empty_dist.update(
                n=0, underflow=0, overflow=0, Q1=None, median=None, Q3=None,
                bin_counts=[0] * len(raw_dist.get("bin_counts") or []))
            if "smoothed_counts" in empty_dist:
                empty_dist["smoothed_counts"] = [0.0] * len(empty_dist["bin_counts"])
            return {"polya_length_corrected": empty_len,
                    "polya_distribution_corrected": empty_dist}
        F, c = terms

        def _m(v):
            return None if v is None else round(F * v + c, 4)

        length = {
            "n": raw_len["n"],
            "mean": _m(raw_len["mean"]),
            "std": None if raw_len["std"] is None else round(F * raw_len["std"], 4),
            "variance": (None if raw_len["variance"] is None
                         else round(F * F * raw_len["variance"], 4)),
            "min": _m(raw_len["min"]),
            "max": _m(raw_len["max"]),
        }
        dist = dict(raw_dist)
        for k in ("Q1", "median", "Q3", "bin_min", "bin_max"):
            if dist.get(k) is not None:
                dist[k] = round(F * dist[k] + c, 2)
        if dist.get("bin_width") is not None:
            dist["bin_width"] = round(F * dist["bin_width"], 4)
        # The curve again on the corrected axis: the bins are F nt wide there, so the
        # same bandwidth in nt spans 1/F as many of them.
        self._add_smoothed(dist, F * self._pt_hist.bin_width)
        return {"polya_length_corrected": length, "polya_distribution_corrected": dist}

    def _composition_block(self, tail_nt: Optional[float] = None) -> dict[str, Any]:
        """The reference-derived inputs, and any that fall past the fitted range.

        ``tail_nt`` is the corrected median, judged against the model's own tail range.
        """
        c = self._composition
        if c is None:
            return {"f": None, "u12": None, "range_flags": None, "range_edges": None}
        support = (self._constants or {}).get("tail_nt_support")
        return {
            "f": round(c["f"], 6) if c["f"] is not None else None,
            "u12": c["u12"],
            "range_flags": range_flags(f=c["f"], u12=c["u12"], tail_nt=tail_nt,
                                       tail_support=support) or None,
            "range_edges": range_edges(f=c["f"], tail_nt=tail_nt,
                                       tail_support=support) or None,
            **({"composition_reason": c["reason"]} if c["reason"] else {}),
        }

    def _needs_delta(self) -> bool:
        return bool(self._constants and self._constants["m_delta_prime_per_pct"]
                    and self._arm != "U")

    def _correction_block(self) -> dict[str, Any]:
        """The correction as applied, and its inputs.

        ``pt_corr = factor * pt + shift_nt``
        """
        terms = self._affine()
        reason = self._correction_reason
        if self._correction_status == "applied" and terms is None:
            # An f+delta model on m1psi also waits for an aligned read's delta_pct.
            reason = ("the constants resolved, but this model's m1psi factor needs "
                      "delta_pct and no aligned read has contributed one yet")
        return {
            "status": self._correction_status,
            "applied": terms is not None,
            "factor": None if terms is None else round(terms[0], 6),
            "shift_nt": None if terms is None else round(terms[1], 4),
            # None where it is not an input: on U, and on an f-form model.
            "delta_pct": self._delta.value if self._needs_delta() else None,
            "arm": self._arm,
            "arm_source": "declared" if self._arm else None,
            **self._composition_block(
                tail_nt=None if terms is None
                else self._corrected_pair()["polya_distribution_corrected"]["median"]),
            # From the RG tag, and after aliases (a BAM may carry an older name).
            "run_model": self._run_model,
            "run_model_resolved": self._run_model_canonical,
            "dorado_speed_correction": self._dorado_speed,
            "dorado_offset_correction": self._dorado_offset,
            "reason": reason,
        }

    def _short_tail(self, n_with_pt: int) -> dict[str, Any]:
        """The short-tail rate, or ``not_measured`` when no cutoff is configured.

        Judged on the corrected length when the correction applies (the cutoff is a
        designed length), on raw ``pt`` otherwise; ``judged_on`` says which.
        ``ratio`` is over reads with a length. ``ratio_lower`` and ``ratio_upper`` are
        over all processed reads, counting every unmeasured read (no pt, or a clamped
        zero) as long and as short respectively, so the true rate lies between them.
        """
        spec = {
            "threshold": self._short_threshold,
            "target_length": self._target_length,
            "short_fraction": self._short_fraction,
            "judged_on": "raw_pt",
        }
        if self._short_threshold is None:
            return {
                "status": "not_measured",
                "reason": (
                    "targets are set per construct (module_params.polya.target_lengths), "
                    "so a rate over all of them would mix designs; see each construct."
                    if self._per_construct_targets else
                    "a target poly(A) length is set but no cutoff: set "
                    "module_params.polya.short_fraction (the fraction of the target below "
                    "which a tail is short) or short_threshold (nt)."
                    if self._target_length is not None else
                    "no target poly(A) length configured — 'short' is undefined without the "
                    "designed tail length. Set module_params.polya.target_length (nt), "
                    "target_lengths per construct, or short_threshold for an absolute cutoff."
                ),
                "count": None, "ratio": None,
                "ratio_lower": None, "ratio_upper": None,
                **spec,
            }
        total, short, missing = self._read_count, self._short_count, self._missing_pt
        terms = self._affine()
        if terms is not None:
            # The correction is affine and pt is counted per exact value, so the count
            # on the corrected scale is exact at any snapshot.
            F, c = terms
            short = sum(n for pt, n in self._pt_exact.counts().items()
                        if F * pt + c < self._short_threshold)
            spec["judged_on"] = "corrected"
        unmeasured = missing + self._zero_with_border
        return {
            "status": "measured",
            "count": short,
            "count_zero_with_border": self._zero_with_border,
            "ratio": round(short / n_with_pt, 6) if n_with_pt else None,
            "ratio_denominator": "reads_with_a_length",
            "ratio_lower": round(short / total, 6) if total else None,
            "ratio_upper": round((short + unmeasured) / total, 6) if total else None,
            **spec,
        }

    def snapshot(self) -> dict[str, Any]:
        """Current metrics; does not change state."""
        n_with_pt = self._pt_acc.n
        total = self._read_count

        return {
            # Over reads with a pt estimate; pt_coverage is their share.
            "polya_length": self._pt_acc.to_dict(),
            "polya_distribution": self._distribution(),
            **self._corrected_pair(),
            "correction": self._correction_block(),
            "short_tail": self._short_tail(n_with_pt),
            "missing_pt": self._missing_pt,
            # pt == 0 inside located boundaries: unmeasured, but not missing.
            "n_zero_with_border": self._zero_with_border,
            "zero_with_border_ratio": (round(self._zero_with_border / total, 6) if total else None),
            # dorado's offset_correction for this model; None when it is not listed.
            "model_offset_correction_nt": self._dorado_offset,
            # no_tag: no pt; no_border: no poly(A) region found; no_pa: no usable pa.
            "missing_pt_breakdown": {
                "no_tag": self._missing_no_tag,
                "no_border": self._missing_no_border,
                "no_pa": self._missing_no_pa,
            },
            "reads_processed": total,
            # Excluded by rule (see update), and in no denominator.
            "excluded": {
                "reverse_strand": self._n_reverse,
                "split_mapped_sa": self._n_split_mapped,
                "split_child_pi": self._n_split_child,
            },
            "n_with_pt": n_with_pt,
            "pt_coverage": round(n_with_pt / total, 6) if total else None,
            "missing_pt_ratio": round(self._missing_pt / total, 6) if total else None,
        }

    def finalize(self) -> dict[str, Any]:
        """Freeze state and return final metrics."""
        self._finalized = True
        return self.snapshot()
