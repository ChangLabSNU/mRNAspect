"""The per-model poly(A) length correction and its inputs.

    pt_corr = (pt + offset) * F - offset - E_nt
    F_U     = (1 + b'f) / (1 + b'f_ref)
    F_m1psi = (1 + m_f'f + m_d'delta_pct) / (1 + b'f_ref)
    E_nt    = (intercept + slope * U12) / ruler_ref    on m1psi; 0 on U

``f`` is the U (T) fraction of the whole reference, ``U12`` the U count in its last
12 nt, and ``delta_pct`` the construct's mean (deletions - insertions) per aligned
reference length. U12 is read from the last 12 bases of the record, so the reference
should stop where the poly(A) starts.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

__all__ = [
    "U12_WINDOW_NT", "U12_SUPPORT", "F_SUPPORT", "F_EDGE_MARGIN", "TAIL_EDGE_MARGIN_NT",
    "SHARED", "E_LINE", "CONSTANTS", "ALIASES",
    "reference_composition", "range_flags", "range_edges", "resolve_model", "correct_pt",
    "affine_terms", "DELTA_FIT_FLAGS", "delta_flags_match", "DeltaAccumulator",
]

#: Terminus window for U12, in nt.
U12_WINDOW_NT = 12

#: U12 range the E line was fitted over; outside it the correction is flagged.
U12_SUPPORT = (2, 6)

#: f range the fit covered (the same for both models).
F_SUPPORT = (0.0546448087431694, 0.2485207100591716)

#: How far past F_SUPPORT an f is noted as at the edge rather than flagged as outside.
F_EDGE_MARGIN = 0.005

#: The same, in nt, for a construct's corrected median tail against its model's
#: ``tail_nt_support``.
TAIL_EDGE_MARGIN_NT = 3.0

_U = ("T", "U")


def reference_composition(ref_seq: str) -> dict[str, Any]:
    """Compute the reference composition.

    ``f`` and ``U12`` for one reference, with a reason when one cannot be computed.
    T or U, any case. Ambiguous bases count toward the length but not as U.
    """
    seq = (ref_seq or "").upper()
    n = len(seq)
    if n == 0:
        return {"f": None, "u12": None, "ref_length": 0,
                "reason": "the reference record is empty, so neither f nor U12 is defined"}
    if n < U12_WINDOW_NT:
        # f is still defined; only U12 is not.
        return {"f": sum(seq.count(b) for b in _U) / n, "u12": None, "ref_length": n,
                "reason": (f"the reference is {n} nt, shorter than the {U12_WINDOW_NT}-nt "
                           f"terminus window the E line is defined over")}
    return {
        "f": sum(seq.count(b) for b in _U) / n,
        "u12": sum(seq[-U12_WINDOW_NT:].count(b) for b in _U),
        "ref_length": n,
        "reason": None,
    }


def _past(value: float, lo: float, hi: float) -> float:
    """How far ``value`` lies outside [lo, hi]; 0 inside it."""
    return max(lo - value, value - hi, 0.0)


def _f_past(f: float) -> float:
    return _past(f, *F_SUPPORT)


def range_flags(*, f: Optional[float], u12: Optional[int],
                tail_nt: Optional[float] = None,
                tail_support: Optional[Sequence[float]] = None) -> list[str]:
    """Flag inputs outside the fitted range by more than their margin.

    The correction is still applied; a flag tells the reader it is an extrapolation.
    ``tail_nt`` (a corrected median) is judged only when ``tail_support`` is given.
    """
    out: list[str] = []
    if f is not None and _f_past(f) > F_EDGE_MARGIN:
        out.append(f"f={f:.4f} is outside the fitted range "
                   f"{F_SUPPORT[0]:.4f}–{F_SUPPORT[1]:.4f}, by more than {F_EDGE_MARGIN}")
    if u12 is not None and not (U12_SUPPORT[0] <= u12 <= U12_SUPPORT[1]):
        out.append(f"U12={u12} is outside the measured range {U12_SUPPORT[0]}–"
                   f"{U12_SUPPORT[1]} (the E line was fitted on three termini: 2, 3, 6)")
    if tail_nt is not None and tail_support is not None and _past(
            tail_nt, *tail_support) > TAIL_EDGE_MARGIN_NT:
        out.append(f"the corrected median tail, {tail_nt:.2f} nt, is outside the "
                   f"{tail_support[0]:.2f}–{tail_support[1]:.2f} nt the fit's cells covered, "
                   f"by more than {TAIL_EDGE_MARGIN_NT:g} nt")
    return out


def range_edges(*, f: Optional[float], tail_nt: Optional[float] = None,
                tail_support: Optional[Sequence[float]] = None) -> list[str]:
    """Inputs past the fitted range by no more than their margin: noted, not flagged."""
    out: list[str] = []
    if f is not None and 0 < _f_past(f) <= F_EDGE_MARGIN:
        side, end = ("upper", F_SUPPORT[1]) if f > F_SUPPORT[1] else ("lower", F_SUPPORT[0])
        out.append(f"f={f:.4f} is {_f_past(f):.4f} past the fitted range's {side} end, "
                   f"{end:.4f}")
    if tail_nt is not None and tail_support is not None:
        past = _past(tail_nt, *tail_support)
        if 0 < past <= TAIL_EDGE_MARGIN_NT:
            side, end = (("upper", tail_support[1]) if tail_nt > tail_support[1]
                         else ("lower", tail_support[0]))
            out.append(f"the corrected median tail, {tail_nt:.2f} nt, is {past:.2f} nt past "
                       f"the fitted range's {side} end, {end:.2f}")
    return out


#: Shared by both models' fits; ``offset_correction`` is dorado's own for hac@v5.3.0.
SHARED = {"f_ref": 0.245, "offset_correction": 10.88}

#: E line in samples; each model's ``ruler_ref_sa_per_nt`` converts it to nt.
E_LINE = {"intercept_sa": 36.8230769230769, "slope_sa": 25.834615384615375}

#: Fitted constants per model (provisional), keyed on the model part of the RG tag; a
#: model absent here gets raw ``pt``. ``tail_nt_support`` is the range of corrected
#: tails the fit's cells covered.
CONSTANTS = {
    "rna004_130bps_hac@v5.3.0": {
        "b_prime": -0.3493493409665946,
        "m_f_prime": 0.006881446574111139,
        "m_delta_prime_per_pct": 0.011802198927374807,
        "form": "f+delta",
        "ruler_ref_sa_per_nt": 27.38166856821104,
        "fit": "deployment (M1, f + delta_pct); 47 cells, 8 designs",
        "tail_nt_support": (29.98, 122.55),
    },
    "m1psi_tuned_v1": {
        "b_prime": -0.35844113281777373,
        "m_f_prime": 0.15494468933506658,
        "m_delta_prime_per_pct": 0.0,
        "form": "f",
        "ruler_ref_sa_per_nt": 27.167336928537548,
        "fit": "train8s_hac_5e-6_ep2_w2 V_f full_fit, admit_reach reads; 18 cells, 5 designs",
        "tail_nt_support": (29.98, 122.45),
    },
}

#: Other names a BAM's RG may carry for a registered model: the name of the model's
#: folder when the BAM was basecalled.
ALIASES = {
    "qcrna_m1psi_v1_ov2500": "m1psi_tuned_v1",
    "train8s_hac_5e-6_ep2_w2_ov2500": "m1psi_tuned_v1",
}


def resolve_model(model: Optional[str]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Canonical name and constants for a model, or (name, None) when unregistered."""
    if not model:
        return None, None
    name = ALIASES.get(model, model)
    return name, CONSTANTS.get(name)


def correct_pt(pt: float, *, arm: str, f: float, u12: Optional[int],
               constants: Mapping[str, Any],
               delta_pct: Optional[float] = None) -> float:
    """The corrected tail length (nt) for one ``pt``.

    ``u12`` is needed on m1psi; ``delta_pct`` only by an ``f+delta`` model on m1psi.
    A caller without it must withhold the correction rather than pass 0.
    """
    f_ref = SHARED["f_ref"]
    offset = SHARED["offset_correction"]
    denom = 1.0 + constants["b_prime"] * f_ref

    if arm == "U":
        factor = (1.0 + constants["b_prime"] * f) / denom
        e_nt = 0.0
    else:
        numer = 1.0 + constants["m_f_prime"] * f
        if constants["m_delta_prime_per_pct"]:
            if delta_pct is None:
                raise ValueError(
                    f"this model's form is {constants['form']!r}, so the m1psi factor needs "
                    "delta_pct; withhold the correction instead of defaulting it to 0")
            numer += constants["m_delta_prime_per_pct"] * delta_pct
        factor = numer / denom
        if u12 is None:
            raise ValueError("E_nt needs U12, which this reference did not supply")
        e_nt = ((E_LINE["intercept_sa"] + E_LINE["slope_sa"] * u12)
                / constants["ruler_ref_sa_per_nt"])

    return (pt + offset) * factor - offset - e_nt


def affine_terms(*, arm: str, f: float, u12: Optional[int],
                 constants: Mapping[str, Any],
                 delta_pct: Optional[float] = None) -> tuple[float, float]:
    """``(F, c)`` such that ``pt_corr = F * pt + c``.

    Being affine, the correction can be applied once to summary statistics at snapshot,
    when ``delta_pct``, a mean over the construct's reads, is final.
    """
    offset = SHARED["offset_correction"]
    f_factor = correct_pt(0.0, arm=arm, f=f, u12=u12, constants=constants,
                          delta_pct=delta_pct)
    one = correct_pt(1.0, arm=arm, f=f, u12=u12, constants=constants,
                     delta_pct=delta_pct)
    return one - f_factor, f_factor


#: The minimap2 flags ``delta_pct`` was fitted under. An ``f+delta`` correction is
#: withheld unless the run's flags match; Core's default alignment uses these.
DELTA_FIT_FLAGS = ("-k7", "-w5", "--secondary=no")


def delta_flags_match(minimap2_flags: Optional[str]) -> bool:
    """Whether the run's minimap2 flags include ``DELTA_FIT_FLAGS``.

    Spacing is normalised, so ``-k 7`` and ``-k7`` match.
    """
    if not minimap2_flags:
        return False
    normalised = " ".join(minimap2_flags.split()).replace("-k ", "-k").replace("-w ", "-w")
    return all(flag in normalised for flag in DELTA_FIT_FLAGS)


class DeltaAccumulator:
    """Mean of (deletions - insertions) / aligned reference span x 100 over the reads.

    The denominator is the span on the reference, as in the fit.
    """

    __slots__ = ("_sum", "_n", "_skipped")

    def __init__(self) -> None:
        self._sum = 0.0
        self._n = 0
        self._skipped = 0

    def update(self, read: Any) -> None:
        span = read.reference_length
        cigar = read.cigartuples
        if not span or not cigar:
            self._skipped += 1
            return
        ins = sum(n for op, n in cigar if op == 1)
        dele = sum(n for op, n in cigar if op == 2)
        self._sum += (dele - ins) / span * 100.0
        self._n += 1

    @property
    def n(self) -> int:
        return self._n

    @property
    def value(self) -> Optional[float]:
        return self._sum / self._n if self._n else None

    def to_dict(self) -> dict[str, Any]:
        return {"delta_pct": round(self.value, 6) if self._n else None,
                "n_reads": self._n, "n_skipped": self._skipped}
