"""mRNAspect offline driver: QC over a finished set of inputs.

Each input (a BAM of basecalled reads, aligned or not, or a POD5 to basecall) is
aligned and goes through ``AnalysisEngine`` once, in natural filename order, and
``CoAReporter`` renders the report. The real-time driver (``mrnaspect.realtime.main``) runs the same per-input step,
``OfflinePipeline._process_one_input``, on inputs from a watcher.

- SIGINT/SIGTERM stop the run between inputs; what was processed is finalized as
  ``user_abort``.
- ``latest_snapshot.json`` is rewritten atomically after each input, so
  ``--resume-from-snapshot`` can still render a report after a hard kill. In pod5 mode
  each finished basecall is cached, and a restart in the same run directory reuses it.
- An interim report is rendered every N inputs, and optionally every T seconds.
- ``run.log`` in the run directory logs the whole run.

Usage:
    mrnaspect --input-dir <dir> --output-dir <dir> --reference <fasta> --mode bam
    mrnaspect --output-dir <dir> --output-name <run> --reference <fasta> \\
        --resume-from-snapshot
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
from contextlib import contextmanager
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from . import PRODUCT_NAME
from .analysis.engine import (
    AnalysisEngine, DEFAULT_MAX_CONSTRUCTS, DEFAULT_MAX_TRACKED_READS,
    MaxConstructsExceeded)
from .analysis.minknow import (PORE_ACTIVITY_GLOB, PORE_SCAN_GLOB,
                               read_final_summary,
                               SEQUENCING_SUMMARY_GLOB, find_run_root)
from .analysis.modules.end_reason import EndReasonModule
from .analysis.modules.polya import MixedModelError
from .analysis.modules.pore_activity import PoreActivityModule
from .analysis.modules.voltage import VoltageModule
from .analysis.modules.qc_integrator import QCResultIntegrator
from . import snapshot_keys as keys
from .reporting import summary_writer as SW
from .reporting.reporter import (
    CoAReporter,
    DEFAULT_MIN_READS,
)
from .runtime.checkpoint import (Checkpoint, DEFAULT_STATE_NAME, ModelFingerprint,
                                 StaleBasecallError, file_sha256, model_fingerprint)
from .runtime.cli_result import (emit_error, emit_result, exit_code_for,
                                 stamp_run_outcome)
from .runtime.input_folder import (BAM_FAIL_SUBDIR, BAM_SUBDIR, describe,
                                   discover_inputs, no_inputs_message)
from .runtime.run_dir_state import describe_run_dir
from .runtime.run_input import check_run_input, run_input
from .runtime.run_status import write_run_status
from .runtime.reference_check import ReferenceError, reference_contigs
from .runtime.lifecycle import (
    AbortFlag,
    FinalizeReason,
    install_signal_handlers,
    restore_signal_handlers,
)
from .wrappers._shell import resolve_tool
from .wrappers.basecaller import Basecaller, shell_basecall
from .wrappers.aligner import (Aligner, missing_minimap2_flags, resolve_align_threads,
                                shell_align)
from .wrappers.dorado_server import DoradoServerError, dorado_server

PROGRESS_LOGGER = "mrnaspect.progress"
logger = logging.getLogger(__name__)
# Shown on the console at INFO even without -v, where `logger` shows only WARNING:
# for what an operator needs to see while the run is going.
progress_log = logging.getLogger(PROGRESS_LOGGER)

# The read-level QC modules, recorded in provenance as `module_set`. run_summary and
# polya run for the whole run and per construct; the others per construct only.
MODULES = ("run_summary", "polya", "consensus", "error_hotspot", "mapq", "terminal_reach")
_MODE_GLOB = {"bam": "*.bam", "pod5": "*.pod5"}
#: What `on_error` may be. `skip`: an unreadable input is left out and named in the
#: report. `raise`: the run stops at the first one. The documents are checked against this.
ON_ERROR_VALUES = ("skip", "raise")
RUN_LOG_NAME = "run.log"

# The shipped default config, loaded by every run: the only copy of the driver defaults.
_SHIPPED_CONFIG = Path(__file__).resolve().parent / "config" / "run_config.yaml"

#: `sample:` keys that are analysis inputs rather than identity. They are reported where
#: they are used (the poly(A) correction block), not as rows of the sample table.
_SAMPLE_NON_IDENTITY = ("arm", "arms")


def _declared_identity(sample_cfg):
    """The ``sample`` block minus the analysis inputs, for ``note_sample``."""
    return {k: v for k, v in dict(sample_cfg or {}).items()
            if k not in _SAMPLE_NON_IDENTITY}


#: What the operator declares about the sample (`sample:` in the config), as
#: ``(flag, key, help)``: the fields the report prints, in its order. One table for both
#: drivers' parsers, because the live driver, which a GUI live run launches, had none.
SAMPLE_FLAGS: tuple[tuple[str, str, Optional[str]], ...] = (
    ("--manufacturer", "manufacturer", None),
    ("--product", "product", "Product or construct name."),
    ("--batch-id", "batch_id", "Batch / lot identifier."),
    ("--sample-id", "sample_id", "Sample identifier, if different from MinKNOW's."),
    ("--manufacture-date", "manufacture_date", None),
    ("--operator", "operator", "Who ran it."),
)


def add_sample_arguments(p: argparse.ArgumentParser) -> None:
    """Add the sample flags to a driver's parser. Nothing can verify them; they are
    reported as given, apart from the instrument's own record."""
    for flag, _key, help_text in SAMPLE_FLAGS:
        p.add_argument(flag, default=None, help=help_text)


def sample_overrides(args: argparse.Namespace) -> dict[str, Optional[str]]:
    """The sample flags as ``load_config`` overrides (``sample.<key>``).

    argparse stores ``--batch-id`` as ``batch_id``, so each flag's key is its dest.
    """
    return {f"sample.{key}": getattr(args, key) for _flag, key, _help in SAMPLE_FLAGS}


# Keys the driver dereferences directly; a config without them is refused at load.
_REQUIRED_KEYS = ("reference", "mode", "min_reads", "min_reads_per_construct", "on_error")

# Wrong-reference guard. A valid FASTA of the wrong construct passes reference_check and
# then maps nothing, which looks like a failed flow cell. Checked once, when cumulative
# primary reads first reach MAPPING_GUARD_MIN_READS (reads, not inputs, which vary in
# size). 1000 reads: even at a true mapping rate of 1 %, P(none mapped) ~ 4e-5.
# 1 %: a wrong reference maps ~0 %, so a poor but real run passes, and so does a
# reference that lacks some of the sample's constructs (it maps partially).
MAPPING_GUARD_MIN_READS = 1000
MAPPING_GUARD_MIN_RATE = 0.01


@dataclasses.dataclass(frozen=True)
class InputResult:
    """What one input produced through basecall, align and ingest.

    Returned by :meth:`OfflinePipeline._process_one_input`, which both drivers use.
    ``bam`` is the aligned BAM that was ingested and ``n_records`` the records processed
    from it. ``primary_total`` is ``engine.primary_total`` after this input (the
    real-time manifest records it and checks it on replay). ``basecall_cached`` is True
    when the basecall came from the cache.
    """

    path: Path
    bam: Path
    n_records: int
    primary_total: int
    basecall_s: float
    align_s: float
    ingest_s: float
    basecall_cached: bool


# ---------------------------------------------------------------------------
# Config: shipped YAML -> user --config -> CLI (CLI wins)
# ---------------------------------------------------------------------------

def _load_yaml_mapping(path: Path, *, what: str) -> dict[str, Any]:
    """Load a YAML file that must exist and be a mapping; raise otherwise."""
    if not path.exists():
        raise FileNotFoundError(
            f"{what} not found at {path}. The shipped run_config.yaml is the source of the "
            "pipeline's driver defaults; if it is missing the package install is broken "
            "(reinstall), or pass an explicit --config.")
    import yaml  # local import: keep the package importable without PyYAML present
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{what} at {path} must be a YAML mapping, got {type(data).__name__}")
    return data


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> None:
    """Merge ``over`` into ``base`` in place, recursing into mappings at any depth.

    A list is replaced whole, not extended.
    """
    for key, val in over.items():
        if isinstance(val, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], val)
        else:
            base[key] = val


#: ``--config``: one path, several (merged in order), or None for the shipped defaults.
ConfigArg = Union[None, str, Path, Sequence[Union[str, Path]]]


def _config_paths(arg: ConfigArg) -> list[Path]:
    """The config paths as a list, in merge order; a str is one path."""
    if arg is None:
        return []
    if isinstance(arg, (str, Path)):
        return [Path(arg)]
    return [Path(p) for p in arg]


def load_config(config_path: ConfigArg, cli_overrides: dict[str, Any],
                *, require_reference: bool = True) -> dict[str, Any]:
    """The effective config: shipped YAML, then each ``--config`` in order, then CLI.

    CLI values that are not None or blank win. Their keys may be dotted
    (``"cadence.interim_every_n"``) to reach one level into a section. The result is
    the config recorded in provenance. ``require_reference=False`` is for
    ``--print-config``, which may be asked before a reference is chosen.
    """
    cfg = _load_yaml_mapping(_SHIPPED_CONFIG, what="shipped default config (run_config.yaml)")
    for path in _config_paths(config_path):
        _merge(cfg, _load_yaml_mapping(path, what="config file (--config)"))
    for key, val in cli_overrides.items():
        # Blank is absent: an empty field must not overwrite the config's value.
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        if "." in key:
            section, sub = key.split(".", 1)
            cfg.setdefault(section, {})[sub] = val
        else:
            cfg[key] = val
    # Name a dropped required key here rather than fail with a KeyError later.
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(
            f"config is missing required key(s) {missing} (resolved from "
            f"{_SHIPPED_CONFIG.name} + any --config/CLI overrides)")
    # Sections read with [...] downstream must be mappings, not scalars.
    for section in ("cadence", "durability", "module_params", "basecall", "align",
                    "realtime", "analysis"):
        if section in cfg and not isinstance(cfg[section], dict):
            raise ValueError(
                f"config section '{section}' must be a mapping, got "
                f"{type(cfg[section]).__name__}")
    # There is no default reference: one would QC the sample against another construct.
    if require_reference and not cfg.get("reference"):
        raise ValueError(
            "no reference was supplied. The reference construct is per-run and user-"
            "provided, so this pipeline ships no default — one would silently QC your "
            "sample against somebody else's construct (almost no read would map, which "
            "looks like a failed flow cell, not a wrong setting). Pass "
            "--reference <your.fasta>, or set `reference:` in your --config.")
    return cfg


# ---------------------------------------------------------------------------
# The offline pipeline
# ---------------------------------------------------------------------------

#: The timed stages of the input loop; `total_s` minus their sum is `unaccounted_s`.
#: The final render is not one of them: `total_s` stops before finalize, which is timed
#: on its own as `finalize_render_s`.
_LOOP_STAGES = ("basecall_s", "align_s", "ingest_s", "instrument_s",
                "durability_s", "interim_snapshot_s", "interim_render_s")


class OfflinePipeline:
    """Process every input in a directory through the QC engine, once, in order."""

    def __init__(
        self,
        input_dir: Path | str,
        output_dir: Path | str,
        config: Mapping[str, Any],
        *,
        basecaller: Optional[Basecaller] = None,
        aligner: Optional[Aligner] = None,
        state_file: Optional[Path | str] = None,
        continues: bool = False,
    ) -> None:
        """``continues``: the run folder was there before this run (a run name reused),
        so this run continues an earlier one; run_status.json says so (``continued``).
        The caller decides, since the folder exists by the time this is built."""
        # When Core started this run, for the elapsed time in interim JSON and reports
        # (2026-11-10). Set once, here, not in _reset_run_state: the live driver
        # starts dorado-server between building this and running it, and the GUI's own
        # Elapsed counts that wait too. Monotonic, so a clock change cannot bend it.
        self._started_mono = time.monotonic()
        # The effective config from load_config(); there are no defaults here.
        self.config = dict(config)
        mode = self.config["mode"]
        if mode not in _MODE_GLOB:
            raise ValueError(f"mode must be one of {sorted(_MODE_GLOB)}, got {mode!r}")
        on_error = self.config.get("on_error", "skip")
        if on_error not in ON_ERROR_VALUES:
            raise ValueError(f"on_error must be 'skip' or 'raise', got {on_error!r}")

        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.reference = Path(self.config["reference"])
        # Checked before any basecalling: minimap2 accepts a FASTA with no records and
        # leaves every read unmapped.
        self.reference_contigs = reference_contigs(self.reference)
        logger.info("Reference %s: %d contig(s) — %s", self.reference.name,
                    len(self.reference_contigs),
                    ", ".join(self.reference_contigs[:8])
                    + (" ..." if len(self.reference_contigs) > 8 else ""))
        self.mode = mode
        self.min_reads = int(self.config["min_reads"])
        self.min_reads_per_construct = int(self.config["min_reads_per_construct"])
        self.on_error = on_error
        # pod5 mode: the dorado-server model (a stock model or an exported fine-tune).
        self.basecall_model = self.config.get("basecall", {}).get("model")
        # The move table: nothing here reads it, and it about doubles the align time.
        self.emit_moves = self.config.get("basecall", {}).get("emit_moves", False)
        if not isinstance(self.emit_moves, bool):
            raise ValueError(
                f"basecall.emit_moves must be true or false, got {self.emit_moves!r}")
        # The dorado-server that basecalled this run, for provenance. Set by the CLI;
        # None in bam mode.
        self.server_info: Optional[dict[str, Any]] = None
        self.align_flags = self.config.get("align", {}).get("minimap2_flags")
        # Both modes align with these; empty means run_align.sh's own default.
        if self.align_flags:
            missing = missing_minimap2_flags(str(self.align_flags))
            if missing:
                raise ValueError(
                    f"align.minimap2_flags {self.align_flags!r} lacks "
                    f"{' and '.join(missing)}. -a makes minimap2 write SAM; without it the "
                    "output is PAF and every input fails. -y copies the basecaller tags, "
                    "pt among them, into the SAM; without it every read still aligns but "
                    "poly(A) and the model check have nothing to read. In a cluster such "
                    "as -ax, letters count up to the first option that takes a value.")
        # minimap2's -t: align.threads, every CPU when null; None when the flags set it.
        self.align_threads = resolve_align_threads(
            self.config.get("align", {}).get("threads"), self.align_flags)
        # pod5 mode needs a model; checked here rather than at the first POD5.
        if self.mode == "pod5" and not self.basecall_model:
            raise FileNotFoundError(
                "pod5 mode needs basecall.model — the dorado model folder to basecall "
                "with. There is no default: models are downloaded from ONT by the user and "
                "do not live in a predictable place. This pipeline is specified for "
                "rna004_130bps_hac@v5.3.0 (@v6.0.0 models crash the server). "
                "Pass --basecall-model, or set `basecall: {model: /path/to/rna004_130bps_hac@v5.3.0}` in your config.")
        if self.mode == "pod5" and self.basecall_model:
            model_path = Path(self.basecall_model)
            # A relative value may be a model name the server resolves itself, so only
            # an absolute path is checked.
            if model_path.is_absolute() and not model_path.exists():
                raise FileNotFoundError(
                    f"basecall.model does not exist: {model_path}. Check the version — the "
                    f"7.13.6 bundle ships models/rna004_130bps_{{fast,hac,sup}}@v5.2.0 and "
                    f"bin/rna004_{{fast,hac,sup}}@v6.0.0 (the v6 arch crashes the bundled "
                    f"1.3.2 engine). An exported fine-tune folder is also valid.")
        # Two injectable stages; the defaults are bound from config:
        #   basecaller: (pod5, out_dir) -> ubam dir              (GPU; cached)
        #   aligner:    (prefix, ref, out_dir) -> aligned BAM    (CPU; always re-run)
        if basecaller is None:
            _server = dict(self.config.get("basecall", {}).get("server", {}) or {})
            client = _server.get("client")
            if self.mode == "pod5":
                # Resolved now: under `on_error: skip` a missing client would otherwise
                # fail once per POD5. It is also looked for beside the server, whether
                # the server is named in config or by $DORADO_SERVER.
                client = resolve_tool(
                    "ont_basecall_client", client, env_var="DORADO_CLIENT",
                    sibling_of=_server.get("binary") or os.environ.get("DORADO_SERVER"),
                    hint="it ships inside the ONT dorado-server bundle, which is "
                         "proprietary and cannot be bundled here. Set "
                         "basecall.server.binary to the server in that bundle (the client "
                         "is found beside it), set basecall.server.client, or export "
                         "$DORADO_CLIENT.")
            basecaller = functools.partial(
                shell_basecall, model=self.basecall_model,
                client=client,
                server_binary=_server.get("binary") or os.environ.get("DORADO_SERVER"),
                emit_moves=self.emit_moves)
        if aligner is None:
            aligner = functools.partial(shell_align, align_flags=self.align_flags,
                                        emit_moves=self.emit_moves,
                                        threads=self.align_threads)
        self.basecaller = basecaller
        self.aligner = aligner

        cadence = self.config.get("cadence", {})
        self.interim_every_n = int(cadence.get("interim_every_n", 0))
        self.interim_interval_s = cadence.get("interim_interval_s")
        self.durability_latest = bool(
            self.config.get("durability", {}).get("latest_snapshot", True))
        # Per-module overrides; a module with none uses its own DEFAULTS.
        self.module_params = dict(self.config.get("module_params") or {})
        # The poly(A) correction uses delta_pct only if the run was aligned with the
        # flags its constants were fitted with, so the module is given them.
        self.module_params["polya"] = {
            **(self.module_params.get("polya") or {}),
            "minimap2_flags": self.align_flags,
        }
        self._interim_count = 0
        # Real-time driver settings (unused offline), in the same config object.
        self.realtime = dict(self.config.get("realtime", {}) or {})
        # Here as well as in run(): the real-time driver calls _process_one_input
        # without ever calling run().
        self._reset_run_state()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint = Checkpoint(
            Path(state_file) if state_file else self.output_dir / DEFAULT_STATE_NAME
        )
        # What a continued run carries over, for run_status.json: the basecalls on record
        # (none in bam mode, which records none). Every input is aligned and counted again.
        self._continued: Optional[dict[str, int]] = (
            {"basecalls_cached": len(self.checkpoint)} if continues else None)
        # What a cached basecall must have been made with to be reused: the model's name
        # and a digest of its folder. Read once, here, so a folder that cannot be read
        # stops the run before the first input.
        self._model_fp: Optional[ModelFingerprint] = (
            model_fingerprint(self.basecall_model) if self.mode == "pod5" else None)
        # input name -> its cached basecall as checked at the start of run(), so each
        # file is hashed once.
        self._cache_checked: dict[str, Optional[Path]] = {}
        # The basecalled ubam dirs (pod5 mode) or links to the input BAMs (bam mode),
        # and the aligned BAMs. A restart reuses a cached ubam instead of basecalling
        # again.
        self._basecall_cache_dir = self.output_dir / "basecall_cache"
        # A resource limit, not a method parameter: it changes no reported number.
        analysis_cfg = dict(self.config.get("analysis", {}) or {})
        self.max_constructs = int(
            analysis_cfg.get("max_constructs", DEFAULT_MAX_CONSTRUCTS))
        self.reporter = CoAReporter(self.output_dir, min_reads=self.min_reads,
                                    min_reads_per_construct=self.min_reads_per_construct,
                                    started_monotonic=self._started_mono)
        self.engine = AnalysisEngine(
            self.reporter, reference=self.reference,
            module_params=self.module_params,
            sample=self.config.get("sample"),
            max_constructs=self.max_constructs,
            max_tracked_reads=int(analysis_cfg.get(
                "max_tracked_reads", DEFAULT_MAX_TRACKED_READS)),
        )
        self.integrator = QCResultIntegrator()
        # Instrument telemetry: modules fed from MinKNOW's run files, not from reads.
        # Whether a file exists is checked at every refresh; absence is reported.
        params = self.module_params or {}
        self.pore_activity = PoreActivityModule(params.get("pore_activity"))
        self.end_reason = EndReasonModule(params.get("end_reason"))
        self.voltage = VoltageModule(params.get("voltage"))
        self._instrument: tuple[tuple[str, Any, str], ...] = (
            (keys.PORE_ACTIVITY, self.pore_activity, PORE_ACTIVITY_GLOB),
            (keys.END_REASON, self.end_reason, SEQUENCING_SUMMARY_GLOB),
            (keys.VOLTAGE, self.voltage, PORE_SCAN_GLOB),
        )
        self._instrument_roots: dict[str, Optional[Path]] = {}

    # -- helpers -------------------------------------------------------------

    def _bundle(self) -> dict[str, Any]:
        """The reporter's current ``{module: snapshot}`` bundle."""
        return dict(self.reporter._snapshots)

    def _mapped_reads(self) -> int:
        snap = self.reporter._snapshots.get("run_summary", {})
        return int(snap.get("mapping", {}).get("mapped", 0))

    def _discover_inputs(self) -> list[Path]:
        """The inputs in acquisition order; raises if there are none.

        Natural order: ``_2.pod5`` before ``_10.pod5`` (see ``runtime/ordering.py``).
        """
        if not self.input_dir.is_dir():
            raise NotADirectoryError(f"input_dir is not a directory: {self.input_dir}")
        # One rule for what a folder gives, shared with the summary shown before a run
        # (runtime/input_folder.py): in bam mode a MinKNOW run folder is read as its
        # bam_pass/ and bam_fail/ together.
        inputs = discover_inputs(self.input_dir, self.mode)
        if not inputs:
            raise FileNotFoundError(no_inputs_message(self.input_dir, self.mode))
        return inputs

    def _inputs_text(self, inputs: Sequence[Path]) -> str:
        """The run's inputs in one line for the report: how many files, from where, and
        in bam mode what became of MinKNOW's failed reads."""
        said = f"{len(inputs)} file(s) from {self.input_dir}"
        if self.mode != "bam":
            return said
        bam = describe(str(self.input_dir), "bam").get("bam")
        if not bam:
            return said
        if not bam.get("pass_included", True):
            # bam_fail/ given by itself: the failed reads only.
            n = bam["pass"]["inputs"]
            return f"{said} ({BAM_SUBDIR}/ not read: {n} file{'' if n == 1 else 's'})" \
                if n else said
        if bam["fail_included"]:
            return (f"{said}: {bam['pass']['inputs']} in {BAM_SUBDIR}/, "
                    f"{bam['fail']['inputs']} in {BAM_FAIL_SUBDIR}/")
        n = bam["fail"]["inputs"]
        return f"{said} ({BAM_FAIL_SUBDIR}/ not read: {n} file{'' if n == 1 else 's'})" \
            if n else said

    def _write_status(self, current: Optional[str]) -> None:
        """run_status.json: how far the run has got, from before its first input (GUI
        round 2, 2026-11-12; runtime/run_status.py). Never fatal: it carries no QC
        number, nothing reads it back, and a progress line must not end a run."""
        try:
            write_run_status(self.output_dir, found=self._inputs_total or 0,
                             processed=self._n_processed, total=self._inputs_total,
                             current=current, continued=self._continued)
        except (OSError, ValueError) as exc:
            logger.warning("run_status.json not written: %s", exc)

    # -- per-input processing (shared with the real-time driver) --------------

    def _reset_run_state(self) -> None:
        """Zero the per-run counters (also called from ``__init__``)."""
        self._n_processed = 0
        # How many inputs the run has, set by run() once it has globbed them. The
        # real-time driver never calls run(), so a live run leaves it None: its inputs
        # are still arriving, and latest_snapshot.json then carries no inputs_total.
        self._inputs_total: Optional[int] = None
        self._n_basecall_cached = 0
        self._failed_inputs: list[str] = []
        # Seconds per stage of the input loop (see _LOOP_STAGES), and per input.
        self._timing: dict[str, Any] = {
            "basecall_s": 0.0, "align_s": 0.0, "ingest_s": 0.0,
            "instrument_s": 0.0, "durability_s": 0.0,
            "interim_snapshot_s": 0.0, "interim_render_s": 0.0,
            "per_input": []}
        # input name -> primary reads it added, for the real-time driver's check of
        # per-POD5 reads against MinKNOW's own counts (sealing.check_read_coverage).
        self._primary_by_input: dict[str, int] = {}
        # The mapping-rate guard is evaluated once per run.
        self._mapping_guard_done = False

    @contextmanager
    def _stage(self, key: str):
        """Add the block's wall time to ``_timing[key]``, also when it raises."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._timing[key] += time.perf_counter() - t0

    @staticmethod
    def _fmt_stat(value: Optional[float]) -> str:
        """A statistic for a message; ``n/a`` when the accumulator had no data."""
        return "n/a" if value is None else f"{value:.1f}"

    def _check_mapping_rate(self) -> None:
        """Stop a run whose reads do not map to the reference.

        Evaluated once, when cumulative primary reads first reach
        ``MAPPING_GUARD_MIN_READS``. It raises rather than warns: the fault is in the
        configuration, so every later input would fail the same way.

        Raises:
            ReferenceError: fewer than ``MAPPING_GUARD_MIN_RATE`` of primary reads mapped.
        """
        if self._mapping_guard_done:
            return
        summary = self.engine.run_summary.snapshot()
        mapping = summary["mapping"]
        total = mapping["total"]
        if total < MAPPING_GUARD_MIN_READS:
            return          # not yet enough reads to distinguish 0 % from bad luck
        self._mapping_guard_done = True
        rate = mapping["mapping_rate"]
        if rate >= MAPPING_GUARD_MIN_RATE:
            logger.info("Mapping-rate guard passed: %d/%d primary reads (%.2f%%) mapped "
                        "to %s.", mapping["mapped"], total, rate * 100.0,
                        self.reference.name)
            return

        # The rate cannot tell a wrong reference from a failed run; read length and
        # qscore can, so the message names both causes and gives that evidence.
        length = summary["read_length"]
        qs = summary["quality_score"]
        contigs = ", ".join(self.reference_contigs[:8])
        if len(self.reference_contigs) > 8:
            contigs += " ..."
        raise ReferenceError(
            f"Only {mapping['mapped']}/{total} primary reads "
            f"({rate * 100.0:.2f}%) mapped to {self.reference.name} — below the "
            f"{MAPPING_GUARD_MIN_RATE * 100.0:g}% floor, so this run is being stopped "
            f"before it reports over nothing.\n"
            f"  Two causes give this and the mapping rate alone cannot separate them:\n"
            f"    (1) the reference does not match the sample, or\n"
            f"    (2) the sequencing run / basecall failed.\n"
            f"  Evidence: read length mean={self._fmt_stat(length['mean'])} nt "
            f"(min={self._fmt_stat(length['min'])}, max={self._fmt_stat(length['max'])}), "
            f"qscore median={self._fmt_stat(qs['median'])}. Long, good-qscore reads that "
            f"do not map point at (1); short or low-qscore reads point at (2).\n"
            f"  Reference in use: {self.reference} [{contigs}]"
        )

    def _stage_bam_input(self, path: Path) -> None:
        """Put a bam-mode input where the aligner reads a basecall, ``basecall_<stem>/``.

        A symbolic link, not a copy: the input may be gigabytes on another disk, and
        run_align.sh follows links. The folder must hold nothing else, or its other BAMs
        would be aligned with this input.

        Raises:
            FileExistsError: the folder holds another file, e.g. a POD5 run's basecalls
                left in the same output folder.
        """
        staged = self._basecall_cache_dir / f"basecall_{path.stem}"
        staged.mkdir(parents=True, exist_ok=True)
        link = staged / path.name
        others = sorted(p.name for p in staged.iterdir() if p.name != path.name)
        if others:
            raise FileExistsError(
                f"{staged} already holds {', '.join(others)}, which would be aligned "
                f"together with {path.name}. Give this run an output folder of its own.")
        # A rerun in the same folder replaces its own link.
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(path.resolve())

    def _cached_basecall(self, path: Path) -> Optional[Path]:
        """The cached basecall of ``path``, if made from this file with this model.

        Raises:
            StaleBasecallError: it was made from another file or with another model.
        """
        if path.name in self._cache_checked:
            return self._cache_checked[path.name]
        return self.checkpoint.verified(
            path.name, input_path=path, model=self._model_fp)

    def check_cached_models(self, names: Iterable[str]) -> None:
        """Stop if a cached basecall among ``names`` was made with another model.

        For the real-time driver, which restores finished inputs from their aligned
        BAMs without coming through the cache.

        Raises:
            StaleBasecallError: an entry names another model.
        """
        if self._model_fp is None:
            return
        for name in sorted(names):
            self.checkpoint.check_model(name, self._model_fp)

    def _check_cached_basecalls(self, inputs: Sequence[Path]) -> None:
        """Check every cached basecall of this run's inputs before any is used.

        The model first, for all of them: that costs nothing. Then each file's
        contents, which reads the file once; a resumed run finds out in minutes, not
        after the inputs before the changed one have been processed again.

        Raises:
            StaleBasecallError: an input or the model is not what the cache was made
                from.
        """
        self._cache_checked = {}
        if self.mode != "pod5":
            return
        cached = [p for p in inputs if p.name in self.checkpoint]
        if not cached:
            return
        self.check_cached_models(p.name for p in cached)
        progress_log.info(
            "Checking %d cached basecall(s) against their POD5 files…", len(cached))
        for path in cached:
            try:
                self._cache_checked[path.name] = self.checkpoint.verified(
                    path.name, input_path=path, model=self._model_fp)
            except OSError as exc:
                # A file that cannot be read is that input's failure, not the run's:
                # left unchecked, it comes up again in the input loop, under `on_error`.
                logger.warning("%s could not be read to check its cached basecall (%s)",
                               path.name, exc)

    def _process_one_input(
        self, path: Path, *, label: str = ""
    ) -> Optional["InputResult"]:
        """Basecall, align and ingest one input, with timing and the error policy.

        The step both drivers share. Input discovery, the abort check, interim cadence
        and finalize stay with each driver. Every input is ingested even on a restart,
        so the report is complete; the checkpoint only caches basecalls. In pod5 mode a
        cached basecall is reused. Alignment always reruns in both modes, so it follows
        the current reference and flags; in bam mode an alignment already in the file is
        discarded.

        Args:
            path: A POD5 (pod5 mode) or a BAM of basecalled reads (bam mode), aligned or
                not.
            label: Prefix for log lines, e.g. ``"[3/400] "``.

        Returns:
            An :class:`InputResult`, or ``None`` if the input failed under
            ``on_error: skip`` (logged, and listed in ``_failed_inputs``).

        Raises:
            ReferenceError: a reference that does not fit the data, under any
                ``on_error``.
            MaxConstructsExceeded: reads on more constructs than the configured limit,
                under any ``on_error``.
            StaleBasecallError: the cached basecall was made from another file or with
                another model, under any ``on_error``.
            Exception: whatever a stage raised, under ``on_error: raise``.
        """
        primary_before = self.engine.primary_total
        try:
            bc_s = al_s = 0.0
            cached = False
            if self.mode == "bam":
                # Aligned or not, the reads are aligned here (2026-11-03): a BAM
                # aligned upstream was trusted, flags and all, and an unaligned one,
                # which is what MinKNOW and dorado write, failed at open.
                self._stage_bam_input(path)
                self.engine.capture_input_provenance(path)
            else:
                cached_ubam = self._cached_basecall(path)
                if cached_ubam is not None:
                    cached = True
                    self._n_basecall_cached += 1
                    logger.info("%s%s: basecall served from cache", label, path.name)
                else:
                    # Announced before the call: basecalling is the longest stage, and
                    # silence would look like a hang.
                    progress_log.info("%s%s: basecalling…", label, path.name)
                    # What the basecall is made from, so a restart can tell whether
                    # the file under this name is still that one.
                    digest = file_sha256(path)
                    t_bc = time.perf_counter()
                    ubam = self.basecaller(path, self._basecall_cache_dir)
                    bc_s = time.perf_counter() - t_bc
                    # Recorded only once the ubam is complete, so an entry always means
                    # a usable ubam; a kill mid-basecall leaves none.
                    self.checkpoint.record(path.name, ubam, input_sha256=digest,
                                           model=self._model_fp)
            logger.info("%s%s: aligning…", label, path.name)
            t_al = time.perf_counter()
            bam = self.aligner(path.stem, self.reference, self._basecall_cache_dir)
            al_s = time.perf_counter() - t_al
            t_ing = time.perf_counter()
            n = self.engine.ingest_bam(bam)
            ing_s = time.perf_counter() - t_ing
            self._check_mapping_rate()
        except (ReferenceError, MixedModelError, MaxConstructsExceeded,
                StaleBasecallError):
            # `on_error: skip` skips a bad input; a wrong reference, a second basecaller
            # model, more constructs than `analysis.max_constructs` allows, or a cached
            # basecall of another file or model is wrong for the whole run. Skipped,
            # the third of these left the counts half updated, and the run ended on a
            # read-accounting error that hid the cause.
            raise
        except Exception as exc:
            if self.on_error == "raise":
                raise
            logger.warning("%s%s -> FAILED (%s); skipping and continuing",
                           label, path.name, exc)
            logger.debug("Failure detail for %s", path.name, exc_info=True)
            self._failed_inputs.append(path.name)
            # Now, not at finalize, so the next interim report says an input is missing.
            self.reporter.note_incomplete_inputs(self._failed_inputs)
            return None

        self._timing["basecall_s"] += bc_s
        self._timing["align_s"] += al_s
        self._timing["ingest_s"] += ing_s
        # `reads`: BAM records processed (a throughput figure), not primary reads.
        self._timing["per_input"].append({
            "input": path.name, "reads": n, "basecall_s": round(bc_s, 2),
            "align_s": round(al_s, 2), "ingest_s": round(ing_s, 2)})
        self._n_processed += 1
        # This input's own primary reads, not the running total.
        self._primary_by_input[path.name] = self.engine.primary_total - primary_before
        result = InputResult(
            path=path, bam=Path(bam), n_records=n,
            primary_total=self.engine.primary_total,
            basecall_s=bc_s, align_s=al_s, ingest_s=ing_s, basecall_cached=cached)

        # Refresh the instrument panels, then rewrite latest_snapshot.json for recovery.
        with self._stage("instrument_s"):
            self._refresh_instrument()
        if self.durability_latest:
            with self._stage("durability_s"):
                SW.dump_latest_snapshot(self.output_dir, self._bundle(), self.integrator,
                                        total_reads=result.primary_total,
                                        last_input=path.name,
                                        inputs_processed=self._n_processed,
                                        inputs_total=self._inputs_total,
                                        started_monotonic=self._started_mono)
        progress_log.info("%s%s -> %d reads (basecall %.1fs, align %.1fs, QC %.1fs)",
                          label, path.name, n, bc_s, al_s, ing_s)
        return result

    def _refresh_instrument(self) -> None:
        """Re-read MinKNOW's telemetry files and push their snapshots; never fatal.

        The modules raise on a malformed file. Here that is logged and recorded in the
        snapshot, and the last good parse is kept. MinKNOW writes these files when the
        run ends, so until then they are normally absent; ``run`` refreshes once more
        after the last input.
        """
        for key, module, pattern in self._instrument:
            # Searched for until found: the files appear only at run end, and when the
            # input is a pod5/ subdirectory they are one level up.
            if self._instrument_roots.get(key) is None:
                found = find_run_root(self.input_dir, pattern)
                if found is not None:
                    self._instrument_roots[key] = found
            root = self._instrument_roots.get(key) or self.input_dir
            try:
                module.update_from_run_dir(root)   # records absence when there is no file
                snap = module.snapshot()
            except (ValueError, OSError) as exc:
                # The exception text (with its path) goes to the log; the snapshot gets
                # only the cause, so reported values do not depend on paths.
                logger.warning("%s unreadable this input (%s); keeping last good snapshot",
                               key, exc)
                snap = module.snapshot()
                reason = f"{type(exc).__name__} while reading the {key} file"
                snap = (dict(snap, stale_reason=reason) if snap.get("available")
                        else {"available": False, "unavailable_reason": reason})
            self.reporter.update(key, snap)

    # -- run -----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Process every input in order, then finalize and write ``qc_result.json``.

        Returns the reporter's result, stamped with the outcome for the CLI.
        """
        inputs = self._discover_inputs()
        progress_log.info("Offline run: %d input(s), mode=%s, reference=%s",
                          len(inputs), self.mode, self.reference.name)

        # Before anything is written or processed: a cached basecall of another file or
        # model stops the run here.
        self._check_cached_basecalls(inputs)

        abort = AbortFlag()
        prev_handlers = install_signal_handlers(abort)
        total_reads = 0
        self._reset_run_state()
        # The glob above fixed the input set, so the snapshot can say "n of N".
        self._inputs_total = len(inputs)
        # Said before the first input: latest_snapshot.json comes only when one finishes.
        self._write_status(current=None)
        reason = FinalizeReason.RUN_END
        t_run0 = time.perf_counter()
        last_interim = t_run0
        try:
            for i, path in enumerate(inputs, 1):
                # Checked between inputs, so the one in progress finishes.
                if abort.is_set():
                    reason = FinalizeReason.USER_ABORT
                    logger.warning("Abort flag set -> stopping after %d/%d input(s); "
                                   "finalizing current state as user_abort",
                                   self._n_processed, len(inputs))
                    break
                self._write_status(current=path.name)
                res = self._process_one_input(path, label=f"[{i}/{len(inputs)}] ")
                if res is None:
                    continue
                total_reads = res.primary_total

                # Interims every N inputs and/or T seconds, once primary reads exceed
                # min_reads.
                if total_reads > self.min_reads:
                    by_count = (self.interim_every_n
                                and self._n_processed % self.interim_every_n == 0)
                    by_time = (self.interim_interval_s is not None
                               and time.perf_counter() - last_interim >= self.interim_interval_s)
                    if by_count or by_time:
                        self._emit_interim(total_reads)
                        last_interim = time.perf_counter()
        finally:
            restore_signal_handlers(prev_handlers)
        self._write_status(current=None)

        self._timing["total_s"] = round(time.perf_counter() - t_run0, 2)
        # What the timed stages do not account for is reported, not hidden.
        named = sum(self._timing[k] for k in _LOOP_STAGES)
        self._timing["loop_measured_s"] = round(named, 2)
        self._timing["unaccounted_s"] = round(self._timing["total_s"] - named, 2)
        for k in _LOOP_STAGES:
            self._timing[k] = round(self._timing[k], 2)

        # A restart re-ingests every input and reuses cached basecalls: still complete.
        if self._n_basecall_cached:
            logger.info("Continued restart: %d of %d input(s) reused a cached basecall "
                        "(re-aligned + re-ingested); report is complete.",
                        self._n_basecall_cached, len(inputs))
        if self._failed_inputs:
            logger.warning("%d input(s) failed and were skipped: %s — report reflects "
                           "%d of %d input(s).", len(self._failed_inputs),
                           ", ".join(self._failed_inputs), self._n_processed, len(inputs))
        # Again with the total, so the report can say "N of M".
        self.reporter.note_incomplete_inputs(
            self._failed_inputs, total_inputs=len(inputs))
        # `declared`: as the operator gave it; `recorded`: from MinKNOW's final summary.
        self.reporter.note_sample(
            declared=_declared_identity(self.config.get("sample")),
            recorded=read_final_summary(self.input_dir))
        # Before _finalize, which renders the report.
        self.reporter.note_run_context(
            reference=self.reference,
            mode=self.mode,
            inputs=self._inputs_text(inputs) if inputs else None,
            basecall_model=self.basecall_model,
            minimum_reads=f"{self.min_reads:,} mapped",
            minimum_reads_per_construct=f"{self.min_reads_per_construct:,} assigned",
            tools=", ".join(f"{k} {v}" for k, v in
                            sorted((self.engine.tool_provenance or {}).items())) or None,
        )

        # MinKNOW writes its telemetry files when the run ends, after the last input,
        # so this refresh is the one that finds them.
        with self._stage("instrument_s"):
            self._refresh_instrument()

        # Freeze the modules, then render.
        self.engine.finalize_all()
        # Timed apart from total_s, which stopped before finalize.
        t_fin0 = time.perf_counter()
        result = self._finalize(reason)
        self._timing["finalize_render_s"] = round(time.perf_counter() - t_fin0, 2)
        self._write_result_json(result, inputs, reason)
        # After the write: these keys are for stdout; qc_result.json already has the
        # same facts under `report.reason` and `run_accounting`.
        stamp_run_outcome(result, reason=reason, processed=self._n_processed,
                          total=len(inputs))
        logger.info("Report: status=%s reason=%s path=%s",
                    result.get("status"), reason.value, result.get("path"))
        return result

    def _finalize(self, reason: FinalizeReason) -> dict[str, Any]:
        """Render the final report; only a missing render dependency is tolerated."""
        try:
            return self.reporter.finalize(reason.value, force=True)
        except (ModuleNotFoundError, ImportError) as exc:
            logger.warning("Report render stack unavailable (%s); qc_result.json is "
                           "written, PDF/HTML skipped.", exc)
            # low_reads fields are None: the read-sufficiency check is in the render.
            return {"status": "render_unavailable", "path": None,
                    "mapped_reads": self._mapped_reads(), "low_reads": None,
                    "low_reads_state": None, "render_error": str(exc)}

    def _emit_interim(self, total_reads: int) -> None:
        """Write ``interim_<k>_snapshot.json`` and render an interim report.

        A missing render dependency skips the render; the snapshot is still written.
        """
        self._interim_count += 1
        label = f"interim_{self._interim_count}"
        # Snapshot and render are timed apart: one scales with constructs, the other
        # with figures.
        with self._stage("interim_snapshot_s"):
            bundle = self._bundle()
            # Atomic (tmp beside the target, then os.replace): a crash cannot leave a
            # truncated snapshot.
            payload = json.dumps(
                {"label": label, **SW.clock_stamp(self._started_mono),
                 "total_reads": total_reads,
                 "qc_metrics": self.integrator.integrate(bundle), "snapshots": bundle},
                indent=2)
            final = self.output_dir / f"{label}_snapshot.json"
            tmp = final.with_suffix(final.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, final)
        try:
            with self._stage("interim_render_s"):
                r = self.reporter.report(label, force=True)
            logger.info("Interim %s: %d reads, report status=%s -> %s",
                        label, total_reads, r.get("status"), r.get("path"))
        except (ModuleNotFoundError, ImportError) as exc:
            logger.warning("Interim %s: %d reads, snapshot saved; report render "
                           "unavailable (%s)", label, total_reads, exc)

    def _write_result_json(self, result: dict[str, Any], inputs: list[Path],
                           reason: FinalizeReason) -> Path:
        """Write ``qc_result.json``: results, run accounting, timing, config, tools."""
        run_accounting = {
            "n_inputs": len(inputs), "n_processed": self._n_processed,
            "n_basecall_cached": self._n_basecall_cached,
            "n_failed": len(self._failed_inputs), "failed_inputs": self._failed_inputs,
            "n_interim_reports": self._interim_count,
            # duplicate_primary_reads > 0: an input was ingested twice. The repeats were
            # dropped, not double-counted.
            "dedup": self.engine.dedup_accounting}
        params = {
            # The effective config. Module defaults it does not override are pinned by
            # the git commit recorded in provenance.
            "config": self.config,
            "module_set": list(MODULES),
            "dorado_server": self.server_info}
        out = SW.write_result_json(
            self.output_dir, self._bundle(), self.integrator, result=result,
            inputs=inputs, run_accounting=run_accounting, timing=self._timing,
            params=params, reason=reason.value,
            tools=self.engine.tool_provenance)
        logger.info("Wrote %s", out)
        return out


# ---------------------------------------------------------------------------
# Recovery: render a terminal CoA from the last durable snapshot (no re-ingest)
# ---------------------------------------------------------------------------

def resume_from_snapshot(output_dir: Path | str,
                         *, min_reads: int = DEFAULT_MIN_READS,
                         min_reads_per_construct: int = 0) -> dict[str, Any]:
    """Render a final ``user_abort`` report from ``latest_snapshot.json``.

    For a run killed before it could finalize (SIGKILL, OOM); ``output_dir`` is its run
    directory. The saved snapshots go to a fresh reporter; nothing is basecalled or
    re-ingested.

    Raises:
        RuntimeError: the run already finished normally; its result is not overwritten.
        FileNotFoundError: there is no ``latest_snapshot.json`` in ``output_dir``.
    """
    output_dir = Path(output_dir)

    # Never overwrite the result of a run that finished normally (reason run_end).
    result_path = output_dir / SW.RESULT_JSON_NAME
    if result_path.exists():
        prior_reason = None
        try:
            prior = json.loads(result_path.read_text(encoding="utf-8"))
            prior_reason = prior.get("report", {}).get("reason")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Existing %s is unreadable (%s); proceeding with resume.",
                           result_path.name, exc)
        if prior_reason == FinalizeReason.RUN_END.value:
            raise RuntimeError(
                f"{result_path.name} already exists with reason='run_end' — this run "
                "completed normally; refusing to overwrite it with a snapshot resume. "
                "Resume is only for a hard-killed run with no terminal report; use a "
                "fresh output dir to re-run.")

    snap_path = output_dir / SW.LATEST_SNAPSHOT_NAME
    if not snap_path.exists():
        # If this is the parent of run directories, name the ones that have a snapshot.
        candidates = sorted(d.name for d in output_dir.glob(f"{RUN_DIR_PREFIX}*")
                            if (d / SW.LATEST_SNAPSHOT_NAME).exists())
        hint = (f" This directory holds run subdirectories that DO have one: "
                f"{candidates[-5:]}. Pass --output-name <one of them>, or --continue-latest."
                if candidates else "")
        raise FileNotFoundError(
            f"no {SW.LATEST_SNAPSHOT_NAME} to resume from in {output_dir}.{hint}")
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    snapshots = data["snapshots"]
    logger.info("Resuming from %s (last_input=%s, total_reads=%s)",
                snap_path.name, data.get("last_input"), data.get("total_reads"))

    reporter = CoAReporter(output_dir, min_reads=min_reads,
                           min_reads_per_construct=min_reads_per_construct)
    for name, snap in snapshots.items():
        reporter.update(name, snap)
    integrator = QCResultIntegrator()
    try:
        result = reporter.finalize(FinalizeReason.USER_ABORT.value, force=True)
    except (ModuleNotFoundError, ImportError) as exc:
        logger.warning("Render stack unavailable (%s); writing JSON only.", exc)
        result = {"status": "render_unavailable", "path": None,
                  "mapped_reads": int(snapshots.get("run_summary", {})
                                      .get("mapping", {}).get("mapped", 0)),
                  "low_reads": None, "low_reads_state": None,
                  "render_error": str(exc)}
    SW.write_result_json(
        output_dir, snapshots, integrator, result=result, inputs=[],
        run_accounting={"recovered_from": snap_path.name,
                        "last_input": data.get("last_input")},
        timing={}, params={"resumed_from_snapshot": True, "min_reads": min_reads},
        reason=FinalizeReason.USER_ABORT.value)
    logger.info("Recovery report: status=%s path=%s", result.get("status"), result.get("path"))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: Prefix for the per-run subdirectory created under ``--output-dir``.
RUN_DIR_PREFIX = "run_"


#: A key missing from the layer below, as distinct from one set to None.
_ABSENT = object()


def _leaves(cfg: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """A config as dotted key -> value, one entry per leaf. A list is a leaf."""
    out: dict[str, Any] = {}
    for key, val in cfg.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, Mapping):
            out.update(_leaves(val, dotted + "."))
        else:
            out[dotted] = val
    return out


def config_changes(config_path: ConfigArg) -> list[dict[str, Any]]:
    """What each ``--config`` changes, file by file.

    For each file, the leaves whose value differs from the stack below it (the shipped
    defaults, then each earlier file). A value restated unchanged is not a change.
    """
    paths = _config_paths(config_path)
    below = _leaves(load_config(None, {}, require_reference=False))
    out: list[dict[str, Any]] = []
    for i, path in enumerate(paths):
        here = _leaves(load_config(paths[:i + 1], {}, require_reference=False))
        out.append({"path": str(path.resolve()),
                    "changes": {k: v for k, v in here.items()
                                if below.get(k, _ABSENT) != v}})
        below = here
    return out


def resolve_io(cfg: Mapping[str, Any], *, need_input: bool
               ) -> tuple[Optional[Path], Path, Optional[str]]:
    """The input dir, output dir and run name, from the CLI or the config.

    ``load_config`` has already applied precedence; this raises ``ValueError`` when
    neither supplied a required one. A blank value is absent, not the current directory.
    """
    def _set(key: str) -> Optional[str]:
        val = cfg.get(key)
        text = str(val).strip() if val is not None else ""
        return text or None

    out, inp, name = _set("output_dir"), _set("input_dir"), _set("output_name")
    if out is None:
        raise ValueError(
            "no output directory was supplied. Pass --output-dir <dir>, or set "
            "`output_dir:` in your --config.")
    if need_input and inp is None:
        raise ValueError(
            "no input directory was supplied. Pass --input-dir <dir>, or set "
            "`input_dir:` in your --config.")
    return (Path(inp) if inp else None, Path(out), name)


def resolve_run_dir(output_dir: Path, *, output_name: Optional[str] = None,
                    continue_latest: bool = False) -> Path:
    """The directory this run writes into: ``<output_dir>/<output_name>``.

    Each run gets its own subdirectory: artifacts are named by kind, so a second run in
    the same directory would overwrite the first. The basecall cache lives in the run
    directory, so continuing a run (an existing ``output_name``, or ``continue_latest``)
    reuses its basecalls. With neither, a new ``run_<YYYYMMDD_HHMMSS>`` is named.

    Args:
        output_dir: The parent directory.
        output_name: A subdirectory name; an existing one is continued.
        continue_latest: Continue the most recently modified ``run_*`` subdirectory.

    Raises:
        ValueError: ``output_name`` is not a single directory name, both selectors are
            given, or ``continue_latest`` finds nothing to continue.
    """
    output_dir = Path(output_dir)
    # One directory name: an absolute path, a separator, '..' or '.' would write
    # outside the run's own subdirectory.
    if output_name is not None:
        name = str(output_name).strip()
        if not name:
            output_name = None
        elif name in (".", "..") or Path(name).parts != (name,):
            raise ValueError(
                f"--output-name must be a single directory name, not a path: got "
                f"{output_name!r}. It names a subdirectory of the output directory; "
                f"a separator, '..' or an absolute path would write somewhere else.")
        else:
            output_name = name
    if output_name and continue_latest:
        raise ValueError(
            "--output-name and --continue-latest both given; they name the same thing two "
            "ways. Pass one.")
    if continue_latest:
        existing = [d for d in output_dir.glob(f"{RUN_DIR_PREFIX}*") if d.is_dir()]
        if not existing:
            raise ValueError(
                f"--continue-latest: no {RUN_DIR_PREFIX}* directory under {output_dir} to "
                f"continue. Drop the flag to start a new run.")
        return max(existing, key=lambda d: d.stat().st_mtime)
    if output_name:
        return output_dir / output_name
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    candidate = output_dir / f"{RUN_DIR_PREFIX}{stamp}"
    # Two runs started within the same second get separate directories.
    suffix = 1
    while candidate.exists():
        candidate = output_dir / f"{RUN_DIR_PREFIX}{stamp}_{suffix}"
        suffix += 1
    return candidate


def announce_run_dir(run_dir: Path, *, existed: bool) -> None:
    """Log whether the run is new or continues a directory (and reuses its basecalls).

    ``existed`` must be checked by the caller before ``_setup_logging``, which creates
    the directory for ``run.log``.
    """
    cache = run_dir / DEFAULT_STATE_NAME
    if not existed:
        logger.info("Run directory (new): %s", run_dir)
        return
    n_cached = 0
    if cache.exists():
        n_cached = sum(1 for line in cache.read_text(encoding="utf-8").splitlines()
                       if line.strip())
    logger.warning(
        "Run directory (CONTINUING an existing one): %s — %d cached basecall(s) will be "
        "reused where the POD5 file and the model are unchanged, and any report already "
        "in it will be replaced.", run_dir, n_cached)


# The progress channel is INFO from import on, also for callers that bypass the CLI's
# logging setup (left NOTSET, it would inherit root's WARNING and drop every line).
logging.getLogger(PROGRESS_LOGGER).setLevel(logging.INFO)


def _setup_logging(output_dir: Path, verbose: bool) -> None:
    """Log to the console (INFO with -v, else WARNING) and to run.log (INFO)."""
    level = logging.INFO if verbose else logging.WARNING
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let handlers filter
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    output_dir.mkdir(parents=True, exist_ok=True)
    fileh = logging.FileHandler(output_dir / RUN_LOG_NAME, encoding="utf-8")
    fileh.setLevel(logging.INFO)  # run.log always captures INFO regardless of -v
    fileh.setFormatter(fmt)
    # Replace any prior handlers (idempotent across repeated CLI calls in one process).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(fileh)

    # Progress lines: always on the console, and in run.log. Not propagated, or -v would
    # print each one twice.
    progress = logging.getLogger(PROGRESS_LOGGER)
    progress.setLevel(logging.INFO)  # as at import
    progress.propagate = False
    for h in list(progress.handlers):
        progress.removeHandler(h)
    p_console = logging.StreamHandler()
    p_console.setLevel(logging.INFO)
    # Time and message only; run.log keeps the full format.
    p_console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    progress.addHandler(p_console)
    progress.addHandler(fileh)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"{PRODUCT_NAME}: QC over a finished set of inputs.")
    # type=str, not Path, here and for --reference: Path("") is Path("."), so a blank
    # value would pass as the current directory. Not `required`: the config may supply
    # them, and resolve_io refuses when neither does.
    p.add_argument("--input-dir", type=str, default=None,
                   help="Dir of *.bam (bam) / *.pod5 (pod5). Or set `input_dir:` in your "
                        "--config.")
    p.add_argument("--output-dir", type=str, default=None,
                   help="PARENT dir. This run's artifacts go in a subdirectory of it "
                        "(see --output-name), so a second run cannot overwrite the first. "
                        "Or set `output_dir:` in your --config.")
    p.add_argument("--output-name", type=str, default=None,
                   help="Subdirectory of --output-dir for this run (default "
                        "run_YYYYMMDD_HHMMSS). Naming an EXISTING one continues that run: "
                        "its basecall cache is reused instead of re-basecalling.")
    p.add_argument("--continue-latest", action="store_true",
                   help="Continue the most recent run_* subdirectory of --output-dir "
                        "instead of starting a new one.")
    p.add_argument("--config", type=Path, default=None, action="append", metavar="YAML",
                   help="run_config.yaml (optional). Repeatable; each one merges over the "
                        "one before it.")
    p.add_argument("--print-config", action="store_true",
                   help="Print the RESOLVED config as JSON and exit, without running or "
                        "creating anything. What a surface shows the operator before a run, "
                        "and the run directory that run would write (run_dir).")
    p.add_argument("--describe-input", action="store_true",
                   help="Print, as JSON, the folder a run of this kind would read when "
                        "--input-dir names any level of a MinKNOW run's folders, how many "
                        "inputs it holds now (this driver's own glob), and the runs below "
                        "it; a blank --input-dir looks in MinKNOW's data folder. Reads "
                        "folder listings only and creates nothing.")
    p.add_argument("--live", action="store_true",
                   help="With --describe-input: for a live run, which reads the RUN "
                        "folder (the live driver watches its realtime.pod5_subdir). With "
                        "--print-config: the input is a live run's, for run_dir_state's "
                        "check of a run name that continues an earlier run.")
    # Override flags default to None, so "not passed" leaves the config's value.
    p.add_argument("--reference", type=str, default=None)
    p.add_argument("--mode", choices=sorted(_MODE_GLOB), default=None)
    p.add_argument("--basecall-model", default=None,
                   help="Path to the dorado model folder (pod5 mode). This pipeline is "
                        "specified for rna004_130bps_hac@v5.3.0.")
    p.add_argument("--min-reads", type=int, default=None)
    p.add_argument("--min-reads-per-construct", type=int, default=None,
                   help="A construct with fewer assigned reads is listed in the report "
                        "rather than given a section and figures.")
    # What the operator declares about the sample (`sample:` in the config).
    add_sample_arguments(p)
    # Declared too, but used by the analysis: without an arm the poly(A) correction is
    # withheld. Sets sample.arm for every construct; sample.arms overrides it per
    # construct.
    p.add_argument("--arm", default=None,
                   help="m1psi (fully m1Ψ-substituted) or U (canonical): every construct's "
                        "arm. Constructs that differ go in the config's sample.arms.")
    p.add_argument("--on-error", choices=["skip", "raise"], default=None)
    p.add_argument("--interim-every-n", type=int, default=None,
                   help="Render an interim QC report every N inputs (deterministic). 0=off.")
    p.add_argument("--interim-interval-s", type=float, default=None,
                   help="Optional wall-clock interim cadence (nondeterministic).")
    p.add_argument("--dorado-device", default=None,
                   help="pod5 mode: GPU(s) for a server this run starts, e.g. 'cuda:3'. "
                        "Default: an idle GPU picked from nvidia-smi (a busy one is never "
                        "taken; the choice is logged). Set basecall.server."
                        "min_free_gpus_remaining in your config to also reserve idle GPUs "
                        "for other users.")
    p.add_argument("--dorado-port", type=int, default=None,
                   help="pod5 mode: port to reach or bind (default 5555). A server already "
                        "listening there is adopted and never stopped.")
    p.add_argument("--no-dorado-autostart", action="store_true",
                   help="pod5 mode: refuse to start a server; require a running one.")
    p.add_argument("--resume-from-snapshot", action="store_true",
                   help="Render a terminal QC report from latest_snapshot.json (recovery).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the CLI; the outcome is printed as JSON (``runtime.cli_result``).

    Exit codes: 0, a report (status ``report`` or ``insufficient``), also when some
    inputs failed; 1, no verdict (a reference or dorado-server error, a report that
    could not render, or every input failed); 2, a usage or configuration error.
    """
    args = _parse_args(argv)
    # Config first: it may supply the input and output directories. The run directory
    # is resolved before logging, so run.log goes into it. Errors here exit 2.
    try:
        # A resume renders from the saved snapshot and never reads the reference.
        cfg = load_config(args.config, require_reference=not (
            args.print_config or args.describe_input or args.resume_from_snapshot),
            cli_overrides={
            "input_dir": args.input_dir,
            "output_dir": args.output_dir,
            "output_name": args.output_name,
            "reference": str(args.reference) if args.reference else None,
            "mode": args.mode,
            "basecall.model": args.basecall_model,
            "min_reads": args.min_reads,
            "min_reads_per_construct": args.min_reads_per_construct,
            "on_error": args.on_error,
            "cadence.interim_every_n": args.interim_every_n,
            "cadence.interim_interval_s": args.interim_interval_s,
            **sample_overrides(args),
            "sample.arm": args.arm,
        })
        if args.describe_input:
            # Which folder a run of this kind reads, from any level the operator picked
            # (GUI round 2, 2026-11-12). Read-only; its own JSON shape. The live driver's
            # pod5_subdir is the resolved config's, as the run would use it.
            pod5_subdir = (cfg.get("realtime") or {}).get("pod5_subdir", "pod5")
            # A live run follows POD5 files unless --mode bam says MinKNOW's BAM files.
            mode = (args.mode or "pod5") if args.live else cfg["mode"]
            print(json.dumps(describe(args.input_dir, mode,
                                      live=args.live, pod5_subdir=pod5_subdir), indent=2))
            return 0
        if args.print_config:
            # Needs no input directory and creates nothing. Its own JSON shape, not the
            # run-result contract. run_dir is where a run would write, from the same two
            # functions a run uses; a failure there is reported, not raised.
            run_dir, run_dir_error = None, None
            current, not_compared = None, []
            try:
                in_dir, out_dir, name = resolve_io(cfg, need_input=False)
                run_dir = str(resolve_run_dir(out_dir, output_name=name,
                                              continue_latest=args.continue_latest))
                # The input a Start would give, so run_dir_state can say whether the run
                # there would refuse it (run_input.py). --live says which driver.
                if in_dir:
                    ref = cfg.get("reference") or None
                    driver = "live" if args.live else "offline"
                    try:
                        current = run_input(driver=driver, mode=cfg["mode"],
                                            input_dir=in_dir, reference=ref)
                    except OSError:
                        not_compared.append("reference (it cannot be read)")
                        current = run_input(driver=driver, mode=cfg["mode"],
                                            input_dir=in_dir, reference=None)
                    if not ref:
                        not_compared.append("reference")
                else:
                    not_compared.append("input folder")
            except ValueError as exc:
                run_dir_error = str(exc)
            print(json.dumps({
                "resolved_config": cfg,
                "config_paths": [str(Path(p).resolve()) for p in (args.config or [])],
                "shipped_config": str(_SHIPPED_CONFIG),
                "run_dir": run_dir,
                "run_dir_error": run_dir_error,
                # Whether a Start would continue an earlier run there, and what it left.
                "run_dir_state": (describe_run_dir(run_dir, current, not_compared)
                                  if run_dir else None),
                "config_changes": config_changes(args.config),
            }, indent=2, default=str))
            return 0
        input_dir, output_dir, output_name = resolve_io(
            cfg, need_input=not args.resume_from_snapshot)
        if args.resume_from_snapshot and not (output_name or args.continue_latest):
            # The directory named is the run to recover; a new run_<stamp> under it
            # would hold no snapshot.
            run_dir = Path(output_dir)
        else:
            run_dir = resolve_run_dir(output_dir, output_name=output_name,
                                      continue_latest=args.continue_latest)
        if args.resume_from_snapshot and not run_dir.is_dir():
            raise ValueError(f"--resume-from-snapshot: {run_dir} is not a directory")
    # OSError: e.g. a missing --config file.
    except (ValueError, OSError) as exc:
        return emit_error(str(exc), exit_code=2)
    existed = run_dir.exists()  # sample BEFORE _setup_logging creates it
    _setup_logging(run_dir, args.verbose)
    announce_run_dir(run_dir, existed=existed)

    # basecall.server.* is two levels deep; load_config's dotted overrides reach only one.
    server_cfg = cfg.setdefault("basecall", {}).setdefault("server", {})
    if args.dorado_device is not None:
        server_cfg["device"] = args.dorado_device
    if args.dorado_port is not None:
        server_cfg["port"] = args.dorado_port
    if args.no_dorado_autostart:
        server_cfg["auto_start"] = False

    if args.resume_from_snapshot:
        try:
            result = resume_from_snapshot(
                run_dir, min_reads=cfg["min_reads"],
                min_reads_per_construct=cfg["min_reads_per_construct"])
        except (FileNotFoundError, RuntimeError) as exc:
            # No snapshot here, or a run that finished normally: exit 2.
            logger.error("%s", exc)
            return emit_error(str(exc), exit_code=2, run_dir=run_dir)
        return emit_result(result, run_dir=run_dir, message="recovered from snapshot")

    # A reference error (at construction or during the run) or a dorado-server error
    # ends the run with its message and exit 1.
    try:
        pipe = OfflinePipeline(input_dir, run_dir, cfg, continues=existed)
        # A run name continues the earlier run of its folder only with the same input
        # and reference; else exit 2 here, before anything is processed (run_input.py).
        check_run_input(run_dir, run_input(driver="offline", mode=cfg["mode"],
                                           input_dir=input_dir,
                                           reference=cfg["reference"]))
        if cfg["mode"] == "pod5":
            # One server for the whole run, so the model loads once; none in bam mode.
            with dorado_server(cfg["basecall"].get("server"),
                               output_dir=run_dir) as server:
                pipe.server_info = server
                result = pipe.run()
        else:
            result = pipe.run()
    except (ReferenceError, DoradoServerError) as exc:
        logger.error("%s", exc)
        return emit_error(str(exc), exit_code=1, run_dir=run_dir)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        # A startup configuration error (no model, a bad model path, a missing or empty
        # input directory): exit 2. Under `on_error: raise`, one of these raised from
        # inside the run lands here too.
        logger.error("%s", exc)
        return emit_error(str(exc), exit_code=2, run_dir=run_dir)

    code = exit_code_for(result.get("status"))
    # A report over no data is not a success: exit 1 when every input failed. Partial
    # failure exits 0; run_accounting records it.
    if code == 0 and pipe._failed_inputs and pipe._n_processed == 0:
        logger.error(
            "Every input failed (%d/%d); the report covers NO data. Exiting non-zero. "
            "See run_accounting.failed_inputs in qc_result.json.",
            len(pipe._failed_inputs), len(pipe._failed_inputs))
        code = 1
    return emit_result(result, run_dir=run_dir, exit_code=code)


if __name__ == "__main__":
    raise SystemExit(main())
