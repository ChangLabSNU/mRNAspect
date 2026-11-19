"""Append-only ingest manifest: the real-time loop's replay log.

A QC engine is only ever fed one monotone sequence of inputs, starting empty. A restart
builds a fresh engine and replays the sequence rather than resuming one, so no module's
accumulator state is ever serialized.

The manifest is not a skip-set. The POD5 directory decides which inputs belong in the
report, and a fresh engine plus a process-local set ensure each is ingested once; the
manifest only decides how much work a restart can skip. Without it a restart is slower,
not wrong.

Each line records ``primary_total_after``, ``engine.primary_total`` right after that
input. Replay checks it against the rebuilt engine and raises ``ManifestIntegrityError``
on a mismatch (a truncated BAM, a duplicated line, a change in how reads are counted);
the redundant ``n_primary`` must equal the difference of consecutive totals. A missing
BAM is not an error: replay stops there and the caller re-derives the rest from POD5.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

MANIFEST_NAME = "ingest_manifest.tsv"
SCHEMA_VERSION = 1

_COLUMNS = ("seq", "pod5_name", "aligned_bam", "n_records", "n_primary",
            "primary_total_after")
_HEADER = "\t".join(_COLUMNS)
_SCHEMA_LINE = f"# mrnaspect ingest_manifest schema_version={SCHEMA_VERSION}"


class ManifestIntegrityError(RuntimeError):
    """The manifest disagrees with itself or with the engine. Always fatal."""


class _Engine(Protocol):
    """What replay needs of ``AnalysisEngine`` (structural, so tests can fake it)."""

    primary_total: int

    def ingest_bam(self, bam_path: Path) -> int: ...


@dataclass(frozen=True)
class ManifestEntry:
    """One manifest line."""

    seq: int
    pod5_name: str
    aligned_bam: str
    """Relative to the output root when inside it, so the output stays relocatable."""
    n_records: int
    n_primary: int
    primary_total_after: int

    def to_line(self) -> str:
        return "\t".join((str(self.seq), self.pod5_name, self.aligned_bam,
                          str(self.n_records), str(self.n_primary),
                          str(self.primary_total_after)))

    @classmethod
    def from_line(cls, line: str) -> "ManifestEntry":
        parts = line.rstrip("\n").split("\t")
        if len(parts) != len(_COLUMNS):
            raise ValueError(f"expected {len(_COLUMNS)} columns, got {len(parts)}")
        return cls(seq=int(parts[0]), pod5_name=parts[1], aligned_bam=parts[2],
                   n_records=int(parts[3]), n_primary=int(parts[4]),
                   primary_total_after=int(parts[5]))


@dataclass
class ReplayReport:
    """What a replay re-ingested, and where and why it stopped if it stopped early."""

    replayed: list[ManifestEntry]
    primary_total: int
    stopped_at: Optional[ManifestEntry] = None
    stop_reason: Optional[str] = None
    torn_tail: bool = False

    @property
    def n_replayed(self) -> int:
        return len(self.replayed)

    @property
    def replayed_pod5s(self) -> set[str]:
        return {e.pod5_name for e in self.replayed}


class IngestManifest:
    """Append-only record of what has been ingested, in ingest order.

    BAM paths are stored relative to ``root``, the output root.
    """

    def __init__(self, path: Path | str, *, root: Path | str) -> None:
        self.path = Path(path)
        self.root = Path(root)
        self._next_seq = 0
        self._last_total = 0

    # -- writing ----------------------------------------------------------

    def append(self, *, pod5_name: str, aligned_bam: Path, n_records: int,
               primary_total_after: int) -> ManifestEntry:
        """Record one ingested input; the line is fsynced before this returns.

        Raises ``ManifestIntegrityError`` if ``primary_total_after`` went backwards.
        """
        entry = ManifestEntry(
            seq=self._next_seq,
            pod5_name=pod5_name,
            aligned_bam=self._relpath(aligned_bam),
            n_records=int(n_records),
            n_primary=int(primary_total_after) - self._last_total,
            primary_total_after=int(primary_total_after),
        )
        if entry.n_primary < 0:
            raise ManifestIntegrityError(
                f"primary_total went backwards ({self._last_total} -> "
                f"{primary_total_after}) at {pod5_name}; the engine is not monotone")

        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            if new_file:
                fh.write(f"{_SCHEMA_LINE}\n{_HEADER}\n")
            fh.write(entry.to_line() + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        self._next_seq += 1
        self._last_total = entry.primary_total_after
        return entry

    def _relpath(self, bam: Path) -> str:
        bam = Path(bam)
        try:
            return str(bam.resolve().relative_to(self.root.resolve()))
        except ValueError:
            # Outside the output root (e.g. bam-mode inputs): stored absolute.
            return str(bam.resolve())

    def resolve(self, entry: ManifestEntry) -> Path:
        p = Path(entry.aligned_bam)
        return p if p.is_absolute() else self.root / p

    # -- reading ----------------------------------------------------------

    def read(self) -> tuple[list[ManifestEntry], bool]:
        """Parse the manifest into ``(entries, torn_tail)``.

        A torn or unparseable final line (a crash mid-append) is dropped and flagged; a
        malformed line elsewhere, or a shape error, raises ``ManifestIntegrityError``.
        With entries, also sets the next ``seq`` and running total from the last one.
        """
        if not self.path.exists():
            return [], False
        raw = self.path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True)
        body = [ln for ln in raw if not ln.startswith("#") and not ln.startswith("seq\t")]

        entries: list[ManifestEntry] = []
        torn = False
        for i, line in enumerate(body):
            is_last = i == len(body) - 1
            if is_last and not line.endswith("\n"):
                torn = True
                logger.warning(
                    "Manifest %s has a torn final line (crash mid-append); dropping it. "
                    "That input will simply be re-derived.", self.path.name)
                break
            try:
                entries.append(ManifestEntry.from_line(line))
            except ValueError as exc:
                if is_last:
                    torn = True
                    logger.warning("Manifest %s: unparseable final line (%s); dropping it",
                                   self.path.name, exc)
                    break
                raise ManifestIntegrityError(
                    f"{self.path}: line {i + 1} of {len(body)} is malformed ({exc}). Only "
                    f"the final line may be torn; a mid-file error means corruption.")

        self._verify_shape(entries)
        if entries:
            self._next_seq = entries[-1].seq + 1
            self._last_total = entries[-1].primary_total_after
        return entries, torn

    def _rewrite(self, entries: Sequence[ManifestEntry]) -> None:
        """Rewrite the file to exactly ``entries``, atomically, and reset the counters.

        Used after a short replay, so the log describes what the engine holds; otherwise
        the next ``append`` would take its delta from a total the engine never reached.
        The dropped inputs are re-derived from POD5 and appended again.
        """
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"{_SCHEMA_LINE}\n{_HEADER}\n")
            for entry in entries:
                fh.write(entry.to_line() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self._next_seq = entries[-1].seq + 1 if entries else 0
        self._last_total = entries[-1].primary_total_after if entries else 0

    def _verify_shape(self, entries: Sequence[ManifestEntry]) -> None:
        """Check seq order, ``n_primary`` deltas and duplicate POD5s; opens no BAM."""
        prev_total = 0
        for i, e in enumerate(entries):
            if e.seq != i:
                raise ManifestIntegrityError(
                    f"{self.path}: seq must be 0,1,2,... — entry {i} has seq={e.seq}. "
                    f"Lines were reordered, spliced, or two writers shared the file.")
            expected = e.primary_total_after - prev_total
            if e.n_primary != expected:
                raise ManifestIntegrityError(
                    f"{self.path}: entry {i} ({e.pod5_name}) records n_primary="
                    f"{e.n_primary} but its cumulative total implies {expected}. The "
                    f"redundant column disagrees with the running total.")
            prev_total = e.primary_total_after
        seen = {e.pod5_name for e in entries}
        if len(seen) != len(entries):
            dupes = sorted({e.pod5_name for e in entries
                            if [x.pod5_name for x in entries].count(e.pod5_name) > 1})
            raise ManifestIntegrityError(
                f"{self.path}: POD5(s) recorded more than once: {dupes}. Replaying would "
                f"double-count their reads.")

    # -- replay -----------------------------------------------------------

    def replay(self, engine: _Engine) -> ReplayReport:
        """Re-ingest the longest usable prefix into an empty engine, checking totals.

        Stops at the first missing BAM. If it stopped early or the last line was torn,
        the file is rewritten to the replayed prefix.
        """
        if engine.primary_total != 0:
            raise ManifestIntegrityError(
                f"replay() needs a FRESH engine but this one already holds "
                f"{engine.primary_total} primary reads. Replaying into a used engine would "
                f"double-count; construct a new one.")

        entries, torn = self.read()
        report = ReplayReport(replayed=[], primary_total=0, torn_tail=torn)
        for entry in entries:
            bam = self.resolve(entry)
            if not bam.exists():
                # Not an error: the caller re-derives the rest from the POD5s.
                report.stopped_at = entry
                report.stop_reason = f"aligned BAM missing: {bam}"
                logger.warning(
                    "Replay stops at seq=%d (%s): %s. The remaining %d input(s) will be "
                    "re-derived from POD5.", entry.seq, entry.pod5_name,
                    report.stop_reason, len(entries) - len(report.replayed))
                break

            engine.ingest_bam(bam)
            actual = engine.primary_total
            if actual != entry.primary_total_after:
                # An error: the BAM is there but no longer gives the recorded total.
                raise ManifestIntegrityError(
                    f"{self.path}: replaying seq={entry.seq} ({entry.pod5_name}) gave "
                    f"primary_total={actual}, but the manifest recorded "
                    f"{entry.primary_total_after}. The aligned BAM, the manifest, or the "
                    f"counting code has changed since it was written. Refusing to emit a "
                    f"report whose numbers disagree with its own audit trail.")
            report.replayed.append(entry)
            report.primary_total = actual

        if report.n_replayed != len(entries) or torn:
            # Make the log describe what the engine holds (see _rewrite).
            logger.warning(
                "Truncating manifest from %d to %d entry/entries so it describes the "
                "engine; the dropped input(s) will be re-derived and re-appended.",
                len(entries), report.n_replayed)
            self._rewrite(report.replayed)

        logger.info("Replayed %d/%d manifest entries (%d primary reads)%s",
                    report.n_replayed, len(entries), report.primary_total,
                    " — truncated" if report.stopped_at else "")
        return report

    # -- introspection ----------------------------------------------------

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def recorded_pod5s(self) -> set[str]:
        """POD5 names in the manifest; for reporting, never to decide what to ingest."""
        entries, _ = self.read()
        return {e.pod5_name for e in entries}
