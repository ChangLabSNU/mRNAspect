"""Validate the reference FASTA before a run can waste hours on it.

minimap2 accepts a file with no FASTA records: it indexes zero sequences, exits 0, and
every read comes back unmapped in a BAM with no ``@SQ`` lines, which looks like a failed
sequencing run rather than a broken config. Checking the reference at startup turns that
into one accurate error before any GPU time is spent.

Stdlib-only and index-free, so checking the reference writes nothing beside it and trusts
no index that may be stale. (The engine indexes it afresh, privately: _fasta_handle.)
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import IO, Iterator

logger = logging.getLogger(__name__)

_GZIP_MAGIC = b"\x1f\x8b"


class ReferenceError(ValueError):
    """The reference FASTA is unusable. Always raised, never warned."""


def compression(path: Path) -> str:
    """``"plain"``, ``"bgzf"`` or ``"gzip"``, by the file's first bytes.

    BGZF is gzip whose members carry a ``BC`` extra subfield (SAM specification, 4.1),
    which is what lets samtools seek in it. Ordinary gzip has none.
    """
    with open(path, "rb") as probe:
        head = probe.read(16)
    if head[:2] != _GZIP_MAGIC:
        return "plain"
    # FLG.FEXTRA set, then the first subfield: SI1 'B', SI2 'C', SLEN 2.
    if len(head) == 16 and head[3] & 0x04 and head[12:16] == b"BC\x02\x00":
        return "bgzf"
    return "gzip"


def _open_maybe_gzip(path: Path) -> IO[str]:
    """Open a plain or BGZF FASTA as text (BGZF is valid gzip, so gzip reads it)."""
    if compression(path) != "plain":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


#: The only characters a sequence line may hold. N and the other IUPAC codes are
#: positions no read can agree with; anything else (a gap, a digit, a space) minimap2
#: reads as a base and samtools does not, so their coordinates would disagree.
_BASES = frozenset("ACGTUacgtu")


def _iter_records(path: Path) -> Iterator[tuple[str, int]]:
    """Stream ``(header, n_bases)`` per record; never loads a reference into memory.

    Raises:
        ReferenceError: a sequence line holds a character other than A, C, G, T or U.
    """
    header, n_bases = None, 0
    with _open_maybe_gzip(path) as handle:
        for lineno, line in enumerate(handle, 1):
            if line.startswith(">"):
                if header is not None:
                    yield header, n_bases
                header, n_bases = line[1:].strip(), 0
                continue
            seq = line.rstrip("\r\n")
            if header is None or not seq:
                continue
            col = next((i for i, ch in enumerate(seq) if ch not in _BASES), None)
            if col is not None:
                name = header.split()[0] if header.split() else header
                raise ReferenceError(
                    f"Reference FASTA {path}, record {name!r}, line {lineno}, column "
                    f"{col + 1}: {seq[col]!r} is not A, C, G, T or U. Only fully specified "
                    f"sequences are accepted: N or another IUPAC code is a position no "
                    f"read can agree with, and anything else (a gap, a digit, a space) "
                    f"minimap2 reads as a base while samtools does not, so the two would "
                    f"disagree on coordinates.")
            n_bases += len(seq)
    if header is not None:
        yield header, n_bases


def reference_contigs(reference: Path | str, *, max_records: int = 100_000) -> list[str]:
    """Return the contig names in ``reference``, or raise :class:`ReferenceError`.

    The name is everything up to the first whitespace, as minimap2 and samtools derive
    ``@SQ`` ``SN:``, so these are the names reads carry as ``reference_name``.

    Args:
        reference: Path to a FASTA, plain or BGZF-compressed (bgzip).
        max_records: Refuse references with more contigs than this. A construct panel has
            a handful; a whole transcriptome would allocate one pileup per contig.

    Raises:
        ReferenceError: blank, missing, unreadable, empty, compressed with ordinary
            gzip, not FASTA, over
            ``max_records``, with a nameless or empty record, duplicate contig names
            (which would merge two constructs' pileups), or a sequence character other
            than A, C, G, T or U (either case).
    """
    # Before Path(): Path("") is Path("."), an existing directory, so an unset reference
    # would otherwise be reported as "is not a file: .".
    if not str(reference).strip():
        raise ReferenceError(
            "No reference FASTA was given. There is no default: mRNAspect cannot tell a wrong "
            "construct from a dead flow cell, so it will not guess one. Pass --reference, "
            "or set `reference:` in your run configuration.")

    path = Path(reference)
    if not path.exists():
        raise ReferenceError(f"Reference FASTA does not exist: {path}")
    if not path.is_file():
        raise ReferenceError(f"Reference FASTA is not a file: {path}")
    if path.stat().st_size == 0:
        raise ReferenceError(f"Reference FASTA is empty (0 bytes): {path}")
    # minimap2 reads ordinary gzip, so the alignment would succeed; the engine then
    # cannot index the reference, and every read would be put down as a bad read.
    try:
        kind = compression(path)
    except OSError as exc:
        raise ReferenceError(f"Reference FASTA {path} could not be read: {exc}") from exc
    if kind == "gzip":
        raise ReferenceError(
            f"Reference FASTA {path} is compressed with ordinary gzip, which cannot be "
            f"indexed: the alignment would run and no read could then be placed on a "
            f"construct. Decompress it (gunzip), or compress it with bgzip (BGZF) "
            f"instead, and give that file as the reference.")

    names: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    try:
        for header, n_bases in _iter_records(path):
            name = header.split()[0] if header.split() else ""
            if not name:
                raise ReferenceError(
                    f"Reference FASTA {path} has a record with an empty name "
                    f"(a bare '>' line). Every contig needs an identifier.")
            if not n_bases:
                raise ReferenceError(
                    f"Reference FASTA {path}: record {name!r} has no sequence.")
            if name in seen:
                duplicates.add(name)
            seen.add(name)
            names.append(name)
            if len(names) > max_records:
                raise ReferenceError(
                    f"Reference FASTA {path} has more than {max_records:,} contigs. "
                    f"This pipeline allocates per-construct state per contig and is "
                    f"built for construct panels, not whole transcriptomes.")
    except OSError as exc:
        raise ReferenceError(f"Reference FASTA {path} could not be read: {exc}") from exc

    if not names:
        raise ReferenceError(
            f"Reference FASTA {path} contains no FASTA records (no '>' header lines). "
            f"minimap2 accepts this silently — it builds an index over 0 sequences, exits "
            f"0, and every read comes back unmapped in a BAM with no @SQ lines. Check "
            f"that the path points at a FASTA and not at a .fai, an .mmi, an empty file, "
            f"or a truncated download.")
    if duplicates:
        raise ReferenceError(
            f"Reference FASTA {path} has duplicate contig name(s): "
            f"{', '.join(sorted(duplicates))}. Reads cannot be attributed to a construct "
            f"unambiguously, and their pileups would silently merge.")
    return names
