"""Per-position error profile of one construct, from its SharedPileup.

Substitution, deletion and insertion rates at each reference position, their summaries, and
the deep-enough positions ranked by error (``top_positions``). Positions are ranked, not
flagged. The ranking is by observed total error, or by the residual over a per-k-mer
expected-error null when ``NULL_TABLES`` holds one for the run's model and this
construct's arm; ``ranked_by`` says which.

A pileup consumer: no ``update(read)``, only ``snapshot()`` and ``finalize()``.
"""

from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import numpy as np

from ..shared_pileup import (
    SharedPileup, _BASE_TO_IDX, _COL_DEL, _COL_INS, _COL_INS_BASES,
)
from ..streaming_interface import resolve_params


#: Per-k-mer expected-error nulls by (model name after aliases, arm): a file name in
#: ``_NULL_DIR``. None is registered yet, so positions are ranked by observed error, as
#: against a null of 0; tables are planned for a later version. A table's header names
#: the model and arm it was built for (``# model: ...``, ``# arm: ...``), and a table
#: whose header disagrees with its key is refused: against another model's or arm's
#: null, positions rank wrongly.
NULL_TABLES: dict[tuple[str, str], str] = {}
_NULL_DIR = Path(__file__).resolve().parents[2] / "config" / "nulls"

# Two depth gates, both 50 by default: min_depth decides which positions are reported,
# per position and in the mean and max rates (the consensus module's gate too), and
# hotspot_min_depth which may be ranked. A rate's sampling error falls as 1/sqrt(depth),
# so thin positions would report and rank noise.
DEFAULTS = {
    "min_depth": 50,
    "hotspot_min_depth": 50,
    "hotspot_top_n": 20,
}


def _load_null_table(path: str) -> tuple[dict[str, float], int, dict[str, str]]:
    """Load a per-k-mer expected-error null (TSV ``kmer<TAB>expected_error``).

    Returns ``(table, k, header)``; ``header`` holds the ``# key: value`` lines.
    Raises on a missing, empty, ragged or malformed file rather than falling back to
    ranking by observed error.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"error_hotspot null table not found: {p}")
    table: dict[str, float] = {}
    header: dict[str, str] = {}
    k: Optional[int] = None
    with p.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if line.startswith("#"):
                key, sep, value = line[1:].partition(":")
                if sep:
                    header[key.strip().lower()] = value.strip()
                continue
            if not line or line.lower().startswith("kmer\t"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise ValueError(f"{p}:{lineno}: expected 'kmer<TAB>rate', got {line!r}")
            kmer, rate = parts[0].upper(), float(parts[1])
            if k is None:
                k = len(kmer)
            elif len(kmer) != k:
                raise ValueError(f"{p}:{lineno}: k-mer {kmer!r} length != {k} (ragged table)")
            table[kmer] = rate
    if not table:
        raise ValueError(f"{p}: null table is empty")
    return table, k, header


def _round6(value):
    """Round to 6 decimal places; None stays None."""
    return None if value is None else round(value, 6)


def _mask_uncovered(rate, reported):
    """``rate`` as a list, None where the position is not reported."""
    return [float(v) if m else None for v, m in zip(rate.tolist(), reported.tolist())]


class ErrorHotspotModule:
    """Per-position error decomposition of one construct, computed at each snapshot.

    Args:
        pileup: the construct's SharedPileup.
        config: overrides of ``DEFAULTS``.
        arm: this construct's declared arm, or None. A null is per arm.
        model_of: returns the run's model name (aliases resolved), or None until a read
            names one. A callable because this module sees no reads.
    """

    def __init__(self, pileup: SharedPileup,
                 config: Optional[Mapping[str, Any]] = None, *,
                 arm: Optional[str] = None,
                 model_of: Optional[Callable[[], Optional[str]]] = None) -> None:
        cfg = resolve_params(config, DEFAULTS)
        self.pileup = pileup
        self.min_depth = int(cfg["min_depth"])
        self.hotspot_min_depth = int(cfg["hotspot_min_depth"])
        if self.hotspot_min_depth < self.min_depth:
            raise ValueError(
                f"hotspot_min_depth ({self.hotspot_min_depth}) must be >= min_depth "
                f"({self.min_depth}): ranking a position the summary layer considers too "
                f"thin to describe would accuse on evidence it declines to report")
        self.top_n = int(cfg["hotspot_top_n"])
        self._arm = arm
        self._model_of = model_of
        self._nulls: dict[tuple[str, str], tuple[dict[str, float], int]] = {}
        self._finalized = False

    def _null(self) -> tuple[Optional[tuple[str, str, str]], Optional[str]]:
        """``((model, arm, file), None)`` for this construct's null, or ``(None, why)``."""
        model = self._model_of() if self._model_of else None
        if model is None:
            return None, "the reads name no basecall model, so no null can be chosen"
        if not any(m == model for m, _arm in NULL_TABLES):
            return None, f"no null is registered for {model}"
        if self._arm is None:
            return None, "no arm declared for this construct, and a null is per arm"
        name = NULL_TABLES.get((model, self._arm))
        if name is None:
            return None, f"no null is registered for {model} on the {self._arm} arm"
        return (model, self._arm, name), None

    def _table(self, model: str, arm: str, name: str) -> tuple[dict[str, float], int]:
        """The null's k-mer table, loaded once; raises if its header disagrees."""
        if (model, arm) not in self._nulls:
            table, k, header = _load_null_table(str(_NULL_DIR / name))
            said = (header.get("model"), header.get("arm"))
            if said != (model, arm):
                raise ValueError(
                    f"null table {name} is registered for {model} on the {arm} arm but its "
                    f"header says model={said[0]!r}, arm={said[1]!r}. Ranking against "
                    f"another model's or arm's expected error orders positions wrongly.")
            self._nulls[(model, arm)] = (table, k)
        return self._nulls[(model, arm)]

    def snapshot(self) -> dict[str, Any]:
        """Per-position rates and their summaries, from the pileup as it stands."""
        pileup = self.pileup
        ref_length = pileup.ref_length
        depth = pileup.depth
        counts = pileup.counts

        # -1 where the reference base is not A/C/G/T (e.g. N).
        ref_indices = np.array(
            [_BASE_TO_IDX.get(b, -1) for b in pileup.ref_seq], dtype=np.int64
        )

        # Depth 0 divides by 1: those rates are 0.0 here and None in the output.
        safe_depth = np.maximum(depth, 1).astype(np.float64)

        # has_depth: the position is reported, per position and in the summaries.
        # flaggable: it may be ranked. A reference N has no base to disagree with, so it
        # is in neither; ambiguous_ref_positions counts them.
        ref_is_known = ref_indices >= 0
        has_depth = (depth >= self.min_depth) & ref_is_known
        flaggable = (depth >= self.hotspot_min_depth) & ref_is_known

        # --- Substitution rate ---
        # (depth - ref_base_count - del_count) / depth, where depth includes deletions:
        # reads carrying another base. Not the consensus module's mismatch rate, which
        # counts deletions too.
        ref_base_counts = np.zeros(ref_length, dtype=np.int64)
        ref_base_counts[ref_is_known] = counts[
            np.arange(ref_length)[ref_is_known], ref_indices[ref_is_known]
        ]
        del_counts = counts[:, _COL_DEL]
        substitution_counts = depth - ref_base_counts - del_counts
        substitution_rate = substitution_counts.astype(np.float64) / safe_depth

        # --- Deletion rate ---
        deletion_rate = del_counts.astype(np.float64) / safe_depth

        # --- Insertion rate ---
        # Insertion events at a position (the one just after the inserted bases) over
        # that position's depth, so the three rates share one denominator.
        ins_counts = counts[:, _COL_INS]
        insertion_rate = ins_counts.astype(np.float64) / safe_depth
        # Insertion size: inserted bases per covering read, and the mean insertion
        # length (NaN where none). Kept out of total_error_rate, which counts events.
        ins_base_counts = counts[:, _COL_INS_BASES]
        inserted_bases_rate = ins_base_counts.astype(np.float64) / safe_depth
        with np.errstate(divide="ignore", invalid="ignore"):
            mean_insertion_length = np.where(
                ins_counts > 0,
                ins_base_counts / np.maximum(ins_counts, 1), np.nan)

        # --- Total error rate ---
        total_error_rate = substitution_rate + deletion_rate + insertion_rate

        # --- Ranking ---
        # Every flaggable position is ranked and the top N reported: by observed total
        # error, or by the residual over the null when there is one. The null is
        # looked up by the reference k-mer centred on the position (k odd); the k//2
        # positions at each end, and k-mers missing from the table (e.g. spanning an
        # N), are left out.
        null, null_reason = self._null()
        table, half = None, 0
        if null is not None:
            table, k = self._table(*null)
            half = k // 2
        ref_seq = pileup.ref_seq
        ranked = []
        for pos in np.flatnonzero(flaggable):
            entry = {
                "pos": int(pos),
                "observed_error_rate": round(float(total_error_rate[pos]), 6),
                "depth": int(depth[pos]),
                "substitution_rate": round(float(substitution_rate[pos]), 6),
                "deletion_rate": round(float(deletion_rate[pos]), 6),
                "insertion_rate": round(float(insertion_rate[pos]), 6),
            }
            if table is not None:
                if pos < half or pos + half >= ref_length:
                    continue
                kmer = ref_seq[pos - half: pos + half + 1]
                expected = table.get(kmer)
                if expected is None:
                    continue
                entry.update(kmer=kmer, null_expected=round(expected, 6),
                             residual=round(float(total_error_rate[pos]) - expected, 6))
            ranked.append(entry)
        ranked_by = "observed_total_error" if table is None else "residual_over_null"
        # Stable, so equal values keep reference order.
        ranked.sort(key=lambda h: h["residual" if table is not None else
                                    "observed_error_rate"], reverse=True)
        top_positions = ranked[: self.top_n]

        # --- Summaries, over positions at min_depth or deeper ---
        covered = has_depth
        n_covered = int(np.sum(covered))

        if n_covered > 0:
            mean_substitution = float(np.mean(substitution_rate[covered]))
            mean_deletion = float(np.mean(deletion_rate[covered]))
            mean_insertion = float(np.mean(insertion_rate[covered]))
            mean_ins_bases = float(np.mean(inserted_bases_rate[covered]))
            gated_lengths = mean_insertion_length[covered]
            gated_lengths = gated_lengths[~np.isnan(gated_lengths)]
            max_ins_len = float(np.max(gated_lengths)) if gated_lengths.size else None
            mean_total = float(np.mean(total_error_rate[covered]))
            max_total = float(np.max(total_error_rate[covered]))
            # -1 when no such position has any error, rather than position 0.
            if max_total > 0.0:
                covered_idx = np.flatnonzero(covered)
                max_total_pos = int(covered_idx[np.argmax(total_error_rate[covered])])
            else:
                max_total_pos = -1
        else:
            # Nothing at min_depth: the rates are None, and -1 means no position.
            mean_substitution = mean_deletion = mean_insertion = None
            mean_ins_bases = max_ins_len = None
            mean_total = max_total = None
            max_total_pos = -1

        return {
            "ref_name": pileup.ref_name,
            "min_depth_threshold": self.min_depth,
            # The ranking gate, and how many positions passed it.
            "hotspot_min_depth": self.hotspot_min_depth,
            "flaggable_positions": int(np.sum(flaggable)),
            "covered_positions": n_covered,
            # Reference positions not A/C/G/T (e.g. N), excluded from every rate.
            "ambiguous_ref_positions": int(np.sum(~ref_is_known)),
            "reads_in_pileup": pileup._read_count,
            # What the ranking was against: the null's model, arm and file, or None and
            # why there is none.
            "ranked_by": ranked_by,
            "null": (None if null is None
                     else {"model": null[0], "arm": null[1], "table": null[2]}),
            "null_reason": null_reason,
            "hotspot_top_n": self.top_n,
            "top_positions": top_positions,
            # Summaries (None when no position reached min_depth).
            "mean_substitution_rate": _round6(mean_substitution),
            "mean_deletion_rate": _round6(mean_deletion),
            "mean_insertion_rate": _round6(mean_insertion),
            # Inserted bases per covering read, and the largest per-position mean
            # insertion length (a sign of homopolymer slippage).
            "mean_inserted_bases_rate": _round6(mean_ins_bases),
            "max_mean_insertion_length": _round6(max_ins_len),
            "mean_total_error_rate": _round6(mean_total),
            "max_total_error_rate": _round6(max_total),
            "max_total_error_pos": max_total_pos,
            # Per position, for plotting; None where depth is 0 or the base is N.
            "substitution_rate": _mask_uncovered(substitution_rate, has_depth),
            "deletion_rate": _mask_uncovered(deletion_rate, has_depth),
            "insertion_rate": _mask_uncovered(insertion_rate, has_depth),
            "inserted_bases_rate": _mask_uncovered(inserted_bases_rate, has_depth),
            "total_error_rate": _mask_uncovered(total_error_rate, has_depth),
        }

    def finalize(self) -> dict[str, Any]:
        """Freeze and return final metrics."""
        self._finalized = True
        return self.snapshot()
