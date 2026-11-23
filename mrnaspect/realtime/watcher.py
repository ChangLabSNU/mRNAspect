"""Polling directory watcher: detect completed POD5s and the run-end marker.

A POD5 is ready once its size has stayed the same over several polls. Polling needs no
OS event API and covers both ways MinKNOW has been seen to deliver a POD5: renamed into
the output directory at full size, or, when the output is on another filesystem, created
at 0 bytes and then filled.

MinKNOW writes POD5s to ``<run_dir>/pod5/`` but ``sequencing_summary*.txt`` and the
run-end marker ``final_summary_*.txt`` to ``<run_dir>/``, so the watcher looks in both.

The marker does not stop the watcher. It lands seconds after the last POD5 appears, long
before that POD5 can show the required stability (30 s by default), so stopping at the
marker would drop the run's last batch. The marker only means the input set is closed;
the owner decides when to call :meth:`DirectoryWatcher.stop`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Temp-file suffixes that are never emitted, even when ``pattern`` matches them.
DEFAULT_EXCLUDE_SUFFIXES: tuple[str, ...] = (".tmp", ".ctmp", ".temp", ".partial")

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_STABLE_POLLS = 7
DEFAULT_POD5_PATTERN = "*.pod5"
DEFAULT_RUN_END_GLOB = "final_summary_*.txt"

# Unreadable scans of the POD5 directory in a row before it is logged as a warning.
_DIR_MISSING_WARN_AFTER = 3

#: The 28 bytes every finished BAM ends with: an empty BGZF block (the SAM
#: specification's end-of-file marker). The writer puts it there when it closes the file.
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")


def bam_is_complete(path: Path | str) -> bool:
    """Whether a BAM ends with the end-of-file block, so its writer has closed it.

    A size that has stopped changing is not enough for a BAM: a writer that pauses
    between batches of reads leaves a file that is quiet and half written, and such a
    file opens and reads as a shorter BAM. An unreadable file is not complete.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            if fh.tell() < len(BGZF_EOF):
                return False
            fh.seek(-len(BGZF_EOF), 2)
            return fh.read() == BGZF_EOF
    except OSError:
        return False


@dataclass(frozen=True)
class FileReadyEvent:
    """A file observed complete: same size across ``stable_polls`` consecutive scans."""

    path: Path
    size: int
    detected_at: float
    """Unix epoch seconds. Epoch rather than monotonic so watcher logs correlate with
    MinKNOW's own timestamps; durations are computed by the caller."""


class DirectoryWatcher:
    """Poll a MinKNOW output folder; call back when a POD5 completes or the run ends.

    ``run()`` blocks and starts no thread; :meth:`scan_once` steps the state machine
    directly, which is how tests drive it.

    Args:
        run_dir: MinKNOW run folder; the run-end marker is looked for here.
        pod5_subdir: Subdirectory holding the POD5s; ``""`` for a flat layout.
        on_ready: Called with a :class:`FileReadyEvent` per completed POD5. Not
            wrapped: an exception propagates out of :meth:`scan_once`, and :meth:`run`
            logs it and keeps polling.
        on_run_end: Called once with the marker path; the watcher keeps running.
        poll_interval_s: Seconds between scans.
        stable_polls: Consecutive equal sizes needed to emit a file. The first sighting
            is only a baseline, so the verified silence is ``(stable_polls - 1) *
            poll_interval_s``: 30 s at the defaults (7, 5.0), and a file is emitted at
            most ``stable_polls * poll_interval_s`` (35 s) after it is complete.

            Watch the final output directory only. Where files grow in bursts tens of
            seconds apart, such as MinKNOW's ``reads/tmp/.../pod5/`` staging tree, a
            pause between bursts looks like a finished file.
        emit_preexisting: Emit files already present at the first scan (QC started
            mid-run, or a backlog on disk). ``False`` marks them seen without emitting.
        is_complete: Asked of a file once its size is stable; a file it rejects is not
            emitted and is asked again at every scan. For formats whose writer can go
            quiet mid-file (:func:`bam_is_complete`). None asks nothing more than size.
    """

    def __init__(
        self,
        run_dir: Path | str,
        *,
        pod5_subdir: str = "pod5",
        on_ready: Callable[[FileReadyEvent], None],
        on_run_end: Optional[Callable[[Path], None]] = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        stable_polls: int = DEFAULT_STABLE_POLLS,
        pattern: str = DEFAULT_POD5_PATTERN,
        run_end_glob: str = DEFAULT_RUN_END_GLOB,
        exclude_suffixes: tuple[str, ...] = DEFAULT_EXCLUDE_SUFFIXES,
        emit_preexisting: bool = True,
        is_complete: Optional[Callable[[Path], bool]] = None,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError(f"poll_interval_s must be > 0, got {poll_interval_s}")
        if stable_polls < 2:
            # 1 would emit a file on first sighting, e.g. one just created at 0 bytes.
            raise ValueError(
                f"stable_polls must be >= 2 (a first sighting cannot be 'stable'), "
                f"got {stable_polls}")

        self.run_dir = Path(run_dir)
        self.pod5_dir = self.run_dir / pod5_subdir if pod5_subdir else self.run_dir
        self.on_ready = on_ready
        self.on_run_end = on_run_end
        self.poll_interval_s = float(poll_interval_s)
        self.stable_polls = int(stable_polls)
        self.pattern = pattern
        self.run_end_glob = run_end_glob
        self.exclude_suffixes = tuple(exclude_suffixes)
        self.emit_preexisting = emit_preexisting
        self.is_complete = is_complete
        self._incomplete_warned: set[Path] = set()

        # path -> (last_observed_size, consecutive_matching_observations)
        self._sizes: dict[Path, tuple[int, int]] = {}
        self._emitted: set[Path] = set()
        self._first_scan = True
        self._perm_warned: set[Path] = set()
        self._run_end_sent = False
        self._dir_missing_streak = 0
        self._stop = threading.Event()

    # -- properties -------------------------------------------------------

    @property
    def verified_silence_s(self) -> float:
        """Seconds of unchanging size a file must show before it is emitted."""
        return (self.stable_polls - 1) * self.poll_interval_s

    @property
    def run_end_seen(self) -> bool:
        return self._run_end_sent

    @property
    def emitted_names(self) -> set[str]:
        """Names of the files emitted so far."""
        return {p.name for p in self._emitted}

    # -- lifecycle --------------------------------------------------------

    def stop(self) -> None:
        """Ask :meth:`run` to return after the current scan. Thread-safe."""
        self._stop.set()

    def run(self) -> None:
        """Blocking poll loop until :meth:`stop`; the run-end marker does not end it."""
        logger.info(
            "Watcher: %s (marker in %s), poll %.1fs x %d = %.0fs verified silence",
            self.pod5_dir, self.run_dir, self.poll_interval_s, self.stable_polls,
            self.verified_silence_s)
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                # One unexpected error must not end the watcher for the rest of the run.
                logger.exception("Watcher scan failed; continuing")
            self._stop.wait(self.poll_interval_s)
        logger.info("Watcher stopped (%d file(s) emitted)", len(self._emitted))

    # -- the scan ---------------------------------------------------------

    def scan_once(self) -> list[FileReadyEvent]:
        """One scan; returns the events emitted (also delivered to ``on_ready``)."""
        # Marker first, for prompt notice; the POD5 scan below runs either way.
        self._check_run_end()

        candidates = self._list_candidates()
        if candidates is None:  # directory unreadable this scan; retry next poll
            return []

        events: list[FileReadyEvent] = []
        for path in candidates:
            event = self._observe(path)
            if event is not None:
                events.append(event)

        self._prune_vanished(candidates)

        if self._first_scan:
            if not self.emit_preexisting:
                # A first sighting is never stable, so nothing was emitted above;
                # marking the files seen here suppresses them.
                for path in candidates:
                    self._emitted.add(path)
                self._sizes.clear()
                logger.info("Watcher: %d pre-existing file(s) marked seen (emit_preexisting=False)",
                            len(candidates))
            self._first_scan = False

        for event in events:
            self.on_ready(event)  # not wrapped: callback errors propagate
        return events

    def _check_run_end(self) -> None:
        if self._run_end_sent:
            return
        try:
            markers = sorted(self.run_dir.glob(self.run_end_glob))
        except (FileNotFoundError, PermissionError, OSError):
            return
        if not markers:
            return
        self._run_end_sent = True
        logger.info(
            "Run-end marker: %s. The input set is now CLOSED — but the last POD5 needs "
            "%.0fs of silence to be declared ready and the marker lands ~1-5s after it, "
            "so scanning CONTINUES until the owner stops this watcher.",
            markers[0].name, self.verified_silence_s)
        if self.on_run_end is not None:
            self.on_run_end(markers[0])

    def _list_candidates(self) -> Optional[list[Path]]:
        """Non-excluded files matching ``pattern``, or None if the dir is unreadable."""
        try:
            found = sorted(self.pod5_dir.glob(self.pattern))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            # Normal at run start: pod5/ does not exist until MinKNOW's first batch.
            self._dir_missing_streak += 1
            if self._dir_missing_streak == _DIR_MISSING_WARN_AFTER:
                logger.warning("Watch dir %s still unreadable after %d scans (%s); "
                               "retrying every poll", self.pod5_dir,
                               self._dir_missing_streak, exc)
            else:
                logger.debug("Watch dir %s unreadable (%s)", self.pod5_dir, exc)
            return None
        if self._dir_missing_streak >= _DIR_MISSING_WARN_AFTER:
            logger.info("Watch dir %s is readable again", self.pod5_dir)
        self._dir_missing_streak = 0
        return [p for p in found if not p.name.endswith(self.exclude_suffixes)]

    def _observe(self, path: Path) -> Optional[FileReadyEvent]:
        """Advance one file's stability count; return an event when it becomes ready."""
        if path in self._emitted:
            return None
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            # Renamed or deleted since the glob: an ordinary race, not logged.
            return None
        except PermissionError:
            if path not in self._perm_warned:
                self._perm_warned.add(path)
                logger.warning("Cannot stat %s (permission denied); skipping", path.name)
            return None
        except OSError as exc:
            logger.warning("Cannot stat %s (%s); skipping this scan", path.name, exc)
            return None

        last_size, count = self._sizes.get(path, (None, 0))
        count = count + 1 if size == last_size else 1
        self._sizes[path] = (size, count)

        if count < self.stable_polls:
            return None
        if size == 0:
            # Cross-filesystem delivery starts at 0 bytes, so a file stuck at 0 is a
            # failed write, not a finished one.
            logger.warning("%s has been 0 bytes for %.0fs — not emitting an empty POD5",
                           path.name, self.verified_silence_s)
            return None
        if self.is_complete is not None and not self.is_complete(path):
            # Quiet, and not finished: left for a later scan. Said once per file.
            if path not in self._incomplete_warned:
                self._incomplete_warned.add(path)
                logger.warning(
                    "%s has not changed for %.0fs but its writer has not closed it; "
                    "waiting for it to be finished", path.name, self.verified_silence_s)
            return None

        self._emitted.add(path)
        self._sizes.pop(path, None)  # no longer needed once emitted
        logger.info("Ready: %s (%d bytes, stable %.0fs)", path.name, size,
                    self.verified_silence_s)
        return FileReadyEvent(path=path, size=size, detected_at=time.time())

    def _prune_vanished(self, present: list[Path]) -> None:
        """Drop stability state for files no longer listed.

        Keeps ``_sizes`` bounded, and a name that reappears starts a fresh count.
        """
        alive = set(present)
        for gone in [p for p in self._sizes if p not in alive]:
            del self._sizes[gone]
