"""Per-position base counts for one reference construct, built read by read.

Each read's CIGAR is walked once into a (ref_length, 7) count array, which the consensus
and error-hotspot modules read without re-parsing reads. Memory is O(ref_length),
whatever the read count. ``M``, ``=`` and ``X`` are treated alike: each aligned read
base is counted under its own letter, and match or mismatch is decided against the
reference sequence, not taken from the CIGAR.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pysam

from .streaming_interface import StreamingModule


# CIGAR operation codes (from pysam/htslib)
_OP_M = 0  # alignment match (match or mismatch)
_OP_I = 1  # insertion to reference
_OP_D = 2  # deletion from reference
_OP_N = 3  # skipped region from reference (splicing)
_OP_S = 4  # soft clip
_OP_H = 5  # hard clip
_OP_EQ = 7  # sequence match (=)
_OP_X = 8  # sequence mismatch (X)

# Operations that consume reference positions
_CONSUMES_REF = {_OP_M, _OP_D, _OP_N, _OP_EQ, _OP_X}
# Operations that consume query (read) positions
_CONSUMES_QUERY = {_OP_M, _OP_I, _OP_S, _OP_EQ, _OP_X}

# Base-to-index mapping for pileup array columns
# Column order: A=0, C=1, G=2, T=3, del=4, ins_events=5, ins_bases=6
_BASE_TO_IDX = {"A": 0, "C": 1, "G": 2, "T": 3, "a": 0, "c": 1, "g": 2, "t": 3}
_IDX_TO_BASE = {0: "A", 1: "C", 2: "G", 3: "T"}
_COL_DEL = 4
_COL_INS = 5        # number of insertion EVENTS anchored at this position
_COL_INS_BASES = 6  # total inserted BASES across those events
_N_COLS = 7


class SharedPileup(StreamingModule):
    """Per-position base and event counts for one reference construct.

    Attributes:
        ref_name: Reference construct name.
        ref_seq: Reference sequence, uppercase, with U written as T.
        ref_length: Length of the reference construct.
        counts: int64 array (ref_length, 7). Columns 0-4 count the reads with A, C, G,
            T or a deletion at each position; column 5 counts the insertion events
            anchored there and column 6 their total inserted bases.
        depth: int64 array (ref_length,), the sum of columns 0-4. Insertions are not
            in depth: they consume no reference position.
    """

    def __init__(self, ref_name: str, ref_seq: str) -> None:
        """Allocate the arrays for one reference construct.

        Args:
            ref_name: Reference sequence name.
            ref_seq: Reference sequence; RNA (U) is accepted.

        Raises:
            ValueError: a base other than A, C, G, T, U or N (any case).
        """
        super().__init__()
        self.ref_name = ref_name
        # U is stored as T, the pileup's column letter. N is allowed; any other letter
        # is rejected, since it would match no column.
        seq = ref_seq.upper()
        bad = sorted(set(seq) - set("ACGTUN"))
        if bad:
            raise ValueError(
                f"reference '{ref_name}' has non-ACGTU/N base(s) {bad}; a nucleotide "
                "reference is required (RNA 'U' accepted and mapped to 'T')")
        self.ref_seq = seq.replace("U", "T")
        self.ref_length = len(self.ref_seq)

        # Core pileup array: position × base/event counts
        self.counts = np.zeros((self.ref_length, _N_COLS), dtype=np.int64)
        # Depth = reads covering each position (sum of A+C+G+T+del)
        self.depth = np.zeros(self.ref_length, dtype=np.int64)

        # What did not enter the pileup, reported in the snapshot.
        self._skipped_wrong_ref = 0
        self._skipped_unmapped = 0
        self._unknown_read_bases = 0  # aligned read bases with no ACGT column (e.g. N)
        # Left out by the read rule (see update).
        self._n_reverse = 0
        self._n_split_mapped = 0
        self._n_split_child = 0

    def update(self, read: pysam.AlignedSegment) -> None:
        """Add one read's aligned bases, deletions and insertions to the counts.

        Secondary and supplementary records are ignored; unmapped reads and reads on
        another contig are counted as skipped. Reverse-strand, chimeric (``SA``) and
        dorado-split (``pi``) reads are counted under ``excluded`` and not piled up, as in
        the poly(A) and full-length modules: none is this construct's molecule read whole.
        The CIGAR is validated before any count changes, so a malformed read raises and
        leaves the arrays untouched.

        Args:
            read: A pysam AlignedSegment from a BAM file.

        Raises:
            ValueError: the CIGAR disagrees with the read's sequence length or reference
                span, or contains an N (skip) operation.
            RuntimeError: If called after finalize().
        """
        self._check_not_finalized()

        if read.is_supplementary or read.is_secondary:
            return

        if read.is_unmapped:
            self._skipped_unmapped += 1
            return

        if read.reference_name != self.ref_name:
            self._skipped_wrong_ref += 1
            return

        if read.is_reverse:
            self._n_reverse += 1
            return
        if read.has_tag("SA"):
            self._n_split_mapped += 1
            return
        if read.has_tag("pi"):
            self._n_split_child += 1
            return

        cigartuples = read.cigartuples
        query_seq = read.query_sequence

        # Validate the whole CIGAR before touching the arrays, so a malformed read
        # raises without leaving partial counts.
        if cigartuples is not None and query_seq is not None:
            # Query-consuming ops (not H: hard-clipped bases are absent from SEQ) must
            # sum to the SEQ length.
            expected_qlen = sum(
                length for op, length in cigartuples if op in _CONSUMES_QUERY)
            if expected_qlen != len(query_seq):
                raise ValueError(
                    f"read {read.query_name!r}: CIGAR query-consumption {expected_qlen} "
                    f"!= SEQ length {len(query_seq)} (malformed alignment)")
            # Reference-consuming ops must reproduce htslib's reference span, or the
            # walk below would misplace every count.
            ref_end = read.reference_end
            if ref_end is not None:
                ref_consumed = sum(
                    length for op, length in cigartuples if op in _CONSUMES_REF)
                if ref_consumed != ref_end - read.reference_start:
                    raise ValueError(
                        f"read {read.query_name!r}: CIGAR reference-consumption {ref_consumed} "
                        f"!= htslib reference span {ref_end - read.reference_start} "
                        "(reference-op classification mismatch)")
            # Constructs have no introns: an N (skip) op means a wrong reference or a
            # spliced aligner.
            if any(op == _OP_N for op, _ in cigartuples):
                raise ValueError(
                    f"read {read.query_name!r}: unexpected CIGAR N (splice/skip) op; "
                    "construct references have no introns — check the reference/aligner")

        self._read_count += 1

        # No CIGAR or SEQ: counted as processed, with nothing to walk.
        if cigartuples is None or query_seq is None:
            return

        # Walk the CIGAR (validated above: no N, query length consistent).
        ref_pos = read.reference_start  # 0-based position on reference
        query_pos = 0  # position on read (query)

        for op, length in cigartuples:
            if op == _OP_M or op == _OP_EQ or op == _OP_X:
                # Match/mismatch: consumes both reference and query.
                for i in range(length):
                    rp = ref_pos + i
                    if 0 <= rp < self.ref_length:
                        base = query_seq[query_pos + i]
                        idx = _BASE_TO_IDX.get(base)
                        if idx is not None:
                            self.counts[rp, idx] += 1
                            self.depth[rp] += 1
                        else:
                            # Not A/C/G/T (e.g. N): not scored, but counted.
                            self._unknown_read_bases += 1
                ref_pos += length
                query_pos += length

            elif op == _OP_I:
                # Insertion: one event at ref_pos, the reference position right after
                # the inserted bases. Depth is unchanged.
                if 0 <= ref_pos < self.ref_length:
                    self.counts[ref_pos, _COL_INS] += 1
                    # And its length, which the event count loses (a deletion's
                    # length is already spread over the positions it covers).
                    self.counts[ref_pos, _COL_INS_BASES] += length
                query_pos += length

            elif op == _OP_D:
                # Deletion: reference positions with no read base. Count as deletion at
                # each skipped position; depth includes deletions.
                for i in range(length):
                    rp = ref_pos + i
                    if 0 <= rp < self.ref_length:
                        self.counts[rp, _COL_DEL] += 1
                        self.depth[rp] += 1
                ref_pos += length

            elif op == _OP_S:
                # Soft clip: bases in query but not aligned; skip on query, ref unchanged.
                query_pos += length

            elif op == _OP_H:
                # Hard clip: bases not in query_sequence at all — nothing to do.
                pass

            else:
                # Unknown operation: skip (N is already rejected pre-walk).
                pass

    def snapshot(self) -> dict[str, Any]:
        """Summary of the pileup: read and skip counts, depth and coverage.

        The full arrays are the ``counts`` and ``depth`` attributes. The key set does
        not depend on the read count; with no reads the depth statistics are zero.

        Returns:
            Dictionary with derived summary statistics.
        """
        result = {
            "ref_name": self.ref_name,
            "ref_length": self.ref_length,
            "reads_processed": self._read_count,
            "skipped_wrong_ref": self._skipped_wrong_ref,
            "skipped_unmapped": self._skipped_unmapped,
            "unknown_read_bases": self._unknown_read_bases,
            "excluded": {
                "reverse_strand": self._n_reverse,
                "split_mapped_sa": self._n_split_mapped,
                "split_child_pi": self._n_split_child,
            },
        }

        # With no reads these are true zeros: no position was covered.
        if self.ref_length:
            result["mean_depth"] = float(np.mean(self.depth))
            result["min_depth"] = int(np.min(self.depth))
            result["max_depth"] = int(np.max(self.depth))
            covered = int(np.sum(self.depth > 0))
        else:
            # An empty reference: np.min/np.max raise on a zero-length array.
            result["mean_depth"] = 0.0
            result["min_depth"] = 0
            result["max_depth"] = 0
            covered = 0
        result["covered_positions"] = covered
        result["coverage_fraction"] = (
            round(covered / self.ref_length, 6) if self.ref_length else 0.0)

        return result

    def finalize(self) -> dict[str, Any]:
        """Freeze state and return final metrics."""
        self._finalized = True
        return self.snapshot()
