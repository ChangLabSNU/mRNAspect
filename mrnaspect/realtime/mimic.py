"""MinKNOW output mimic: replays a sequencing run's file-writing behavior.

A test harness: it lets the directory watcher and the real-time driver be developed and
fault-tested without a sequencer, and nothing it writes is real data. Its timings and
event shapes (``PROFILES``) are taken from recorded MinKNOW runs.

``final_summary_*.txt``, the run-end marker, is written seconds after the last POD5,
before the watcher's 30 s stability window can declare that POD5 ready, so a watcher
that stops at the marker loses the last batch. ``marker_gap_s`` reproduces this; do not
lengthen it to make a test pass.

As in MinKNOW, POD5s go to ``<run_dir>/pod5/`` and the summary and marker to
``<run_dir>/``.

Usage::

    python -m mrnaspect.realtime.mimic --run-dir /tmp/run --profile win_same_disk \\
        --source-dir /path/to/recorded/pod5 --speed 60
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence

from ..runtime.ordering import natural_sorted

logger = logging.getLogger(__name__)

# Trees the mimic must never write into, even with allow_dirty: a target that is, or is
# inside, one of them is refused (both paths resolved, so `..` cannot get around it).
# Empty unless set, os.pathsep-separated, in the environment at import time:
#
#     export QCRNA_PROTECTED_ROOTS=/data/recorded_runs:/data/released
PROTECTED_ROOTS: tuple[str, ...] = tuple(
    x for x in os.environ.get("QCRNA_PROTECTED_ROOTS", "").split(os.pathsep) if x)

_POD5_INDEX_RE = re.compile(r"_(\d+)\.pod5$")


# ---------------------------------------------------------------------------
# Profiles: recorded MinKNOW write patterns
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MimicProfile:
    """One MinKNOW write pattern.

    Times are in seconds of simulated run time. ``MinKNOWMimic.speed`` divides them all,
    so a long run replays in minutes with event order and relative gaps unchanged.
    """

    name: str
    delivery: str
    """``atomic`` — publish by ``rename(2)`` from a staging dir, so the file is only ever
    visible complete (Linux Mk1D and Windows same-disk: one event, full size).
    ``grow`` — create at 0 bytes in place and fill (Windows cross-disk: the destination
    is on another filesystem so the kernel cannot rename, and the watcher sees the file
    transit from incomplete to complete)."""

    batch_interval_s: float
    """Steady-state gap between consecutive POD5 arrivals."""

    last_batch_interval_fraction: float
    """The last gap as a fraction of ``batch_interval_s``: MinKNOW's batch clock runs on
    a fixed period, so stopping the run truncates the last window. The first batch is
    partial too, but that shows as a small file, which comes from the source data."""

    summary_gap_s: float
    """Last POD5 -> ``sequencing_summary_*.txt``."""

    marker_gap_s: float
    """Last POD5 -> ``final_summary_*.txt``. See the module docstring."""

    grow_steps: int = 1
    """``grow`` only: how many appends the file arrives in."""

    grow_step_s: float = 0.0
    """``grow`` only: gap between appends."""

    grow_tail_s: float = 0.0
    """``grow`` only: a trailing no-op write after the file is already full size. Real
    MinKNOW emits a second MODIFIED ~2.3 s after the content is complete; a watcher that
    requires N *identical* consecutive sizes is unaffected, but one that resets on any
    mtime change would hang. Reproduced so that distinction is testable."""


PROFILES: Mapping[str, MimicProfile] = {
    # Mk1D on Linux: written as reads/tmp/.../pod5/*.pod5.tmp, then renamed into
    # <run>/pod5/, so it appears once, at full size.
    "linux_mk1d": MimicProfile(
        name="linux_mk1d",
        delivery="atomic",
        batch_interval_s=3600.0,
        last_batch_interval_fraction=0.124,
        summary_gap_s=1.095,
        marker_gap_s=1.095,
    ),
    # Windows, staging and output on one disk: also a rename at full size (reported as
    # CREATED rather than MOVED). Its short batches make it the default for tests.
    "win_same_disk": MimicProfile(
        name="win_same_disk",
        delivery="atomic",
        batch_interval_s=180.6,
        last_batch_interval_fraction=0.639,
        summary_gap_s=1.059,
        marker_gap_s=1.073,
    ),
    # Windows, output on another volume: created at 0 bytes, then filled. The only
    # pattern in which a file is visibly incomplete in the watched directory.
    "win_cross_disk": MimicProfile(
        name="win_cross_disk",
        delivery="grow",
        batch_interval_s=364.0,
        last_batch_interval_fraction=0.603,
        summary_gap_s=5.273,
        marker_gap_s=5.381,
        grow_steps=1,                         # 0 bytes -> full in one write
        grow_step_s=0.0,
        grow_tail_s=2.26,
    ),
}

DEFAULT_PROFILE = "win_same_disk"


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

class Fault:
    """Fault names for ``MinKNOWMimic(faults=...)``: each reproduces one way a run's
    output can go wrong."""

    STALLED_TMP = "stalled_tmp"
    """A ``.pod5.tmp`` that never gets renamed — MinKNOW killed mid-write. The watcher
    must ignore it forever without treating the run as blocked."""

    ZERO_BYTE = "zero_byte"
    """A correctly-named POD5 that stays 0 bytes. Size-stabilizes immediately, so a
    naive watcher hands an empty file to the basecaller."""

    TRUNCATED = "truncated"
    """Half a real POD5 under the final name. Stabilizes, looks legitimate, and only
    fails once dorado opens it — so it tests the *stage-failure* path, not the watcher."""

    MID_COPY_STALL = "mid_copy_stall"
    """``grow`` delivery that pauses longer than the stability window mid-file. The file
    looks finished, then keeps growing. The one case where a size-stability watcher can
    be wrong, so the loop must tolerate a late-growing file rather than assume it cannot
    happen."""

    DUPLICATE_NAME = "duplicate_name"
    """An already-delivered filename republished with different bytes. Exactly-once
    accounting must not double-count it, and must notice."""

    POST_MARKER_STRAGGLER = "post_marker_straggler"
    """A POD5 arriving after ``final_summary``, against the observed ordering. The
    run-end sweep must detect and log it, not ignore it."""

    ALL = (
        STALLED_TMP, ZERO_BYTE, TRUNCATED, MID_COPY_STALL,
        DUPLICATE_NAME, POST_MARKER_STRAGGLER,
    )


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Batch:
    """One POD5 to publish: ``source`` replayed byte for byte, or, when ``source`` is
    None, ``size_bytes`` of filler."""

    source: Optional[Path] = None
    size_bytes: Optional[int] = None
    n_reads: int = 0
    """Rows this batch contributes to ``sequencing_summary.txt``, supplied by the caller
    (this module is stdlib-only and cannot open a POD5). 0 means unknown: the batch is
    then left out of the summary rather than given an invented count."""

    def __post_init__(self) -> None:
        if self.source is None and self.size_bytes is None:
            raise ValueError("Batch needs either a source path or an explicit size_bytes")
        if self.source is not None and not Path(self.source).is_file():
            raise FileNotFoundError(f"Batch source POD5 does not exist: {self.source}")
        if self.n_reads < 0:
            raise ValueError(f"n_reads must be >= 0, got {self.n_reads}")

    @property
    def nbytes(self) -> int:
        if self.size_bytes is not None:
            return self.size_bytes
        assert self.source is not None
        return Path(self.source).stat().st_size

    @classmethod
    def from_dir(
        cls,
        source_dir: Path | str,
        *,
        read_counts: Optional[Mapping[str, int]] = None,
        limit: Optional[int] = None,
    ) -> list["Batch"]:
        """Batches from a directory of MinKNOW-named POD5s, in natural order.

        Natural order puts ``_2.pod5`` before ``_10.pod5``. ``read_counts`` maps a file
        name to its read count; a name it lacks gets 0 and no summary rows.
        """
        source_dir = Path(source_dir)
        if not source_dir.is_dir():
            raise NotADirectoryError(f"source dir not found: {source_dir}")
        found = natural_sorted(source_dir.glob("*.pod5"))
        if not found:
            raise FileNotFoundError(f"no *.pod5 under {source_dir}")
        if limit is not None:
            found = found[:limit]
        counts = read_counts or {}
        return [cls(source=p, n_reads=int(counts.get(p.name, 0))) for p in found]


@dataclass
class MimicResult:
    """What the mimic did, for a test to assert against."""

    run_dir: Path
    pod5_dir: Path
    published: list[str] = field(default_factory=list)
    """Final POD5 filenames published to ``pod5/``, in publication order."""
    marker_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    total_reads: int = 0
    faults_applied: dict[int, str] = field(default_factory=dict)
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# The mimic
# ---------------------------------------------------------------------------

class MinKNOWMimic:
    """Replays MinKNOW's output-directory behavior into ``run_dir``.

    ``faults`` maps a batch index to a :class:`Fault` name; a post-marker straggler is
    keyed one past the last batch.
    """

    def __init__(
        self,
        run_dir: Path | str,
        batches: Sequence[Batch],
        profile: MimicProfile | str = DEFAULT_PROFILE,
        *,
        speed: float = 1.0,
        flowcell: str = "SIM41175",
        run_id: str = "7c428dc3",
        acq_id: str = "9cffaacf",
        faults: Optional[Mapping[int, str]] = None,
        startup_delay_s: float = 0.0,
        write_summaries: bool = True,
        allow_dirty: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(profile, str):
            if profile not in PROFILES:
                raise KeyError(
                    f"unknown profile {profile!r}; have {sorted(PROFILES)}")
            profile = PROFILES[profile]
        if speed <= 0:
            raise ValueError(f"speed must be > 0, got {speed}")
        if not batches:
            raise ValueError("no batches to publish")

        self.run_dir = _check_writable(Path(run_dir).resolve(), allow_dirty=allow_dirty)
        self.pod5_dir = self.run_dir / "pod5"
        # Inside run_dir, on the same filesystem as pod5/, so publishing is a rename.
        self.staging_dir = self.run_dir / ".mimic_staging"
        self.batches = list(batches)
        self.profile = profile
        self.speed = float(speed)
        self.flowcell = flowcell
        self.run_id = run_id
        self.acq_id = acq_id
        self.startup_delay_s = float(startup_delay_s)
        self.write_summaries = write_summaries
        self._sleep = sleep
        self._clock = clock

        self.faults: dict[int, str] = dict(faults or {})
        unknown = set(self.faults.values()) - set(Fault.ALL)
        if unknown:
            raise ValueError(f"unknown fault(s) {sorted(unknown)}; have {list(Fault.ALL)}")
        for i, f in self.faults.items():
            if f == Fault.POST_MARKER_STRAGGLER:
                if i != len(self.batches):
                    raise ValueError(
                        f"{Fault.POST_MARKER_STRAGGLER} must be keyed at index "
                        f"{len(self.batches)} (one past the last batch), got {i}")
            elif not 0 <= i < len(self.batches):
                raise ValueError(
                    f"fault index {i} out of range for {len(self.batches)} batches")

    # -- naming -----------------------------------------------------------

    def pod5_name(self, index: int) -> str:
        return f"{self.flowcell}_{self.run_id}_{self.acq_id}_{index}.pod5"

    @property
    def summary_name(self) -> str:
        return f"sequencing_summary_{self.flowcell}_{self.run_id}_{self.acq_id}.txt"

    @property
    def marker_name(self) -> str:
        return f"final_summary_{self.flowcell}_{self.run_id}_{self.acq_id}.txt"

    # -- schedule ---------------------------------------------------------

    def schedule(self) -> list[tuple[float, str, str]]:
        """The event timeline as ``(t_seconds, kind, name)``, computed without I/O.

        ``kind`` is ``pod5``, a fault name, ``summary`` or ``marker``. Times count from
        the start of :meth:`run`, already divided by ``speed``; :meth:`run` and
        ``--print-schedule`` both use this.
        """
        p = self.profile
        events: list[tuple[float, str, str]] = []
        t = self.startup_delay_s / self.speed
        last = len(self.batches) - 1
        for i in range(len(self.batches)):
            if i > 0:
                # The gap *preceding* batch i. The final gap is the truncated window.
                frac = p.last_batch_interval_fraction if i == last else 1.0
                t += (p.batch_interval_s * frac) / self.speed
            events.append((t, self.faults.get(i, "pod5"), self.pod5_name(i)))
        t_last = t
        if self.write_summaries:
            events.append((t_last + p.summary_gap_s / self.speed, "summary",
                           self.summary_name))
            events.append((t_last + p.marker_gap_s / self.speed, "marker",
                           self.marker_name))
        if Fault.POST_MARKER_STRAGGLER in self.faults.values():
            events.append((t_last + (p.marker_gap_s + p.batch_interval_s * 0.1) / self.speed,
                           Fault.POST_MARKER_STRAGGLER, self.pod5_name(len(self.batches))))
        events.sort(key=lambda e: e[0])
        return events

    # -- execution --------------------------------------------------------

    def run(self) -> MimicResult:
        """Publish the whole run. Blocking; intended to be driven from a thread."""
        self.pod5_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        result = MimicResult(run_dir=self.run_dir, pod5_dir=self.pod5_dir,
                             faults_applied=dict(self.faults))
        t0 = self._clock()
        prev_t = 0.0
        straggler: Optional[tuple[float, str]] = None

        for t, kind, name in self.schedule():
            self._sleep(max(0.0, t - prev_t))
            prev_t = t
            if kind == Fault.POST_MARKER_STRAGGLER:
                straggler = (t, name)
                continue
            if kind == "summary":
                result.summary_path = self._write_summary()
                continue
            if kind == "marker":
                result.marker_path = self._write_marker(result.published)
                continue
            index = int(_POD5_INDEX_RE.search(name).group(1))  # type: ignore[union-attr]
            self._publish(index, name, kind, result)

        if straggler is not None:
            _, name = straggler
            index = len(self.batches)
            # The last batch's bytes: for this fault only the timing matters.
            self._deliver(self.batches[-1], self.pod5_dir / name)
            result.published.append(name)
            logger.warning("mimic: published post-marker straggler %s", name)

        result.total_reads = sum(b.n_reads for b in self.batches)
        result.elapsed_s = self._clock() - t0
        logger.info("mimic: published %d POD5(s) in %.1fs (profile=%s speed=%gx)",
                    len(result.published), result.elapsed_s, self.profile.name, self.speed)
        return result

    def _publish(self, index: int, name: str, kind: str, result: MimicResult) -> None:
        batch = self.batches[index]
        dest = self.pod5_dir / name

        if kind == Fault.STALLED_TMP:
            # Never renamed. Half the bytes so it is also obviously incomplete.
            self._deliver(batch, self.pod5_dir / f"{name}.tmp", truncate_to=batch.nbytes // 2)
            logger.warning("mimic[%d]: stalled .tmp, never completes: %s.tmp", index, name)
            return
        if kind == Fault.ZERO_BYTE:
            dest.touch()
            logger.warning("mimic[%d]: zero-byte %s", index, name)
            result.published.append(name)
            return
        if kind == Fault.TRUNCATED:
            self._deliver(batch, dest, truncate_to=batch.nbytes // 2, force_copy=True)
            logger.warning("mimic[%d]: truncated %s", index, name)
            result.published.append(name)
            return
        if kind == Fault.DUPLICATE_NAME:
            # Republish an *earlier* name with this batch's bytes.
            if index == 0:
                raise ValueError("duplicate_name fault needs a preceding batch")
            dup = self.pod5_dir / self.pod5_name(index - 1)
            self._deliver(batch, dup, force_copy=True)
            logger.warning("mimic[%d]: republished %s with different bytes", index, dup.name)
            return
        if kind == Fault.MID_COPY_STALL:
            self._grow(batch, dest, steps=3,
                       step_s=(self.profile.batch_interval_s * 0.25) / self.speed)
            result.published.append(name)
            return

        self._deliver(batch, dest)
        result.published.append(name)

    # -- delivery mechanics -----------------------------------------------

    def _deliver(self, batch: Batch, dest: Path, *, truncate_to: Optional[int] = None,
                 force_copy: bool = False) -> None:
        if self.profile.delivery == "grow" and truncate_to is None and not force_copy:
            self._grow(batch, dest, steps=self.profile.grow_steps,
                       step_s=self.profile.grow_step_s / self.speed,
                       tail_s=self.profile.grow_tail_s / self.speed)
        else:
            self._atomic(batch, dest, truncate_to=truncate_to, force_copy=force_copy)

    def _atomic(self, batch: Batch, dest: Path, *, truncate_to: Optional[int] = None,
                force_copy: bool = False) -> None:
        """Stage then ``os.replace`` — the file is never visible partially written."""
        tmp = self.staging_dir / f"{dest.name}.staging"
        if batch.source is not None and truncate_to is None and not force_copy:
            # Hardlink where possible instead of copying; the rename stays atomic.
            try:
                if tmp.exists():
                    tmp.unlink()
                os.link(batch.source, tmp)
            except OSError:
                shutil.copyfile(batch.source, tmp)
        else:
            _materialize(batch, tmp, truncate_to=truncate_to)
        os.replace(tmp, dest)

    def _grow(self, batch: Batch, dest: Path, *, steps: int, step_s: float,
              tail_s: float = 0.0) -> None:
        """Create in place at 0 bytes and fill — the cross-filesystem case."""
        total = batch.nbytes
        if steps < 1:
            raise ValueError(f"grow steps must be >= 1, got {steps}")
        chunk = -(-total // steps)  # ceil, so `steps` writes always cover `total`
        src = open(batch.source, "rb") if batch.source is not None else None
        try:
            with open(dest, "wb") as out:
                out.flush()  # the 0-byte CREATED the watcher may observe
                written = 0
                for step in range(steps):
                    if step:
                        self._sleep(step_s)
                    want = min(chunk, total - written)
                    out.write(src.read(want) if src is not None else b"\0" * want)
                    out.flush()
                    written += want
        finally:
            if src is not None:
                src.close()
        if tail_s > 0:
            # Same size, new mtime: MinKNOW's trailing MODIFIED.
            self._sleep(tail_s)
            os.utime(dest, None)

    # -- run-end files ----------------------------------------------------

    def _write_summary(self) -> Path:
        """Write ``sequencing_summary_*.txt`` with a subset of the real columns.

        ``filename_pod5`` and a unique ``read_id`` per row are what matter; the other
        values are placeholders. Every batch's reads are listed, including batches a
        fault damaged or never published, since MinKNOW records what it acquired rather
        than what reached the output directory; that gap is what the coverage check
        measures. A batch with ``n_reads == 0`` adds no rows.
        """
        path = self.run_dir / self.summary_name
        header = ("filename_pod5\tread_id\trun_id\tchannel\tstart_time\t"
                  "sequence_length_template\tmean_qscore_template\tend_reason")
        lines = [header]
        for i, batch in enumerate(self.batches):
            name = self.pod5_name(i)
            for r in range(batch.n_reads):
                lines.append(
                    f"{name}\t{self.acq_id}-{i:04d}-{r:06d}\t{self.run_id}\t"
                    f"{(r % 512) + 1}\t{r}\t0\t0\tsignal_positive")
        _atomic_text(path, "\n".join(lines) + "\n")
        return path

    def _write_marker(self, published: Sequence[str]) -> Path:
        """Write ``final_summary_*.txt`` in one atomic step; the real file is small."""
        path = self.run_dir / self.marker_name
        body = "\n".join([
            "instrument=MIMIC",
            f"flow_cell_id={self.flowcell}",
            f"protocol_run_id={self.run_id}",
            f"acquisition_run_id={self.acq_id}",
            "basecalling_enabled=0",
            f"sequencing_summary_file={self.summary_name}",
            "fast5_files_in_final_dest=0",
            f"pod5_files_in_final_dest={len(published)}",
            "pod5_files_in_fallback=0",
        ]) + "\n"
        _atomic_text(path, body)
        return path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check_writable(run_dir: Path, *, allow_dirty: bool = False) -> Path:
    """Return ``run_dir`` if the mimic may write into it; raise ``ValueError`` if not.

    A target in ``PROTECTED_ROOTS`` is always refused. Otherwise it must be empty or
    not exist yet, unless ``allow_dirty`` is set (``--allow-dirty`` here,
    ``--mimic-allow-dirty`` in the real-time CLI) to re-feed a folder left over from
    one's own earlier drill.
    """
    for root in PROTECTED_ROOTS:
        r = Path(root).resolve()
        if run_dir == r or r in run_dir.parents:
            raise ValueError(
                f"refusing to run the mimic inside a protected tree: {run_dir} is under "
                f"{r} (QCRNA_PROTECTED_ROOTS). The mimic writes junk by design; point "
                f"--run-dir at a scratch dir. This tier cannot be overridden.")
    if allow_dirty or not run_dir.exists():
        return run_dir
    existing = sorted(p.name for p in run_dir.iterdir())
    if existing:
        raise ValueError(
            f"refusing to run the mimic into a non-empty directory: {run_dir} already "
            f"holds {len(existing)} entr{'y' if len(existing) == 1 else 'ies'} "
            f"({', '.join(existing[:3])}{'...' if len(existing) > 3 else ''}). The mimic "
            f"publishes junk files and a run-end marker into the directory it is given, "
            f"so anything already there is at risk and would also be swept up as part of "
            f"this run. Point --run-dir at a fresh path, or pass allow_dirty/"
            f"--mimic-allow-dirty if the leftovers are your own previous drill.")
    return run_dir


def _materialize(batch: Batch, dest: Path, *, truncate_to: Optional[int] = None) -> None:
    n = batch.nbytes if truncate_to is None else truncate_to
    if batch.source is not None:
        with open(batch.source, "rb") as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, length=1 << 20)
            if truncate_to is not None:
                out.truncate(n)
    else:
        with open(dest, "wb") as out:
            out.truncate(n)  # sparse: the watcher only ever reads st_size


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mrnaspect.realtime.mimic",
        description="Replay MinKNOW's output-directory behavior (test harness).")
    p.add_argument("--run-dir", required=True, type=Path,
                   help="MinKNOW run folder to create; POD5s land in <run-dir>/pod5/")
    p.add_argument("--profile", default=DEFAULT_PROFILE, choices=sorted(PROFILES))
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--source-dir", type=Path,
                     help="directory of MinKNOW-named *.pod5 to replay byte-for-byte")
    src.add_argument("--synthetic", type=int, metavar="N",
                     help="publish N synthetic filler files instead of real POD5s")
    p.add_argument("--synthetic-size", type=int, default=1 << 20,
                   help="bytes per synthetic file (default 1 MiB)")
    p.add_argument("--limit", type=int, help="use only the first N source POD5s")
    p.add_argument("--speed", type=float, default=1.0,
                   help="divide all intervals by this (60 = one minute per simulated hour)")
    p.add_argument("--startup-delay", type=float, default=0.0)
    p.add_argument("--fault", action="append", default=[], metavar="INDEX:NAME",
                   help=f"inject a fault; NAME in {list(Fault.ALL)}")
    p.add_argument("--no-summaries", action="store_true",
                   help="omit sequencing_summary/final_summary (simulates an aborted run)")
    p.add_argument("--allow-dirty", action="store_true",
                   help="write into a --run-dir that already has content in it. Mirrors "
                        "mrnaspect-realtime's --mimic-allow-dirty; without it this entry "
                        "point would have no escape from the non-empty check.")
    p.add_argument("--print-schedule", action="store_true",
                   help="print the event timeline and exit without writing anything")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _parse_faults(items: Iterable[str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"--fault must be INDEX:NAME, got {item!r}")
        idx, name = item.split(":", 1)
        out[int(idx)] = name
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    if args.source_dir is not None:
        batches = Batch.from_dir(args.source_dir, limit=args.limit)
    else:
        batches = [Batch(size_bytes=args.synthetic_size) for _ in range(args.synthetic)]

    mimic = MinKNOWMimic(
        args.run_dir, batches, args.profile,
        speed=args.speed,
        faults=_parse_faults(args.fault),
        startup_delay_s=args.startup_delay,
        write_summaries=not args.no_summaries,
        allow_dirty=args.allow_dirty,
    )
    if args.print_schedule:
        print(f"profile={mimic.profile.name} delivery={mimic.profile.delivery} "
              f"speed={mimic.speed}x  batches={len(batches)}")
        for t, kind, name in mimic.schedule():
            print(f"  t+{t:9.3f}s  {kind:<22s} {name}")
        return 0

    mimic.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
