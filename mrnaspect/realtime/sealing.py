"""Run-end sealing: the final sweep, the coverage check and the disposition accounting.

MinKNOW writes ``final_summary_*.txt`` after the last POD5, so once the marker exists no
new POD5 will appear: the input set is closed. That does not make every POD5 ready; the
last one lands seconds before the marker and may still be flushing. Sealing therefore
sweeps the POD5 directory again, with a shorter stability window than the live watcher,
since it only has to wait out a flush.

At finalize every observed POD5 goes into exactly one disposition bucket, and
:func:`build_run_accounting` raises if one is left out: a report may be partial, but
never silently.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from .watcher import DEFAULT_EXCLUDE_SUFFIXES, DEFAULT_POD5_PATTERN

logger = logging.getLogger(__name__)

DEFAULT_SEAL_BUDGET_S = 600.0
DEFAULT_SEAL_POLL_INTERVAL_S = 2.0
DEFAULT_SEAL_STABLE_POLLS = 3
"""(3 - 1) x 2.0 = 4 s of verified silence: enough for the few seconds a POD5 keeps
changing after it appears; the live watcher's longer window is not needed here."""

_SUMMARY_GLOB = "sequencing_summary*.txt"
_POD5_COLUMN = "filename_pod5"
#: MinKNOW's verdict on each read: TRUE for a read it passed (``bam_pass/``), FALSE for
#: one it failed on its qscore.
_PASSES_COLUMN = "passes_filtering"


# ---------------------------------------------------------------------------
# Coverage — what the instrument says it produced
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunCoverage:
    """Read counts per POD5 from ``sequencing_summary*.txt``.

    MinKNOW's own record of the run, and the only one not produced by this pipeline, so
    coverage can be checked against it per POD5.
    """

    reads_per_pod5: dict[str, int]
    source: Path

    @property
    def expected_pod5s(self) -> set[str]:
        return set(self.reads_per_pod5)

    @property
    def total_reads(self) -> int:
        return sum(self.reads_per_pod5.values())

    def fraction_covered(self, ingested: Iterable[str]) -> Optional[float]:
        """Read-weighted share of the instrument's reads in the ``ingested`` POD5s.

        None when the summary lists no reads. An ingested POD5 counts with all its
        reads; :func:`check_read_coverage` checks that per POD5.
        """
        if self.total_reads == 0:
            return None
        seen = sum(self.reads_per_pod5.get(name, 0) for name in ingested)
        return seen / self.total_reads


def read_sequencing_summary(run_dir: Path | str,
                            column: str = _POD5_COLUMN,
                            passes_filtering: Optional[bool] = None
                            ) -> Optional[RunCoverage]:
    """Count rows per input file in the run's ``sequencing_summary*.txt``, by line.

    ``column`` names the file each read went into: ``filename_pod5`` for a run followed
    by its POD5 files, ``filename_bam`` for one followed by MinKNOW's BAM files. A read
    MinKNOW wrote to no such file has ``-`` there (seen on a real run for the reads it
    failed, with no ``bam_fail/`` written) and is not counted.

    ``passes_filtering`` True or False counts only the reads MinKNOW passed or failed
    (its ``passes_filtering`` column): a run that follows ``bam_pass/`` or ``bam_fail/``
    alone is compared with that half of the run, not with the whole. On one real run every
    TRUE row named a ``bam_pass/`` file (2,397,909 of them).

    Uses the first match in sorted order. Returns None (coverage not measured) when
    there is none, it cannot be read, or it lacks a column it needs.
    """
    run_dir = Path(run_dir)
    candidates = sorted(run_dir.glob(_SUMMARY_GLOB))
    if not candidates:
        logger.warning("No %s in %s — run coverage cannot be measured",
                       _SUMMARY_GLOB, run_dir)
        return None
    path = candidates[0]

    counts: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            if column not in header:
                logger.warning(
                    "%s has no %r column (columns: %s); run coverage not measured",
                    path.name, column, ", ".join(header[:6]))
                return None
            idx = header.index(column)
            keep = None
            if passes_filtering is not None:
                if _PASSES_COLUMN not in header:
                    logger.warning(
                        "%s has no %r column, so the %s reads cannot be told apart; run "
                        "coverage not measured", path.name, _PASSES_COLUMN,
                        "passed" if passes_filtering else "failed")
                    return None
                keep = (header.index(_PASSES_COLUMN),
                        "TRUE" if passes_filtering else "FALSE")
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) <= idx:
                    continue  # ragged line (e.g. a truncated last one): skipped
                if keep is not None and (len(parts) <= keep[0]
                                         or parts[keep[0]].strip().upper() != keep[1]):
                    continue
                name = Path(parts[idx]).name
                if name and name != "-":
                    counts[name] = counts.get(name, 0) + 1
    except OSError as exc:
        logger.warning("Could not read %s (%s); run coverage not measured", path, exc)
        return None

    logger.info("Run coverage source %s: %d POD5(s), %d read(s)",
                path.name, len(counts), sum(counts.values()))
    return RunCoverage(reads_per_pod5=counts, source=path)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

@dataclass
class SweepReport:
    """Outcome of :meth:`RunSealer.sweep`."""

    handed_over: list[Path] = field(default_factory=list)
    unstable_at_seal: list[str] = field(default_factory=list)
    budget_exceeded: bool = False
    appeared_after_marker: list[str] = field(default_factory=list)
    """POD5s that appeared only after sealing began. Expected to be empty; if not, the
    closed-input-set assumption fails for that deployment, and it is logged."""
    aborted: list[str] = field(default_factory=list)
    """Outstanding when the operator aborted, so never attempted. NOT ``unstable_at_seal``:
    that name claims the file was still being written, which is a statement about the
    INSTRUMENT. These were stable or unexamined, and we stopped — a statement about us."""
    elapsed_s: float = 0.0


class RunSealer:
    """Bounded sweep of the POD5 directory after the run-end marker."""

    def __init__(
        self,
        pod5_dir: Path | str,
        *,
        poll_interval_s: float = DEFAULT_SEAL_POLL_INTERVAL_S,
        stable_polls: int = DEFAULT_SEAL_STABLE_POLLS,
        budget_s: float = DEFAULT_SEAL_BUDGET_S,
        pattern: str = DEFAULT_POD5_PATTERN,
        exclude_suffixes: tuple[str, ...] = DEFAULT_EXCLUDE_SUFFIXES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        is_complete: Optional[Callable[[Path], bool]] = None,
    ) -> None:
        if stable_polls < 2:
            raise ValueError("stable_polls must be >= 2 (see DirectoryWatcher)")
        # As the watcher's: asked of a file whose size is stable, before it is handed over.
        self.is_complete = is_complete
        self.pod5_dir = Path(pod5_dir)
        self.poll_interval_s = float(poll_interval_s)
        self.stable_polls = int(stable_polls)
        self.budget_s = float(budget_s)
        self.pattern = pattern
        self.exclude_suffixes = tuple(exclude_suffixes)
        self._clock = clock
        self._sleep = sleep

    @property
    def verified_silence_s(self) -> float:
        return (self.stable_polls - 1) * self.poll_interval_s

    def _scan(self) -> dict[str, Path]:
        try:
            found = sorted(self.pod5_dir.glob(self.pattern))
        except OSError as exc:
            logger.warning("Seal scan of %s failed (%s)", self.pod5_dir, exc)
            return {}
        return {p.name: p for p in found if not p.name.endswith(self.exclude_suffixes)}

    def sweep(
        self,
        *,
        already_handled: set[str],
        on_ready: Callable[[Path], None],
        stop: Optional[threading.Event] = None,
    ) -> SweepReport:
        """Hand each unhandled POD5 to ``on_ready`` once it is stable and non-empty.

        Sweeps the directory, not the live queue, so a POD5 the watcher had not yet
        released is still found. Ends when nothing is outstanding, when ``budget_s``
        runs out (the rest become ``unstable_at_seal``), or when ``stop`` is set (the
        rest become ``aborted``).

        Args:
            already_handled: Names to skip (already ingested, failed, or sampled out).
            on_ready: Called per stable, previously unhandled POD5.
            stop: Optional abort signal, checked before each poll and each file.
        """
        report = SweepReport()
        t0 = self._clock()
        # One scan for both: scanning twice would let a POD5 landing in between look as
        # if it had appeared after the marker.
        first_scan = self._scan()
        baseline = set(first_scan)
        pending = {n: p for n, p in first_scan.items() if n not in already_handled}
        if pending:
            logger.info("Sealing: %d POD5(s) not yet handled: %s",
                        len(pending), ", ".join(sorted(pending)[:8]))
        else:
            logger.info("Sealing: nothing outstanding at marker time")

        sizes: dict[str, tuple[int, int]] = {}
        done: set[str] = set()

        stopped = False

        def _abort_requested() -> bool:
            return stop is not None and stop.is_set()

        while True:
            if _abort_requested():
                stopped = True
                break
            elapsed = self._clock() - t0
            if elapsed > self.budget_s:
                report.budget_exceeded = True
                logger.error(
                    "Seal budget of %.0fs exhausted with %d file(s) still unstable. They "
                    "are recorded as unstable_at_seal, NOT silently dropped.",
                    self.budget_s, len(pending) - len(done))
                break

            current = self._scan()
            # The set should not grow after the marker; if it does, log it and ingest.
            grew = set(current) - baseline
            if grew:
                for name in sorted(grew):
                    if name not in report.appeared_after_marker:
                        report.appeared_after_marker.append(name)
                        logger.error(
                            "POD5 %s appeared AFTER the run-end marker, so on this "
                            "setup the input set is not closed when the marker is "
                            "written. Ingesting it anyway.", name)
                baseline |= grew
                for name in grew:
                    if name not in already_handled:
                        pending[name] = current[name]

            outstanding = [n for n in pending if n not in done]
            if not outstanding:
                break

            for name in outstanding:
                # Checked per file too: when QC starts after the run ended, one pass can
                # hand over the whole backlog, each on_ready call a full basecall.
                if _abort_requested():
                    stopped = True
                    break
                path = current.get(name)
                if path is None:
                    # Gone from the directory: recorded as unstable_at_seal.
                    logger.warning("POD5 %s disappeared during sealing", name)
                    done.add(name)
                    report.unstable_at_seal.append(name)
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                last, count = sizes.get(name, (None, 0))
                count = count + 1 if size == last else 1
                sizes[name] = (size, count)
                if count >= self.stable_polls and size > 0 and (
                        self.is_complete is None or self.is_complete(path)):
                    done.add(name)
                    report.handed_over.append(path)
                    logger.info("Sealing: %s ready (%d bytes)", name, size)
                    on_ready(path)

            if stopped:
                break
            if len([n for n in pending if n not in done]) == 0:
                break
            self._sleep(self.poll_interval_s)

        for name in pending:
            if name not in done and name not in report.unstable_at_seal:
                # Aborted if the operator stopped us, unstable if the budget ran out.
                (report.aborted if stopped else report.unstable_at_seal).append(name)
        report.elapsed_s = self._clock() - t0
        if stopped:
            logger.warning(
                "Sealing ABORTED by the operator after %.1fs: %d handed over, %d never "
                "attempted (recorded as aborted, not as unstable).",
                report.elapsed_s, len(report.handed_over), len(report.aborted))
        else:
            logger.info("Sealing finished in %.1fs: %d handed over, %d unstable",
                        report.elapsed_s, len(report.handed_over),
                        len(report.unstable_at_seal))
        return report


# ---------------------------------------------------------------------------
# Read-level coverage
# ---------------------------------------------------------------------------

DEFAULT_READ_COVERAGE_MIN_RATIO = 0.95
"""Per-POD5 yield below this fraction of the instrument's own row count is a shortfall.

Not 1.0: a read the per-read guard rejected, or one dorado could not basecall, is a real
and reportable loss but not necessarily a bug.
"""


def check_read_coverage(
    coverage: Optional[RunCoverage],
    primary_by_pod5: Mapping[str, int],
    *,
    min_ratio: float = DEFAULT_READ_COVERAGE_MIN_RATIO,
) -> dict:
    """Compare primary reads per POD5 with the instrument's row count for that POD5.

    :meth:`RunCoverage.fraction_covered` credits an ingested POD5 with all its reads, so
    it cannot see a POD5 that lost reads in basecalling or alignment; this check can.
    ``sequencing_summary`` is the reference because it does not come from this pipeline.

    A POD5 below ``min_ratio`` is reported as a shortfall, not raised; only an invalid
    ``min_ratio`` raises ``ValueError``.

    For a run followed by MinKNOW's BAM files the inputs are BAM files, compared with
    the summary's ``filename_bam``. The keys keep their names (``per_pod5``,
    ``n_shortfall_pod5s``, a shortfall's ``pod5``): they are what readers of
    ``qc_result.json`` look up, and each holds an input file's name of either kind.

    When there are inputs and the summary names none of them, nothing was compared and
    the status is ``not_measured`` with the reason. It read ``measured`` with an expected
    total of 0 (seen on BAMs basecalled outside MinKNOW and put in a run folder, whose
    summary has ``-`` for every read's BAM).
    """
    if coverage is None:
        return {"status": "not_measured",
                "reason": "no sequencing_summary.txt — instrument read counts unavailable"}
    if not 0 < min_ratio <= 1:
        raise ValueError(f"min_ratio must be in (0, 1], got {min_ratio}")

    per_pod5, shortfalls = {}, []
    expected_total = ours_total = 0
    for name, ours in sorted(primary_by_pod5.items()):
        expected = coverage.reads_per_pod5.get(name)
        if expected is None:
            # Not in the summary: listed, but left out of the totals.
            per_pod5[name] = {"ours": ours, "instrument": None, "ratio": None,
                              "note": "not in sequencing_summary"}
            continue
        ratio = (ours / expected) if expected else None
        per_pod5[name] = {"ours": ours, "instrument": expected,
                          "ratio": None if ratio is None else round(ratio, 6)}
        expected_total += expected
        ours_total += ours
        if ratio is not None and ratio < min_ratio:
            shortfalls.append({"pod5": name, "ours": ours, "instrument": expected,
                               "ratio": round(ratio, 6)})

    overall = (ours_total / expected_total) if expected_total else None
    compared = sum(1 for entry in per_pod5.values() if entry["instrument"] is not None)
    if per_pod5 and not compared:
        return {
            "status": "not_measured",
            "reason": (f"none of this run's {len(per_pod5)} input file(s) is named in "
                       f"{coverage.source.name}, so there is no instrument count to "
                       f"compare with"),
            # No totals and no ratio, as with no summary at all: there is none to give.
            "per_pod5": per_pod5,
        }
    if shortfalls:
        logger.error(
            "READ-LEVEL SHORTFALL on %d input(s): fewer primary reads reached QC than the "
            "instrument recorded (worst %.1f%%). The file-level coverage figure cannot see "
            "this. Check the basecall/align path before trusting any rate in this report: %s",
            len(shortfalls), 100 * min(s["ratio"] for s in shortfalls),
            ", ".join(f"{s['pod5']} {s['ours']}/{s['instrument']}" for s in shortfalls[:5]))
    return {
        "status": "measured",
        "min_ratio": min_ratio,
        "overall_ratio": None if overall is None else round(overall, 6),
        "primary_reads_ingested": ours_total,
        "instrument_reads_expected": expected_total,
        "n_shortfall_pod5s": len(shortfalls),
        "shortfalls": shortfalls,
        "per_pod5": per_pod5,
    }


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------

_BUCKETS = ("ingested", "failed_basecall", "unstable_at_seal", "deferred_backpressure",
            "sampled_out", "lost_before_processing", "seal_budget_exceeded")


def build_run_accounting(
    *,
    observed: Iterable[str],
    ingested: Iterable[str],
    failed: Iterable[str],
    unstable: Iterable[str],
    deferred: Iterable[str] = (),
    aborted: Iterable[str] = (),
    sampling: bool = False,
    seal_budget_exceeded: bool = False,
    coverage: Optional[RunCoverage] = None,
    primary_by_pod5: Optional[Mapping[str, int]] = None,
) -> dict:
    """Put every observed POD5 into exactly one disposition bucket.

    POD5s in the instrument's summary that were never observed or ingested are listed as
    ``lost_before_processing``; ``read_coverage`` adds the per-POD5 read check.

    Args:
        sampling: Report ``deferred`` as ``sampled_out`` (deliberately not analysed)
            rather than ``deferred_backpressure`` (not caught up by the end of the
            seal). Both keys are always present; at most one is non-empty.

    Raises:
        RuntimeError: An observed POD5 is in no bucket (a bookkeeping bug in the
            caller).
    """
    observed = set(observed)
    ingested_s, failed_s = set(ingested), set(failed)
    unstable_s, deferred_s = set(unstable), set(deferred)
    # Queued but never processed because the operator aborted: not failures.
    aborted_s = set(aborted)

    # A POD5 in several sets keeps only the first of: ingested, failed, unstable,
    # deferred, aborted. Aborted is last: anything that reached a stage has a more
    # specific disposition.
    failed_s -= ingested_s
    unstable_s -= ingested_s | failed_s
    deferred_s -= ingested_s | failed_s | unstable_s
    aborted_s -= ingested_s | failed_s | unstable_s | deferred_s

    # In the instrument's summary but never observed or ingested.
    expected = coverage.expected_pod5s if coverage else set()
    lost = (expected - observed) - ingested_s

    accounted = ingested_s | failed_s | unstable_s | deferred_s | aborted_s
    unexplained = observed - accounted
    if unexplained:
        raise RuntimeError(
            f"run accounting does not partition: {len(unexplained)} input(s) fell into no "
            f"bucket: {sorted(unexplained)[:8]}. A partial report is allowed; an "
            f"unexplained one is not.")

    fraction = coverage.fraction_covered(ingested_s) if coverage else None
    accounting = {
        "observed": len(observed),
        "ingested": sorted(ingested_s),
        "failed_basecall": sorted(failed_s),
        "unstable_at_seal": sorted(unstable_s),
        "deferred_backpressure": [] if sampling else sorted(deferred_s),
        "sampled_out": sorted(deferred_s) if sampling else [],
        "aborted_unprocessed": sorted(aborted_s),
        "lost_before_processing": sorted(lost),
        "seal_budget_exceeded": seal_budget_exceeded,
        "coverage": {
            # not_measured (no usable sequencing_summary) is not the same as 0.0.
            "status": "measured" if fraction is not None else "not_measured",
            "fraction_of_run_reads": None if fraction is None else round(fraction, 6),
            "instrument_reads": coverage.total_reads if coverage else None,
            "instrument_pod5s": len(expected) if coverage else None,
            "source": coverage.source.name if coverage else None,
        },
    }
    # Per-POD5 read counts: catches a POD5 that was ingested but lost reads.
    accounting["read_coverage"] = check_read_coverage(coverage, primary_by_pod5 or {})
    if fraction is not None and fraction < 1.0:
        logger.warning(
            "Report covers %.2f%% of the reads the instrument recorded (%d input "
            "file(s) in its summary were never seen here). See run_accounting.",
            fraction * 100, len(lost))
    return accounting
