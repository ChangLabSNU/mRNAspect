"""mRNAspect real-time driver: QC on a live MinKNOW run, as its POD5 files appear.

    BOOT -> REPLAY -> LIVE -> SEALING -> FINALIZE

Each input goes through ``OfflinePipeline._process_one_input``, the same step the
offline driver runs; only the feed differs (a watcher queue instead of a sorted glob).

Threading: a watcher thread fills a queue and the main thread consumes it, so only the
main thread touches the engine, and signal handling (main thread only) keeps working.
A heartbeat thread only reads counters. Progress is read from the atomically replaced
snapshot file, never from memory.

The driver never finalizes because the run went quiet (MinKNOW pauses legitimately; it
only warns). The run-end marker means the input set is closed, not "stop" (see
``sealing``). The manifest is never used to skip inputs (see ``manifest``).

Where to point it, and how a run ends, stops and continues: README, "Live runs".
"""

from __future__ import annotations

import argparse
import enum
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from .. import PRODUCT_NAME
from ..main import (
    _LOOP_STAGES,
    _declared_identity,
    PROGRESS_LOGGER,
    add_sample_arguments,
    announce_run_dir,
    InputResult,
    OfflinePipeline,
    _setup_logging,
    load_config,
    resolve_io,
    resolve_run_dir,
    sample_overrides,
)
from ..reporting import summary_writer as SW
from ..wrappers.dorado_server import DoradoServerError, dorado_server
from ..runtime.cli_result import (emit_error, emit_result, exit_code_for,
                                  stamp_run_outcome)
from ..runtime.lifecycle import (
    AbortFlag,
    FinalizeReason,
    install_signal_handlers,
    restore_signal_handlers,
)
from ..runtime.reference_check import ReferenceError
from ..runtime.run_status import write_run_status
from ..analysis.minknow import read_final_summary
from .backpressure import (
    Backpressure,
    DEFAULT_MAX_QUEUE_DEPTH,
    DEFAULT_MAX_STRIDE,
    DEFAULT_RESUME_FRACTION,
)
from .manifest import IngestManifest, MANIFEST_NAME
from .sealing import (
    DEFAULT_SEAL_BUDGET_S,
    DEFAULT_SEAL_POLL_INTERVAL_S,
    DEFAULT_SEAL_STABLE_POLLS,
    RunSealer,
    build_run_accounting,
    read_sequencing_summary,
)
from .watcher import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_RUN_END_GLOB,
    DEFAULT_STABLE_POLLS,
    DirectoryWatcher,
    FileReadyEvent,
)

logger = logging.getLogger(__name__)
#: The always-visible console channel (see main.PROGRESS_LOGGER).
progress_log = logging.getLogger(PROGRESS_LOGGER)

#: Seconds between heartbeat lines.
DEFAULT_PROGRESS_INTERVAL_S = 30.0

PARTIAL_SUFFIX = ".partial"
DEFAULT_QUIESCENCE_WARN_S = 900.0
"""Warn after this long with no arrival. Sized above the longest realistic batch duration
(3600 s default is longer still, so this is configurable) — a warning, never an action."""


class State(enum.Enum):
    BOOT = "boot"
    REPLAY = "replay"
    LIVE = "live"
    SEALING = "sealing"
    FINALIZE = "finalize"


@dataclass
class _Progress:
    """Input names by outcome; FINALIZE builds the run accounting from them."""

    observed: set[str] = field(default_factory=set)
    ingested: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    deferred: set[str] = field(default_factory=set)
    unstable: set[str] = field(default_factory=set)
    # Never attempted because of an abort: still queued, or not reached by sealing.
    aborted: set[str] = field(default_factory=set)


class RealtimePipeline:
    """Watch a MinKNOW run folder and produce a QC report as the run proceeds."""

    def __init__(
        self,
        run_dir: Path | str,
        output_dir: Path | str,
        config: Mapping[str, Any],
        **offline_kwargs: Any,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.output_dir = Path(output_dir)
        # The offline pipeline holds Core, the stages, the checkpoint and the config;
        # this class adds the feed and the lifecycle.
        self.core = OfflinePipeline(
            self.run_dir, self.output_dir, config, **offline_kwargs)

        rt = dict(self.core.realtime)
        self.poll_interval_s = float(rt.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S))
        self.stable_polls = int(rt.get("stable_polls", DEFAULT_STABLE_POLLS))
        self.pod5_subdir = rt.get("pod5_subdir", "pod5")
        self.run_end_glob = rt.get("run_end_glob", DEFAULT_RUN_END_GLOB)
        self.seal_budget_s = float(rt.get("seal_budget_s", DEFAULT_SEAL_BUDGET_S))
        # Sealing has its own, shorter stability window: once the marker is written no
        # new file appears, so it only waits out a final flush.
        self.seal_poll_interval_s = float(
            rt.get("seal_poll_interval_s", DEFAULT_SEAL_POLL_INTERVAL_S))
        self.seal_stable_polls = int(
            rt.get("seal_stable_polls", DEFAULT_SEAL_STABLE_POLLS))
        self.interim_every_n = int(rt.get("interim_every_n", 1))
        # Backpressure: once max_queue_depth inputs are waiting (0 = never), QC thins
        # its sample to keep up instead of falling behind. `sampling` decides what a
        # skipped POD5 means (see the backpressure module).
        self.backpressure = Backpressure(
            max_queue_depth=int(rt.get("max_queue_depth", DEFAULT_MAX_QUEUE_DEPTH)),
            resume_fraction=float(rt.get("backpressure_resume_fraction",
                                         DEFAULT_RESUME_FRACTION)),
            max_stride=int(rt.get("backpressure_max_stride", DEFAULT_MAX_STRIDE)),
            sampling=bool(rt.get("backpressure_sampling", True)),
        )
        self.quiescence_warn_s = float(
            rt.get("quiescence_warn_s", DEFAULT_QUIESCENCE_WARN_S))

        self.state = State.BOOT
        self.manifest = IngestManifest(self.output_dir / MANIFEST_NAME,
                                       root=self.output_dir)
        self.progress = _Progress()
        self._queue: "queue.Queue[Optional[FileReadyEvent]]" = queue.Queue()
        self._run_end = threading.Event()
        self._abort = AbortFlag()
        self._last_arrival = time.monotonic()
        self._n_interim = 0
        # The input in progress, for the heartbeat thread. A single attribute
        # assignment, so it needs no lock.
        self._current_input: Optional[str] = None
        # The last progress written to run_status.json, so an idle poll writes nothing.
        self._status_key: Optional[tuple] = None
        self._heartbeat_stop = threading.Event()
        self.progress_interval_s = float(rt.get("progress_interval_s",
                                                DEFAULT_PROGRESS_INTERVAL_S))
        # Set by the CLI when the feed is the MinKNOW mimic, and recorded in provenance:
        # the reads are real recorded POD5s, but their arrival timing is simulated.
        self.mimic_params: Optional[dict[str, Any]] = None

    @property
    def pod5_dir(self) -> Path:
        return self.run_dir / self.pod5_subdir if self.pod5_subdir else self.run_dir

    # -- BOOT -------------------------------------------------------------

    def boot(self) -> None:
        """Remove the half-written ``*.partial`` artifacts of a killed stage."""
        self.state = State.BOOT
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stale = sorted(self.output_dir.rglob(f"*{PARTIAL_SUFFIX}*"))
        for path in stale:
            # Outputs are published by rename, so a *.partial is always debris.
            logger.warning("BOOT: removing stale partial artifact %s", path.name)
            if path.is_dir():
                import shutil
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        if stale:
            logger.info("BOOT: cleared %d stale partial artifact(s)", len(stale))

    # -- REPLAY -----------------------------------------------------------

    def replay(self) -> None:
        """On a restart, re-ingest the manifest's aligned BAMs into the fresh engine."""
        self.state = State.REPLAY
        report = self.manifest.replay(self.core.engine)
        for entry in report.replayed:
            self.progress.ingested.add(entry.pod5_name)
            self.progress.observed.add(entry.pod5_name)
        if report.n_replayed:
            logger.info(
                "REPLAY: %d input(s) restored from the manifest (%d primary reads). "
                "Anything after that is re-derived — the manifest bounds WORK, never "
                "which inputs belong in the report.",
                report.n_replayed, report.primary_total)
        self.core._n_processed = report.n_replayed

    # -- LIVE -------------------------------------------------------------

    def _write_status(self) -> None:
        """run_status.json: the POD5 files MinKNOW has finished so far, those done, and the
        one in progress (GUI round 2, 2026-11-12; runtime/run_status.py). Written from the
        watcher thread and the main thread, only when a count moved; never fatal, since a
        progress line must not end a run."""
        try:
            found, done = len(self.progress.observed), len(self.progress.ingested)
            key = (found, done, self._current_input)
            if key == self._status_key:
                return
            self._status_key = key
            write_run_status(self.output_dir, found=found, processed=done, total=None,
                             current=self._current_input)
        except (OSError, ValueError) as exc:
            logger.warning("run_status.json not written: %s", exc)

    def _on_ready(self, event: FileReadyEvent) -> None:
        """Watcher thread. Enqueue only — no QC work happens off the main thread."""
        name = event.path.name
        self.progress.observed.add(name)
        self._write_status()
        decision = self.backpressure.on_arrival(name, self._queue.qsize())
        if not decision.admit:
            # Deferred: re-swept at run end unless sampling is on (see seal), and named
            # in run_accounting if never ingested.
            self.progress.deferred.add(name)
            return
        self._queue.put(event)

    def _on_run_end(self, marker: Path) -> None:
        """Watcher thread. Signal only; the main thread decides what to do about it."""
        logger.info("Run-end marker seen: %s", marker.name)
        self._run_end.set()

    def live(self) -> None:
        """Consume watcher events until run-end or abort."""
        self.state = State.LIVE
        watcher = DirectoryWatcher(
            self.run_dir,
            pod5_subdir=self.pod5_subdir,
            on_ready=self._on_ready,
            on_run_end=self._on_run_end,
            poll_interval_s=self.poll_interval_s,
            stable_polls=self.stable_polls,
            run_end_glob=self.run_end_glob,
            # Files already there when QC starts (QC started mid-run) are processed too.
            emit_preexisting=True,
        )
        thread = threading.Thread(target=watcher.run, name="DirectoryWatcher", daemon=True)
        thread.start()
        logger.info("LIVE: watching %s", self.pod5_dir)
        # Up and watching, before MinKNOW's first POD5, which can be minutes away.
        self._write_status()
        try:
            while not (self._abort.is_set() or self._run_end.is_set()):
                self._write_status()
                try:
                    event = self._queue.get(timeout=self.poll_interval_s)
                except queue.Empty:
                    self._warn_if_quiet()
                    continue
                if event is not None:
                    self._handle(event.path)
            # Drain the queue. On run end these inputs belong in the report, so process
            # them (what the watcher had not released yet, sealing finds). On abort they
            # are cancelled work: record each one, so the report says it was skipped.
            aborted_now: list[str] = []
            while True:
                try:
                    event = self._queue.get_nowait()
                except queue.Empty:
                    break
                if event is None:
                    continue
                if self._abort.is_set():
                    name = event.path.name
                    # Also observed: build_run_accounting requires every bucket to be a
                    # subset of `observed`.
                    self.progress.observed.add(name)
                    self.progress.aborted.add(name)
                    aborted_now.append(name)
                    continue
                self._handle(event.path)
            if aborted_now:
                logger.warning(
                    "ABORT: %d queued input(s) were NOT processed and are absent from "
                    "every number in the report (see run_accounting.aborted_unprocessed).",
                    len(aborted_now))
        finally:
            watcher.stop()
            thread.join(timeout=self.poll_interval_s * 3)
            self._watcher_emitted = watcher.emitted_names

    def _warn_if_quiet(self) -> None:
        quiet = time.monotonic() - self._last_arrival
        if quiet >= self.quiescence_warn_s:
            logger.warning(
                "No new POD5 for %.0f min. MinKNOW pauses legitimately (mux scan, operator "
                "action) and a pause looks identical to a crash from here, so this is a "
                "WARNING only — finishing the run stays an explicit decision.", quiet / 60)
            self._last_arrival = time.monotonic()  # re-arm; warn periodically, not per poll

    # -- shared per-input path --------------------------------------------

    def _progress_line(self) -> str:
        """One line an operator can read at a glance: alive, where, and how far behind."""
        ingested = len(self.progress.ingested)
        observed = len(self.progress.observed)
        reads = self.core.engine.primary_total
        now = self._current_input or "—"
        return (f"[QC] {ingested}/{observed} inputs done · {self._progress_label().strip()} "
                f"· {reads:,} primary reads · now: {now}")

    def _heartbeat(self) -> None:
        """Log the progress line every ``progress_interval_s``, from its own thread.

        Its own thread because the main loop blocks inside each basecall. It only reads
        counters, so it needs no lock.
        """
        if self.progress_interval_s <= 0:
            return  # explicitly disabled
        while not self._heartbeat_stop.wait(self.progress_interval_s):
            try:
                progress_log.info("%s", self._progress_line())
            except Exception:  # a status line must never be able to end a run
                logger.debug("progress line failed", exc_info=True)

    def _progress_label(self) -> str:
        """The log prefix for this input's lines: queue depth and estimated lag.

        The live counterpart of the offline ``[3/400]``. The lag is left out until an
        input has completed, and when the queue is empty.
        """
        depth = self._queue.qsize()
        lag = self.backpressure.estimated_lag_s(depth)
        if lag is None or depth == 0:
            return f"[queue {depth}] "
        if lag < 90:
            return f"[queue {depth}, ~{lag:.0f}s behind] "
        return f"[queue {depth}, ~{lag / 60:.0f} min behind] "

    def _handle(self, pod5: Path) -> Optional[InputResult]:
        """Process one POD5 through the offline driver's per-input step."""
        name = pod5.name
        self.progress.observed.add(name)
        if name in self.progress.ingested:
            # Once per run, by this in-process set (not by the manifest).
            logger.debug("Already ingested this run: %s", name)
            return None
        self._last_arrival = time.monotonic()

        t0 = time.monotonic()
        self._current_input = name
        self._write_status()
        try:
            result = self.core._process_one_input(pod5, label=self._progress_label())
        finally:
            self._current_input = None
        self.backpressure.on_completed(time.monotonic() - t0)
        if result is None:
            self.progress.failed.add(name)
            self._write_status()
            return None

        self.progress.ingested.add(name)
        self._write_status()
        self.progress.deferred.discard(name)
        self.manifest.append(
            pod5_name=name, aligned_bam=result.bam,
            n_records=result.n_records, primary_total_after=result.primary_total)
        self._maybe_interim(result)
        return result

    def _maybe_interim(self, result: InputResult) -> None:
        """An interim snapshot and HTML report every ``interim_every_n`` inputs.

        Only once primary reads exceed ``min_reads``; the PDF is rendered at finalize.
        """
        if not self.interim_every_n:
            return
        if len(self.progress.ingested) % self.interim_every_n:
            return
        if result.primary_total <= self.core.min_reads:
            return
        self._n_interim += 1
        try:
            self.core._emit_interim(result.primary_total)
        except Exception:
            # Losing an interim report must not end a live run.
            logger.exception("Interim render failed; continuing")

    # -- SEALING ----------------------------------------------------------

    def seal(self) -> None:
        """Process the directory's POD5s not yet handled, within the seal budget."""
        self.state = State.SEALING
        # Under sampling, deferred inputs count as handled: sweeping them now would bias
        # the uniform-in-time sample toward the end of the run. Otherwise the sweep
        # catches up on them, within seal_budget_s.
        handled = self.progress.ingested | self.progress.failed
        if self.backpressure.sampling:
            handled = handled | self.progress.deferred
            if self.progress.deferred:
                logger.warning(
                    "SEALING: %d input(s) were sampled out and will NOT be analysed. The "
                    "report is based on a subsample and says so in its banner.",
                    len(self.progress.deferred))
        elif self.progress.deferred:
            logger.info("SEALING: catching up on %d input(s) deferred under backpressure",
                        len(self.progress.deferred))
        sealer = RunSealer(
            self.pod5_dir,
            poll_interval_s=self.seal_poll_interval_s,
            stable_polls=self.seal_stable_polls,
            budget_s=self.seal_budget_s,
        )
        # The sweep honours an abort: it can be long, since if QC starts after the run
        # has ended, every input is processed here.
        report = sealer.sweep(already_handled=handled, on_ready=self._handle,
                              stop=self._abort.event)
        if report.aborted:
            self.progress.aborted.update(report.aborted)
            self.progress.observed.update(report.aborted)
            logger.warning(
                "ABORT: %d input(s) were never attempted during sealing and are absent "
                "from every number in the report (run_accounting.aborted_unprocessed).",
                len(report.aborted))
        for name in report.unstable_at_seal:
            # Unstable means still changing; a deferred input just ran out of budget.
            if name not in self.progress.deferred:
                self.progress.unstable.add(name)
        self.progress.observed.update(report.unstable_at_seal)
        self.progress.deferred -= self.progress.ingested
        if self.progress.deferred:
            logger.warning(
                "%d deferred input(s) never caught up before the seal budget expired: %s",
                len(self.progress.deferred), ", ".join(sorted(self.progress.deferred)[:8]))
        self._seal_report = report

    # -- FINALIZE ---------------------------------------------------------

    def finalize(self, reason: FinalizeReason) -> dict[str, Any]:
        """Render the final report and write ``qc_result.json``; return the result."""
        self.state = State.FINALIZE
        # MinKNOW writes its telemetry files when the run ends. This driver never calls
        # OfflinePipeline.run(), so it reads them here.
        with self.core._stage("instrument_s"):
            self.core._refresh_instrument()
        # Before the render: the banner uses the share of reads analysed.
        coverage = read_sequencing_summary(self.run_dir)

        # The inputs missing from the numbers, by cause. Under sampling, deferred inputs
        # are the unsampled part, not failures.
        deferred = self.progress.deferred
        self.core.reporter.note_incomplete_inputs(
            sorted(self.progress.failed | self.progress.unstable
                   | (set() if self.backpressure.sampling else deferred)),
            aborted_inputs=sorted(self.progress.aborted),
            sampled_inputs=sorted(deferred) if self.backpressure.sampling else (),
            analyzed_read_fraction=(
                coverage.fraction_covered(self.progress.ingested) if coverage else None),
            total_inputs=len(self.progress.observed) or None)
        self.core.reporter.note_sample(
            declared=_declared_identity(self.core.config.get("sample")),
            recorded=read_final_summary(self.pod5_dir))
        # Before the render.
        self.core.reporter.note_run_context(
            reference=self.core.reference,
            mode="pod5 (real time)",
            inputs=f"{len(self.progress.ingested)} POD5 from {self.pod5_dir}",
            basecall_model=self.core.basecall_model,
            minimum_reads=f"{self.core.min_reads:,} mapped",
            minimum_reads_per_construct=f"{self.core.min_reads_per_construct:,} assigned",
            sampling=(
                f"on, queue depth {self.backpressure.max_queue_depth}"
                if self.backpressure.sampling and self.backpressure.max_queue_depth
                else "off (every input analysed)"),
            tools=", ".join(f"{k} {v}" for k, v in
                            sorted((self.core.engine.tool_provenance or {}).items()))
                 or None,
        )
        self.core.engine.finalize_all()
        # The final render is in no per-input stage, so it is timed on its own.
        t_fin0 = time.perf_counter()
        result = self.core._finalize(reason)
        self.core._timing["finalize_render_s"] = round(time.perf_counter() - t_fin0, 2)

        seal_report = getattr(self, "_seal_report", None)
        accounting = build_run_accounting(
            observed=self.progress.observed,
            ingested=self.progress.ingested,
            failed=self.progress.failed,
            unstable=self.progress.unstable,
            deferred=self.progress.deferred,
            aborted=self.progress.aborted,
            sampling=self.backpressure.sampling,
            seal_budget_exceeded=bool(seal_report and seal_report.budget_exceeded),
            coverage=coverage,
            # Primary reads per POD5, to check each one brought all of its reads.
            primary_by_pod5=self.core._primary_by_input,
        )
        accounting["n_interim_reports"] = self._n_interim
        accounting["dedup"] = self.core.engine.dedup_accounting
        accounting["state"] = self.state.value
        # Whether QC kept up with the sequencer, reported even with backpressure off.
        accounting["backpressure"] = self.backpressure.snapshot(self._queue.qsize())
        if seal_report is not None:
            accounting["appeared_after_marker"] = seal_report.appeared_after_marker

        tm = self.core._timing
        start = getattr(self, "_t_start", None)
        if start is not None:
            now = time.perf_counter()
            tm["total_s"] = round(now - start, 2)
            run_end = getattr(self, "_t_run_end", None)
            # QC time after the sequencer stopped; None when there was no run end.
            tm["after_run_end_s"] = (None if run_end is None
                                     else round(now - run_end, 2))
            named = sum(tm.get(k) or 0.0 for k in _LOOP_STAGES)
            tm["loop_measured_s"] = round(named, 2)
            # Not `unaccounted_s` as offline: here the residual is mostly waiting for
            # files to arrive, and idle time is not separated from unmeasured work.
            tm["idle_or_unaccounted_s"] = round(tm["total_s"] - named, 2)
        # False under the mimic feed: its reads are real, its arrival timing is not.
        tm["wall_clock_is_real"] = self.mimic_params is None

        SW.write_result_json(
            self.output_dir, self.core._bundle(), self.core.integrator, result=result,
            inputs=sorted(self.pod5_dir.glob("*.pod5")),
            run_accounting=accounting, timing=self.core._timing,
            params={"config": self.core.config,
                    "driver": "realtime" if self.mimic_params is None else "realtime+mimic",
                    "dorado_server": self.core.server_info,
                    **({"mimic": self.mimic_params} if self.mimic_params else {})},
            reason=reason.value,
            tools=self.core.engine.tool_provenance)
        # total=None: a live run has no fixed input set to count against.
        stamp_run_outcome(result, reason=reason,
                          processed=len(self.progress.ingested), total=None)
        logger.info("FINALIZE: status=%s reason=%s ingested=%d observed=%d",
                    result.get("status"), reason.value,
                    len(self.progress.ingested), len(self.progress.observed))
        return result

    # -- the whole run ----------------------------------------------------

    def _abort_notice(self) -> bytes:
        """The message Ctrl-C prints to stderr, once per press.

        An abort finishes the current input and renders the final report, so it is not
        instant; the message says so, and how to stop hard.
        """
        return (
            f"\n[{PRODUCT_NAME}] Abort requested.\n"
            f"[{PRODUCT_NAME}]   Finishing the input in flight, then writing the terminal report "
            f"(can take a minute).\n"
            f"[{PRODUCT_NAME}]   Queued inputs will NOT be processed; the report will say so.\n"
            f"[{PRODUCT_NAME}]   Pressing Ctrl-C again does not speed this up. To stop hard:\n"
            f"[{PRODUCT_NAME}]     kill -9 {os.getpid()}\n"
            f"[{PRODUCT_NAME}]   then recover the report from the last snapshot with:\n"
            f"[{PRODUCT_NAME}]     python -m mrnaspect.main --output-dir {self.output_dir} "
            f"--resume-from-snapshot\n"
        ).encode("utf-8", "replace")

    def run(self) -> dict[str, Any]:
        """BOOT, REPLAY, LIVE, then SEALING (on run end only) and FINALIZE.

        Returns the result of :meth:`finalize`.
        """
        # For finalize's total_s and after_run_end_s.
        self._t_start = time.perf_counter()
        self._t_run_end: Optional[float] = None
        prev = install_signal_handlers(self._abort, notice=self._abort_notice())
        # From before BOOT to after FINALIZE: replay and the final render are long too.
        beat = threading.Thread(target=self._heartbeat, name="Progress", daemon=True)
        beat.start()
        try:
            self.boot()
            self.replay()
            self.live()
            # The sequencer has stopped (or the operator aborted).
            self._t_run_end = time.perf_counter()
            reason = (FinalizeReason.USER_ABORT if self._abort.is_set()
                      else FinalizeReason.RUN_END)
            if reason is FinalizeReason.RUN_END:
                # Only on run end: after an abort the input set is not closed, and the
                # sweep would wait on files still being written.
                self.seal()
                # An abort during the sweep still makes it a user_abort.
                if self._abort.is_set():
                    reason = FinalizeReason.USER_ABORT
            self._write_status()
            return self.finalize(reason)
        finally:
            self._heartbeat_stop.set()
            beat.join(timeout=2.0)
            restore_signal_handlers(prev)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
#
# A separate entry point from mrnaspect.main: that one takes a directory that already
# holds every input, this one a MinKNOW run root that is still being written. The config
# and logging helpers are shared with it.


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    from .mimic import DEFAULT_PROFILE as _MIMIC_DEFAULT_PROFILE  # noqa: F401
    from .mimic import PROFILES as _MIMIC_PROFILES

    # No fixed `prog`: the help names the command as it was invoked.
    p = argparse.ArgumentParser(
        description=f"{PRODUCT_NAME} real-time QC: watch a live MinKNOW run and report as it goes.")
    # type=str, not Path (Path("") is Path(".")), and not `required`: the config may
    # supply these.
    p.add_argument(
        "--input-dir", type=str, default=None,
        help="MinKNOW RUN ROOT — the directory holding pod5/ and, at run end, "
             "final_summary_*.txt. NOT the pod5/ directory itself: the run-end marker "
             "lives one level above it, and a watcher that never sees the marker never "
             "finishes the run. (The offline driver's --input-dir holds the input FILES; "
             "here it is one level up, because the marker does not live with them.)")
    p.add_argument("--output-dir", type=str, default=None,
                   help="PARENT dir for OUTPUT. This run's report + qc_result.json + "
                        "manifest + run.log go in a subdirectory of it (see "
                        "--output-name).")
    p.add_argument("--output-name", type=str, default=None,
                   help="Subdirectory of --output-dir for this run's results (default "
                        "run_YYYYMMDD_HHMMSS). Naming an EXISTING one continues that run: "
                        "its manifest replays and its basecall cache is reused.")
    p.add_argument("--continue-latest", action="store_true",
                   help="Continue the most recent run_* subdirectory of --output-dir.")
    p.add_argument("--config", type=Path, default=None, action="append", metavar="YAML",
                   help="run_config.yaml (optional). Repeatable; each one merges over the "
                        "one before it. The GUI uses this to add `mode: pod5` on top of "
                        "whatever config the operator supplied.")
    # Override flags default to None, so "not passed" leaves the config's value.
    p.add_argument("--reference", type=str, default=None)
    # Required here (this driver is pod5-only), from this flag or the config.
    p.add_argument("--basecall-model", default=None,
                   help="Path to the dorado model folder. This pipeline is specified for "
                        "rna004_130bps_hac@v5.3.0.")
    p.add_argument("--min-reads", type=int, default=None)
    p.add_argument("--min-reads-per-construct", type=int, default=None)
    p.add_argument("--arm", default=None,
                   help="m1psi or U: every construct's arm (sample.arm). Constructs that "
                        "differ go in the config's sample.arms.")
    # The offline driver's sample flags, from its own table: the GUI sends them to both.
    add_sample_arguments(p)
    p.add_argument("--on-error", choices=["skip", "raise"], default=None)
    # --- the `realtime:` config section -----------------------------------
    p.add_argument("--pod5-subdir", default=None,
                   help="Subdir of --input-dir that MinKNOW writes POD5s into "
                        "(default 'pod5'; empty string = the run dir itself).")
    p.add_argument("--poll-interval-s", type=float, default=None,
                   help="Watcher poll period. poll_interval_s x stable_polls is the "
                        "size-stability window a file must survive to be released.")
    p.add_argument("--stable-polls", type=int, default=None)
    p.add_argument("--seal-budget-s", type=float, default=None,
                   help="Seconds the end-of-run sweep may spend waiting on files that "
                        "were still being written when the marker landed.")
    p.add_argument("--interim-every-n", type=int, default=None,
                   help="Render an interim QC report every N inputs. 0 = off.")
    p.add_argument("--max-queue-depth", type=int, default=None,
                   help="Queue depth at which QC starts thinning the sample to keep up; 0 = "
                        "never thin, and the run just falls behind. Default: "
                        "realtime.max_queue_depth in the config (0 in the shipped config).")
    p.add_argument("--no-sampling", dest="sampling", action="store_const", const=False,
                   default=None,
                   help="Analyse every POD5 even when the queue backs up: thinned inputs "
                        "are re-swept at run-end instead of being left out, so the report "
                        "is a census. The run may finish well after the sequencer does.")
    # --- dorado-server (basecall.server) ----------------------------------
    p.add_argument("--dorado-device", default=None,
                   help="GPU(s) for a server this run starts, e.g. 'cuda:3'. Default: an "
                        "idle GPU picked from nvidia-smi (a busy one is never taken; the "
                        "choice is logged). Set basecall.server.min_free_gpus_remaining "
                        "in your config to also reserve idle GPUs for other users.")
    p.add_argument("--dorado-port", type=int, default=None,
                   help="Port to reach or bind (default 5555). A server already listening "
                        "there is adopted and never stopped.")
    p.add_argument("--no-dorado-autostart", action="store_true",
                   help="Refuse to start a server; require one to be running already.")
    # --- MinKNOW mimic feed ------------------------------------------------
    # Writes recorded POD5s into --input-dir the way a sequencer would, to exercise this
    # entry point (watcher, sealing, backpressure, abort) without MinKNOW. The reads are
    # real; only their arrival pattern is simulated.
    m = p.add_argument_group(
        "MinKNOW mimic: replay a finished run without a sequencer",
        "Feed this run from recorded POD5s instead of a sequencer. The report is stamped "
        "driver=realtime+mimic in qc_result.json and must not back a reported number that "
        "depends on timing.")
    m.add_argument("--mimic-source-dir", type=Path, default=None,
                   help="Directory of MinKNOW-named *.pod5 to replay. Passing this turns "
                        "the mimic ON and --input-dir becomes the folder it CREATES.")
    m.add_argument("--mimic-profile", default=None, choices=sorted(_MIMIC_PROFILES),
                   help="Recorded write topology to reproduce.")
    m.add_argument("--mimic-speed", type=float, default=1.0,
                   help="Divide every interval by this (60 = one minute per simulated "
                        "hour). Does NOT speed up basecalling, so a high value is how you "
                        "make QC fall behind on purpose.")
    m.add_argument("--mimic-limit", type=int, default=None,
                   help="Replay only the first N source POD5s.")
    m.add_argument("--mimic-startup-delay", type=float, default=0.0,
                   help="Seconds before the first file lands (QC attaching early).")
    m.add_argument("--mimic-fault", action="append", default=[], metavar="INDEX:NAME",
                   help="Inject a fault at batch INDEX; repeatable. See mimic.Fault.")
    m.add_argument("--mimic-allow-dirty", action="store_true",
                   help="Replay into a run folder that already holds POD5s or a run-end "
                        "marker. Refused by default — see the error for why.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


# Every --mimic-* option and its argparse default, to detect one given without
# --mimic-source-dir.
_MIMIC_FLAG_DEFAULTS: Mapping[str, Any] = {
    "mimic_profile": None,
    "mimic_speed": 1.0,
    "mimic_limit": None,
    "mimic_startup_delay": 0.0,
    "mimic_fault": [],
    "mimic_allow_dirty": False,
}


def _dirty_mimic_target(run_dir: Path) -> Optional[str]:
    """An error message if ``run_dir`` already holds POD5s or a run-end marker.

    Sealing sweeps the directory, so stale POD5s would be ingested as this replay's, and
    a stale ``final_summary_*.txt`` ends the run on the first poll. Nothing is deleted:
    the leftovers could be a real run's data. None when the folder is clean.
    """
    pod5s = sorted(p.name for p in (run_dir / "pod5").glob("*.pod5")) \
        if (run_dir / "pod5").is_dir() else []
    markers = sorted(p.name for p in run_dir.glob("final_summary_*.txt"))
    if not pod5s and not markers:
        return None
    return (
        f"--input-dir {run_dir} is not empty: {len(pod5s)} POD5(s) and {len(markers)} "
        f"run-end marker(s) are already there ({', '.join((pod5s + markers)[:3])}...). "
        f"Sealing sweeps the directory, so those would be ingested as part of THIS drill, "
        f"and a leftover marker ends the run on the first poll. Use a fresh --input-dir, "
        f"delete the old one yourself, or pass --mimic-allow-dirty if that is what you "
        f"want.")


def _start_mimic(args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    """Start the mimic writing into ``run_dir`` on a daemon thread; return its settings.

    A daemon thread, so it cannot hold an aborted run open. Its errors are logged, not
    raised: a failed feed must not look like a failed pipeline.
    """
    from .mimic import DEFAULT_PROFILE as _MIMIC_DEFAULT_PROFILE
    from .mimic import Batch, MinKNOWMimic, _parse_faults  # imported only for a mimic

    batches = Batch.from_dir(args.mimic_source_dir, limit=args.mimic_limit)
    faults = _parse_faults(args.mimic_fault) if args.mimic_fault else None
    profile = args.mimic_profile or _MIMIC_DEFAULT_PROFILE
    mimic = MinKNOWMimic(
        run_dir, batches, profile,
        speed=args.mimic_speed,
        startup_delay_s=args.mimic_startup_delay,
        faults=faults,
        # The constructor has its own, stricter non-empty check; the flag lifts it too.
        allow_dirty=bool(args.mimic_allow_dirty),
    )

    def _feed() -> None:
        try:
            result = mimic.run()
            logger.info("MIMIC: feed finished (%s)", result)
        except Exception:
            logger.exception(
                "MIMIC: the FEED failed. This is the test harness, not the pipeline — the "
                "run below will simply see fewer inputs than intended.")

    threading.Thread(target=_feed, name="MinKNOWMimic", daemon=True).start()
    return {
        "source_dir": str(Path(args.mimic_source_dir).resolve()),
        "profile": profile,
        "speed": args.mimic_speed,
        "limit": args.mimic_limit,
        "startup_delay_s": args.mimic_startup_delay,
        "faults": dict(faults or {}),
        "n_batches": len(batches),
        # True: the folder was allowed to hold earlier POD5s or a marker, so the inputs
        # may not all come from this replay.
        "allow_dirty": bool(args.mimic_allow_dirty),
    }


def _log_startup(pipe: "RealtimePipeline", cfg: Mapping[str, Any]) -> None:
    """Log the resolved settings before any file is touched.

    Among them the directory being watched: watching the wrong one gives a clean, empty
    and wrong report.
    """
    stability = pipe.poll_interval_s * pipe.stable_polls
    logger.info("REAL-TIME QC — resolved configuration")
    logger.info("  run dir         : %s", pipe.run_dir)
    logger.info("  watching        : %s  (*.pod5)", pipe.pod5_dir)
    logger.info("  run-end marker  : %s", pipe.run_end_glob)
    logger.info("  output dir      : %s", pipe.output_dir)
    logger.info("  reference       : %s", cfg.get("reference"))
    logger.info("  basecall model  : %s", pipe.core.basecall_model)
    logger.info("  minimap2 flags  : %s", pipe.core.align_flags)
    logger.info("  file stability  : %.1fs x %d polls = %.0fs",
                pipe.poll_interval_s, pipe.stable_polls, stability)
    logger.info("  seal budget     : %.0fs", pipe.seal_budget_s)
    logger.info("  interim report  : %s",
                f"every {pipe.interim_every_n} input(s)" if pipe.interim_every_n else "off")
    logger.info("  min_reads       : %s", cfg.get("min_reads"))
    if pipe.mimic_params is not None:
        mp = pipe.mimic_params
        logger.warning("  FEED            : MinKNOW MIMIC (test drill), "
                       "NOT a sequencer")
        logger.warning("    source        : %s (%d batch(es), limit=%s)",
                       mp["source_dir"], mp["n_batches"], mp["limit"])
        logger.warning("    profile/speed : %s / x%g", mp["profile"], mp["speed"])
        if mp["faults"]:
            logger.warning("    faults        : %s", mp["faults"])
        logger.warning("    The reads are real; the ARRIVAL TIMING is manufactured. Any "
                       "number that depends on wall clock (duty cycle, latency, span) "
                       "describes the drill, not a run.")
    if not pipe.pod5_dir.exists():
        # Normal before MinKNOW writes its first batch, and also what a typo looks like.
        logger.warning(
            "%s does not exist yet. That is NORMAL if the run has not started writing, "
            "and is what a mistyped --input-dir/--pod5-subdir also looks like. Nothing will "
            "be ingested until POD5s appear there.", pipe.pod5_dir)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the CLI; the outcome is printed as JSON (``runtime.cli_result``).

    Exit codes: 0, a report (status ``report`` or ``insufficient``); 1, no verdict (an
    unusable reference, a dorado-server error, or a report that could not render); 2, a
    usage or configuration error.
    """
    args = _parse_args(argv)
    # Config first: it may supply the input and output directories. The run directory
    # is resolved before logging, so run.log goes into it. Errors here exit 2.
    try:
        cfg = load_config(args.config, {
            "input_dir": args.input_dir,
            "output_dir": args.output_dir,
            "output_name": args.output_name,
            "reference": str(args.reference) if args.reference else None,
            "basecall.model": args.basecall_model,
            "min_reads": args.min_reads,
            "min_reads_per_construct": args.min_reads_per_construct,
            "sample.arm": args.arm,
            **sample_overrides(args),
            "on_error": args.on_error,
            "realtime.pod5_subdir": args.pod5_subdir,
            "realtime.poll_interval_s": args.poll_interval_s,
            "realtime.stable_polls": args.stable_polls,
            "realtime.seal_budget_s": args.seal_budget_s,
            "realtime.interim_every_n": args.interim_every_n,
            "realtime.max_queue_depth": args.max_queue_depth,
            "realtime.backpressure_sampling": args.sampling,
        })
        input_dir, output_parent, output_name = resolve_io(cfg, need_input=True)
        out_dir = resolve_run_dir(output_parent, output_name=output_name,
                                  continue_latest=args.continue_latest)
    # OSError: e.g. a missing --config file.
    except (ValueError, OSError) as exc:
        return emit_error(str(exc), exit_code=2)
    existed = out_dir.exists()  # sample BEFORE _setup_logging creates it
    _setup_logging(out_dir, args.verbose)
    announce_run_dir(out_dir, existed=existed)

    # No --mode flag: the watcher releases only *.pod5, so this driver is pod5-only. The
    # shipped default is bam, so refuse once here rather than fail on every POD5.
    mode = cfg.get("mode")
    if mode != "pod5":
        msg = (f"real-time QC requires mode=pod5, but the resolved config says "
               f"mode={mode!r}. The watcher releases *.pod5 files and the bam branch "
               f"would misread every one of them. Set `mode: pod5` in your --config.")
        logger.error("%s", msg)
        return emit_error(str(msg), exit_code=2, run_dir=out_dir)

    # basecall.server.* is two levels deep, past load_config's dotted overrides. Each
    # flag overrides one key of the block.
    server_cfg = cfg.setdefault("basecall", {}).setdefault("server", {})
    if args.dorado_device is not None:
        server_cfg["device"] = args.dorado_device
    if args.dorado_port is not None:
        server_cfg["port"] = args.dorado_port
    if args.no_dorado_autostart:
        server_cfg["auto_start"] = False

    mimicking = args.mimic_source_dir is not None
    if mimicking:
        # The mimic writes this directory, so it need not exist yet.
        input_dir.mkdir(parents=True, exist_ok=True)
    elif not input_dir.exists():
        # pod5/ may appear late, but the run root exists from the start.
        msg = (f"--input-dir {input_dir} does not exist. Point it at the MinKNOW run "
               f"root (the dir containing pod5/), not at a path that has yet to be made.")
        logger.error("%s", msg)
        return emit_error(str(msg), exit_code=2, run_dir=out_dir)
    stray = [k for k, default in _MIMIC_FLAG_DEFAULTS.items()
             if getattr(args, k) != default]
    if mimicking and not args.mimic_allow_dirty:
        dirty = _dirty_mimic_target(input_dir)
        if dirty:
            logger.error("%s", dirty)
            return emit_error(str(dirty), exit_code=2, run_dir=out_dir)

    if not mimicking and stray:
        # A --mimic-* option without --mimic-source-dir: no mimic would run.
        msg = (f"{sorted(stray)} given without --mimic-source-dir, so NO mimic will run. "
               "Pass --mimic-source-dir to enable the drill, or drop the options.")
        logger.error("%s", msg)
        return emit_error(str(msg), exit_code=2, run_dir=out_dir)

    try:
        pipe = RealtimePipeline(input_dir, out_dir, cfg)
        if mimicking:
            pipe.mimic_params = _start_mimic(args, input_dir)
        _log_startup(pipe, cfg)
        # One server for the whole run, so the model loads once. An existing server is
        # adopted (and left running); otherwise one is started and later stopped.
        with dorado_server(cfg["basecall"].get("server"),
                           output_dir=out_dir) as server:
            pipe.core.server_info = server
            result = pipe.run()
    except (ReferenceError, DoradoServerError) as exc:
        # An unusable reference, or a dorado-server that cannot be reached or started:
        # exit 1, as offline. Narrow on purpose: a RuntimeError from inside the run is a
        # bug, and is not caught.
        logger.error("%s", exc)
        return emit_error(str(exc), exit_code=1, run_dir=out_dir)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        # Configuration errors at startup (e.g. no model): exit 2.
        logger.error("%s", exc)
        return emit_error(str(exc), exit_code=2, run_dir=out_dir)

    return emit_result(result, run_dir=out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
