"""The QC report: module snapshots in, an HTML report (and, at the end, a PDF) out.

``CoAReporter`` keeps the latest snapshot per module (``update``), renders an interim
HTML report on request (``report``), and renders the final HTML and PDF and freezes
(``finalize``). Each render first checks read sufficiency: below ``min_reads`` primary
mapped reads it writes a warning file instead, and the caller may re-request with
``force=True``. No acceptance criterion is applied beyond that minimum.
"""

from abc import ABC, abstractmethod
from datetime import datetime
import json
import logging
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from markupsafe import Markup

from .. import PRODUCT_NAME
from ..snapshot_keys import CONSTRUCT_PREFIX, UNASSIGNED, construct_ref

logger = logging.getLogger(__name__)

# Primary mapped reads below which a report is not rendered unless forced.
DEFAULT_MIN_READS = 10000

# The snapshot whose ``mapping.mapped`` is the read-sufficiency count.
DEFAULT_RUN_SUMMARY_MODULE = "run_summary"

# Snapshot name for each role; override with ``module_names=``.
DEFAULT_MODULE_NAMES = {
    "run_summary": DEFAULT_RUN_SUMMARY_MODULE,
    "polya": "polya",
    "consensus": "consensus",
    "error_hotspot": "error_hotspot",
}

# The Jinja2 template and the vendored plotly.js, next to this module.
_HERE = Path(__file__).parent
_TEMPLATE_DIR = _HERE / "templates"
_ASSET_DIR = _HERE / "assets"
# The bundle's file name is pinned in figspec.
from .figspec import PLOTLY_BUNDLE as _PLOTLY_BUNDLE  # noqa: E402

_PLOTLY_PATH = _ASSET_DIR / _PLOTLY_BUNDLE
_TEMPLATE_NAME = "sample_coa.html"

# Terminal reasons accepted by finalize().
_VALID_REASONS = {"run_end", "user_abort"}

#: Fields a person declares about the material, as ``(key, display name)`` in display
#: order. The key is what a config file or the GUI sets.
_DECLARED_FIELDS: tuple[tuple[str, str], ...] = (
    ("manufacturer", "Manufacturer"),
    ("product", "Product"),
    ("batch_id", "Batch / lot ID"),
    ("sample_id", "Sample ID"),
    ("manufacture_date", "Manufacture date"),
    ("operator", "Operator"),
)

#: Title and caption for every figure, keyed by figure id. Registering a figure whose
#: id is missing here raises (see ``add`` in ``_interactive_figures``). A caption is a
#: definition, optionally followed by one sentence on how to read the figure.
_FIGURE_TEXT: dict[str, tuple[str, str]] = {
    "read-length": (
        "Read length (whole run)",
        "The basecalled length of every primary read, whether or not it was assigned "
        "to a construct."),
    "polya": (
        "Poly(A) tail length (whole run)",
        "Over the reads where a tail was called, not over every read. The quantile "
        "markers describe the uncorrected lengths."),
    "read-length-by-construct": (
        "Read length by construct",
        "The same read lengths, split by the construct each read was assigned to. Each "
        "construct opens on its own range, so the axes differ between them."),
    "polya-by-construct": (
        "Poly(A) tail length by construct",
        "Uncorrected and corrected, split by the construct each read was assigned to; a "
        "construct whose correction was withheld shows uncorrected only. The quartiles of "
        "both are on the line under the construct's name."),
    "qscore": (
        "Read quality (whole run)",
        "The basecaller's own mean quality score per read. It measures confidence in "
        "the call, not agreement with the reference."),
    "per-position": (
        "Per-position error",
        "Upper panel: the fraction of covering reads at each reference position that "
        "carry another base or a deletion, stacked, so a bar's height is its mismatch "
        "rate; markers flag the positions at or above the dashed threshold, a "
        "user-adjustable setting (module_params.consensus.mismatch_threshold), not a "
        "specification. Lower panel: insertions per covering read, which take no share "
        "of the reads. The subtitle gives the construct's identity and what the rest of "
        "its reads are."),
    "read-end": (
        "Where reads stop",
        "Each read's alignment end position along the reference. The subtitle gives "
        "the share that reaches the final bin."),
    "read-start": (
        "Where reads begin",
        "Each read's alignment start position along the reference, the 5' counterpart "
        "of the figure above."),
    "shortfall": (
        "Shortfall at both ends",
        "How many reference bases each read leaves uncovered, 5' shortfall against 3'. "
        "Along an anti-diagonal the aligned length is constant and only the end that "
        "lost it changes."),
    "share": (
        "Read share by construct",
        "Each construct's share of the run's reads, with the unassigned bucket as a "
        "peer. No designed pooling ratio is recorded for this run, so no expected "
        "share is drawn."),
    "identity-by-construct": (
        "Per-base identity by construct",
        "Each construct's per-base identity: the share of its base observations at "
        "reference positions that carry the reference base."),
    "median-length": (
        "Median read length by construct",
        "The 50th percentile of each construct's read-length distribution. It says "
        "nothing about spread, which the per-construct histogram shows."),
    "mapq": (
        "Mapping quality (all constructs)",
        "Pooled over every construct. MAPQ 0 is a read the aligner placed equally well "
        "somewhere else, so this describes how separable the references are rather "
        "than how good the reads are."),
    "end-reason": (
        "Read end reason",
        "The reason MinKNOW recorded for each read's acquisition ending, read from the "
        "sequencing summary rather than from the sequence."),
    "voltage": (
        "Voltage over the run",
        "The bias voltage MinKNOW applied over the run, running and offset. Run "
        "context rather than a measurement of the sample."),
    "pore": (
        "Pore occupancy over the run",
        "The share of channels in each state over the run. A run-control signal: it is "
        "a lever only while the run is still going."),
}

#: Figure sections, in document order, as ``(key, heading)``. The HTML report, the PDF
#: and the GUI all group figures by these. "Across constructs" compares constructs with
#: each other; "Sequencing information" comes from MinKNOW's files, not the reads.
FIGURE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("whole-run", "Whole run"),
    ("across-constructs", "Across constructs"),
    ("per-construct", "Per construct"),
    ("sequencing", "Sequencing information"),
)

#: The section of every figure id; a figure missing here raises (see `_section_of`).
_FIGURE_SECTION: dict[str, str] = {
    "read-length": "whole-run",
    "polya": "whole-run",
    "qscore": "whole-run",
    # One distribution pooled over constructs, so it describes the run.
    "mapq": "whole-run",

    "share": "across-constructs",
    "identity-by-construct": "across-constructs",
    "median-length": "across-constructs",

    "read-length-by-construct": "per-construct",
    "polya-by-construct": "per-construct",
    "per-position": "per-construct",
    "read-end": "per-construct",
    "read-start": "per-construct",
    "shortfall": "per-construct",

    "end-reason": "sequencing",
    "voltage": "sequencing",
    "pore": "sequencing",
}


def _smoothing_note(dist: Mapping[str, Any]) -> str:
    """The caption sentence for a distribution drawn with a curve; "" without one."""
    smoothing = dist.get("smoothing") or {}
    nt, width = smoothing.get("bandwidth_nt"), dist.get("bin_width")
    if not nt or not width or dist.get("smoothed_counts") is None:
        return ""
    return (f"Bars are reads per bin ({width:g} nt, uncorrected); the line is a Gaussian "
            f"kernel of fixed "
            f"{nt:g} nt standard deviation through them, drawn for the eye. Every quartile "
            f"is from the reads themselves, not from the line.")


def _section_of(fig_id: str) -> str:
    """The section a figure id belongs to; KeyError for an id with none."""
    return _FIGURE_SECTION[fig_id]


#: The GUI's subject panels, in the order it shows them after its Overview. A panel is the GUI's grouping and a section the
#: report's; both are decided here, so neither surface derives one of its own.
FIGURE_PANELS: tuple[str, ...] = (
    "read-length",
    "polya",
    "quality",
    "constructs",
    "identity",
    "full-length",
    "flow-cell",
)

#: The GUI panel of every figure id; a figure missing here raises (see `_panel_of`).
_FIGURE_PANEL: dict[str, str] = {
    "read-length": "read-length",
    "read-length-by-construct": "read-length",
    "median-length": "read-length",

    "polya": "polya",
    "polya-by-construct": "polya",

    "qscore": "quality",
    "mapq": "quality",

    "share": "constructs",

    "identity-by-construct": "identity",
    "per-position": "identity",

    "read-end": "full-length",
    "read-start": "full-length",
    "shortfall": "full-length",

    "pore": "flow-cell",
    "end-reason": "flow-cell",
    "voltage": "flow-cell",
}


def _panel_of(fig_id: str) -> str:
    """The GUI panel a figure id belongs to; KeyError for an id with none."""
    return _FIGURE_PANEL[fig_id]


#: The order of a construct's figures in the PDF: read length and poly(A), then the
#: error figures, then where reads start and stop. Unlisted figures follow.
_CONSTRUCT_FIGURE_ORDER: tuple[str, ...] = (
    "read-length-by-construct", "polya-by-construct",
    "per-position", "read-end", "read-start", "shortfall",
)

#: Printed figures that take the full page width: they plot one value per reference
#: position, too dense for a column. Everything else goes in a column.
_FULL_WIDTH_FIGURES = frozenset({"per-position"})

#: Size (in) of a printed panel that sits in one column. Drawn at the size it is
#: placed at, so its text keeps its point size; scaling it in CSS would shrink the text.
_COLUMN_PANEL_SIZE = (3.3, 2.6)

#: Screen type is the print scale in px (1 pt = 4/3 px) times this, so 10 pt body text
#: is 15 px on screen.
_SCREEN_TYPE_SCALE = 1.125


#: Most flagged positions a construct lists in the PDF (2026-11-10). A stock
#: basecaller on m1Psi reads can flag more than a hundred; past this many the list stops
#: being read, and every flagged position stays in qc_result.json.
FLAGGED_PRINT_LIMIT = 50


def flagged_positions(consensus: Any) -> Optional[dict[str, Any]]:
    """A construct's flagged positions as the PDF lists them, or None without them.

    Positions only (2026-11-06), 1-based as on the per-position figure's axis, from
    the highest mismatch rate down; a tie goes to the lower position. At most
    ``FLAGGED_PRINT_LIMIT``, the highest-mismatch ones; ``count`` is all of them. The full
    entries, with base counts, are the consensus module's ``mismatch_sites``.
    """
    if not isinstance(consensus, Mapping) or "mismatch_sites" not in consensus:
        return None
    sites = list(consensus.get("mismatch_sites") or [])
    ranked = sorted(sites, key=lambda s: (-float(s["mismatch_rate"]), int(s["pos"])))
    return {
        "count": len(sites),
        "threshold": consensus.get("mismatch_threshold"),
        "min_depth": consensus.get("min_depth_threshold"),
        "positions_measured": consensus.get("positions_above_min_depth"),
        "positions": [int(site["pos"]) + 1 for site in ranked[:FLAGGED_PRINT_LIMIT]],
    }


#: Most decimal places used to show the full-length range's two ends as different;
#: ends that still agree are printed as one number.
_ENVELOPE_MAX_PLACES = 3

# Below-threshold states (see CoAReporter._low_reads_state): an interim render that
# may still reach the minimum, and a final render that did not.
LOW_READS_ACCUMULATING = "accumulating"
LOW_READS_FINAL = "finish_low_reads"
LOW_READS_STATES = (LOW_READS_ACCUMULATING, LOW_READS_FINAL)

# Rewritten on every render, so a browser can stay on one URL; each render's
# ``report_{label}.html`` is kept as well.
LATEST_HTML_NAME = "report_latest.html"

#: The report's plotly figure specs, written beside it on the final render only, so the
#: GUI draws the same charts.
FIGURES_JSON_NAME = "figures.json"

# Interim auto-reload interval (s), matched to the 180 s MinKNOW POD5 interval the
# README sets up: an interim report cannot change between POD5 files.
DEFAULT_AUTO_REFRESH_SECONDS = 180


def _inputs(n: int, one: str = "", many: str = "") -> str:
    """``1 input`` or ``3 inputs``, followed by its verb when one is given."""
    verb = one if n == 1 else many
    return f"{n:,} input{'' if n == 1 else 's'}{' ' + verb if verb else ''}"


def _modification_text(arm: Any) -> str:
    """A construct's modification as the GUI's Modification step says it:
    m1\u03a8-substituted or canonical U. ``sample.arm`` stays the config's key. Anything
    else is shown as given."""
    text = "" if arm is None else str(arm).strip()
    return {"m1psi": "m1\u03a8-substituted", "u": "canonical U"}.get(text.lower(), text)


def _json_for_script(value: Any) -> str:
    """JSON safe to embed, unescaped (``| safe``), inside a ``<script>`` element.

    Autoescaping would break the JSON, and raw JSON could close the element: reference
    names come from the user's FASTA. ``<``, ``>`` and ``&`` become unicode escapes, and
    so do U+2028/9, which are line terminators in JavaScript.
    """
    return (json.dumps(value, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _figures_json(figures: list[dict[str, Any]]) -> str:
    """The figure list as the report embeds it and figures.json carries it: `section` is
    the report's grouping, `panel` the GUI's."""
    return _json_for_script(
        [{"id": f["id"], "title": f["title"], "caption": f["caption"],
          "section": f["section"], "panel": f["panel"], "spec": f["spec"]}
         for f in figures])


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file in the same directory and
    ``os.replace``, so a reloading browser never sees a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _use_this_environments_fontconfig() -> Optional[str]:
    """Point fontconfig at the configuration of the environment this Python runs in.

    The PDF's text is laid out by Pango, which asks fontconfig for fonts, and fontconfig
    reads its configuration from a folder compiled into the library. conda writes that
    folder at install time, so in an environment that was COPIED, as the one inside the
    app is, the library still names the folder it was copied from. On the computer that
    built the app that folder exists; anywhere else fontconfig reports that it cannot load
    its configuration and goes on with a built-in one, without the rules in ``conf.d``
    that choose and substitute fonts. ``FONTCONFIG_PATH`` is fontconfig's own way to be
    told the folder, and it must be set before the library is first used.

    Where the environment was never moved, this names the folder the library already
    reads. A setting the operator made (either variable) is left alone.

    Returns:
        The folder now in ``FONTCONFIG_PATH``, or ``None`` when nothing was changed.
    """
    if os.environ.get("FONTCONFIG_PATH") or os.environ.get("FONTCONFIG_FILE"):
        return None
    folder = Path(sys.prefix) / "etc" / "fonts"
    if not (folder / "fonts.conf").is_file():
        return None
    os.environ["FONTCONFIG_PATH"] = str(folder)
    return str(folder)


def _get(snapshot: Any, *keys: str) -> Any:
    """Safely walk nested dict keys; return None if any level is missing."""
    cur = snapshot
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


class Reporter(ABC):
    """Interface the pipeline uses to produce QC reports.

    Lifecycle:
        1. ``update(module_name, snapshot)``: any number of times, as analysis
           progresses.
        2. ``report(label)``: optional, any number of times, for interim reports while
           the run continues.
        3. ``finalize(reason)``: once, at run end or user abort. Produces the final
           report and freezes the reporter; ``update()`` after this raises.
    """

    @abstractmethod
    def update(self, module_name: str, snapshot: dict[str, Any]) -> None:
        """Accumulate the latest snapshot for one module.

        Args:
            module_name: Stable key identifying the producing module (e.g.
                ``"run_summary"``, ``"polya"``, ``"error_hotspot"``).
            snapshot: The module's ``snapshot()`` output.

        Raises:
            RuntimeError: If called after ``finalize()``.
        """
        ...

    @abstractmethod
    def report(self, label: str, force: bool = False) -> dict[str, Any]:
        """Render an interim report from the current snapshots (non-terminal).

        Args:
            label: Short label used in the output filename (e.g. ``"interim_1"``).
            force: Render even if reads are below the sufficiency threshold.

        Returns:
            A result dict (see ``_render``): either ``status="report"`` with a
            ``path``, or ``status="insufficient"`` with ``can_force=True``.
        """
        ...

    @abstractmethod
    def finalize(self, reason: str, force: bool = False) -> dict[str, Any]:
        """Render the final report and freeze the reporter (terminal).

        Args:
            reason: One of ``"run_end"`` or ``"user_abort"``.
            force: Render even if reads are below the sufficiency threshold.

        Returns:
            The same result-dict shape as ``report()``.
        """
        ...


class CoAReporter(Reporter):
    """Accumulates module snapshots and renders them to an HTML/PDF report.

    Args:
        output_dir: Where reports and warning files are written; created if missing.
        min_reads: Primary mapped reads below which a render needs ``force``.
        run_summary_module: Name of the snapshot whose ``mapping.mapped`` is that count.
        min_reads_per_construct: A construct with fewer assigned reads is only listed,
            with its read count and MAPQ 0 share, instead of getting a section and
            figures. 0 lists none; the shipped config sets 100.
        module_names: Overrides for ``DEFAULT_MODULE_NAMES``.
        auto_refresh_seconds: How often an interim report reloads; None disables it.
        started_monotonic: ``time.monotonic()`` when the run started; each report then
            says how long after that it was made. None (a report resumed from a
            snapshot, with no run behind it) leaves the elapsed time out.
    """

    def __init__(
        self,
        output_dir: Path | str,
        min_reads: int = DEFAULT_MIN_READS,
        run_summary_module: str = DEFAULT_RUN_SUMMARY_MODULE,
        *,
        min_reads_per_construct: int = 0,
        module_names: Optional[dict[str, str]] = None,
        auto_refresh_seconds: Optional[int] = DEFAULT_AUTO_REFRESH_SECONDS,
        started_monotonic: Optional[float] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.auto_refresh_seconds = auto_refresh_seconds
        self.started_monotonic = started_monotonic
        self.min_reads = min_reads
        if min_reads_per_construct < 0:
            raise ValueError(
                f"min_reads_per_construct must be >= 0, got {min_reads_per_construct}")
        self.min_reads_per_construct = min_reads_per_construct
        self.run_summary_module = run_summary_module
        # Role -> snapshot name (see DEFAULT_MODULE_NAMES).
        self.module_names = {**DEFAULT_MODULE_NAMES, **(module_names or {})}
        self.module_names["run_summary"] = run_summary_module

        # Latest snapshot per module name.
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._finalized = False
        # Inputs missing from the numbers, by cause; see note_incomplete_inputs().
        self._failed_inputs: list[str] = []
        self._aborted_inputs: list[str] = []
        self._sampled_inputs: list[str] = []
        # See note_run_context().
        self._run_context: dict[str, Any] = {}
        # Declared and instrument-recorded sample identity; see note_sample().
        self._declared: dict[str, str] = {}
        self._recorded: dict[str, str] = {}
        self._analyzed_read_fraction: Optional[float] = None
        self._total_inputs: Optional[int] = None

    def note_incomplete_inputs(
        self, failed_inputs: Sequence[str], *,
        aborted_inputs: Sequence[str] = (),
        sampled_inputs: Sequence[str] = (),
        analyzed_read_fraction: Optional[float] = None,
        total_inputs: Optional[int] = None,
    ) -> None:
        """Record which inputs are missing from the numbers, and why.

        Set by the driver: an input that contributed nothing leaves no trace in any
        snapshot. Every later render, interim or final, carries the notice. The causes
        are kept apart because they bias the results differently: failures are selected
        by the data, a stop drops the end of the run, and sampling at a fixed stride is
        uniform in time. Each call replaces the three lists.

        Args:
            failed_inputs: Inputs that failed to process.
            aborted_inputs: Inputs left unprocessed when the run was stopped.
            sampled_inputs: Inputs sampled out to keep pace with the sequencer.
            analyzed_read_fraction: Share of the instrument's reads that reached the
                report, when a ``sequencing_summary`` makes it measurable; the net of
                all three causes.
            total_inputs: Number of inputs in the run.
        """
        self._failed_inputs = list(failed_inputs)
        self._aborted_inputs = list(aborted_inputs)
        self._sampled_inputs = list(sampled_inputs)
        if analyzed_read_fraction is not None:
            self._analyzed_read_fraction = float(analyzed_read_fraction)
        if total_inputs is not None:
            self._total_inputs = int(total_inputs)

    def note_sample(self, *, declared: Optional[Mapping[str, Any]] = None,
                    recorded: Optional[Mapping[str, Any]] = None) -> None:
        """Record the sample's identity, keeping declared and recorded fields apart.

        ``declared``: what a person entered (the keys of ``_DECLARED_FIELDS``).
        ``recorded``: what MinKNOW wrote (``minknow.FINAL_SUMMARY_FIELDS``: flow cell,
        instrument, run times, ...). The report shows them as separate groups. Empty
        values are ignored; a later call overwrites a field an earlier one set.
        """
        for store, incoming in ((self._declared, declared), (self._recorded, recorded)):
            for key, value in (incoming or {}).items():
                text = "" if value is None else str(value).strip()
                if text:
                    store[key] = text

    def _signoff_scope(self) -> Optional[dict[str, str]]:
        """The scope statement beside the signoff when the report is not the whole run.

        None for a complete run. Sampling alone gets its own wording, decided by the
        same test as in ``_incomplete_notice``, so the two agree on what the run
        supports.
        """
        failed, aborted = len(self._failed_inputs), len(self._aborted_inputs)
        sampled = len(self._sampled_inputs)
        if not (failed or aborted or sampled):
            return None

        if sampled and not failed and not aborted:
            return {
                "lead": "These results are based on a subsample of the run, not on "
                        "every read.",
                "detail": "Inputs were admitted at a fixed stride so that analysis could "
                          "keep pace with the sequencer; the sample is uniform in time "
                          "and therefore unbiased with respect to run time, but it is "
                          "smaller than the run. The inputs left out are listed in "
                          "run_accounting.sampled_out of qc_result.json.",
            }

        causes = []
        if failed:
            causes.append(f"{failed} failed to process")
        if aborted:
            causes.append(f"{aborted} {'was' if aborted == 1 else 'were'} left when the "
                          f"run was stopped")
        if sampled:
            causes.append(f"{sampled} {'was' if sampled == 1 else 'were'} sampled out to "
                          f"keep pace with the sequencer")
        if len(causes) > 1:
            causes[-1] = "and " + causes[-1]
        return {
            "lead": "These results do not cover the whole run.",
            "detail": f"{_inputs(failed + aborted + sampled, 'is', 'are')} absent: "
                      f"{', '.join(causes)}. What is missing was not lost uniformly, so "
                      f"the numbers here are not an unbiased estimate of the whole run. "
                      f"The notice at the top of this report states each cause and the "
                      f"direction it skews; run_accounting in qc_result.json names the "
                      f"inputs.",
        }

    def _page_identity(self) -> str:
        """The sample identifier in the footer of every PDF page.

        The declared batch and sample IDs; failing those, MinKNOW's sample ID and flow
        cell; failing those, a statement that none was recorded.
        """
        parts = [f"batch {self._declared[k]}" if k == "batch_id"
                 else f"sample {self._declared[k]}"
                 for k in ("batch_id", "sample_id") if self._declared.get(k)]
        if not parts:
            parts = [f"sample {self._recorded[k]}" if k == "sample_id"
                     else f"flow cell {self._recorded[k]}"
                     for k in ("sample_id", "flow_cell_id") if self._recorded.get(k)]
        return " \u00b7 ".join(parts) if parts else "no sample identifier recorded"

    def _sample_rows(self) -> dict[str, list[dict[str, str]]]:
        """Declared and recorded sample fields as display rows. A field not given gets
        no row, not a placeholder."""
        from ..analysis.minknow import FINAL_SUMMARY_FIELDS

        declared = [{"name": name, "value": self._declared[key]}
                    for key, name in _DECLARED_FIELDS if key in self._declared]
        recorded = [{"name": name, "value": self._recorded[key]}
                    for key, name in FINAL_SUMMARY_FIELDS if key in self._recorded]
        return {"declared": declared, "recorded": recorded}

    def note_run_context(self, **fields: Any) -> None:
        """Record how the run was invoked (reference, mode, basecall model, ...).

        Each field becomes a provenance row, in the order given (``basecall_model`` via
        ``_basecall_model``); None and "" are dropped. The command line, commit and
        version are read by ``_run_provenance`` itself.
        """
        self._run_context.update({k: v for k, v in fields.items() if v not in (None, "")})

    @staticmethod
    def _applied_corrected(snap: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """A construct's corrected poly(A) histogram, or None where the correction was
        withheld or no read had a tail."""
        pa = snap.get("polya") or {}
        dist = pa.get("polya_distribution_corrected") or {}
        return dist if (pa.get("correction") or {}).get("applied") and dist.get("n") else None

    @staticmethod
    def _spread_onto(dist: Mapping[str, Any], lo: float, width: float,
                     n_bins: int) -> tuple[list[float], float, float]:
        """``dist``'s counts re-binned onto ``lo + i*width``, plus what lands below and
        above.

        Each source bin's count is split among the bins it overlaps, in proportion to
        the overlap (reads taken as uniform within a bin): totals are exact, shape finer
        than one bin is smoothed, and counts come back fractional.
        """
        out = [0.0] * n_bins
        below = above = 0.0
        top = lo + n_bins * width
        s_lo, s_w = dist["bin_min"], dist["bin_width"]
        for i, count in enumerate(dist["bin_counts"]):
            if not count:
                continue
            a, b = s_lo + i * s_w, s_lo + (i + 1) * s_w
            below += count * max(0.0, min(b, lo) - a) / s_w
            above += count * max(0.0, b - max(a, top)) / s_w
            # `//` floors, so a source edge left of `lo` gives a negative index; clamped.
            for j in range(max(0, int((a - lo) // width)),
                           min(n_bins - 1, int((b - lo) // width)) + 1):
                overlap = min(b, lo + (j + 1) * width) - max(a, lo + j * width)
                if overlap > 0:
                    out[j] += count * overlap / s_w
        return out, below, above

    @classmethod
    def _pooled_corrected(cls, tracks, onto: Mapping[str, Any],
                          ) -> Optional[tuple[dict[str, Any], int]]:
        """Every construct's corrected tail histogram, re-binned onto ``onto``'s bins
        and summed, with the number of constructs pooled; None when no correction
        applied.

        Each construct's correction stretches its bins differently, so each is spread
        onto the uncorrected bins (``_spread_onto``) before summing. Quantiles are left
        None, as the re-binned counts cannot support them. The curve is redrawn from the
        pooled counts with ``onto``'s kernel. Raises ValueError when ``onto`` has no bin
        geometry.
        """
        parts = [d for d in (cls._applied_corrected(snap) for _name, snap in tracks) if d]
        if not parts:
            return None
        lo, width, n_bins = onto.get("bin_min"), onto.get("bin_width"), onto.get("n_bins")
        if lo is None or not width or not n_bins:
            raise ValueError("the uncorrected histogram carries no bin geometry to pool onto")
        counts = [0.0] * n_bins
        under = over = 0.0
        for d in parts:
            spread, below, above = cls._spread_onto(d, lo, width, n_bins)
            counts = [x + y for x, y in zip(counts, spread)]
            under += (d.get("underflow") or 0) + below
            over += (d.get("overflow") or 0) + above
        pooled = {"bin_min": lo, "bin_max": onto.get("bin_max"), "bin_width": width,
                  "n_bins": n_bins, "n": sum(d["n"] for d in parts), "bin_counts": counts,
                  "underflow": under, "overflow": over, "Q1": None, "median": None,
                  "Q3": None}
        # The uncorrected series' kernel, on the same grid, so the two curves are smoothed
        # alike. A distribution with no curve (an older snapshot) gets none here either.
        smoothing = onto.get("smoothing")
        if smoothing:
            from ..analysis.streaming_interface import gaussian_smooth

            pooled["smoothing"] = dict(smoothing)
            pooled["smoothed_counts"] = gaussian_smooth(counts, width,
                                                        smoothing["bandwidth_nt"])
        return pooled, len(parts)

    def _basecall_model(self) -> Optional[str]:
        """The basecall model's canonical name (aliases resolved), or None.

        From the reads' RG tag, as the run-level poly(A) module resolved it; for reads
        with no RG, from the folder name of the configured ``basecall_model``.
        """
        from ..analysis.polya_calibration import resolve_model

        pa = self._snapshots.get(self.module_names["polya"]) or {}
        observed = (pa.get("correction") or {}).get("run_model_resolved")
        if observed:
            return str(observed)
        declared = self._run_context.get("basecall_model")
        if declared:
            return resolve_model(Path(str(declared)).name)[0]
        return None

    @staticmethod
    def _screen_figures(figures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """The screen's figure keys: every spec as one JSON blob for the page's script
        (also written as figures.json, so `section` and `panel` travel with each spec),
        their headings, and plotly itself, inlined so the HTML works as a single file."""
        return {
            "interactive_json": _figures_json(figures),
            "interactive": [{"id": f["id"], "title": f["title"],
                             "section": f["section"],
                             "caption": f["caption"]} for f in figures],
            "plotly_js": (_PLOTLY_PATH.read_text(encoding="utf-8") if figures else ""),
        }

    def _run_box(self) -> list[dict[str, str]]:
        """The first page's run lines: the reference file, the input and the basecall
        model. Provenance, at the end, keeps the full record (paths, command, commit)."""
        rows: list[dict[str, str]] = []
        reference = self._run_context.get("reference")
        if reference:
            rows.append({"name": "Reference", "value": Path(str(reference)).name})
        inputs = self._run_context.get("inputs")
        if inputs:
            mode = self._run_context.get("mode")
            rows.append({"name": "Input", "value": f"{inputs} ({str(mode).upper()})"
                         if mode else str(inputs)})
        model = self._basecall_model()
        if model:
            rows.append({"name": "Basecall model", "value": model})
        return rows

    @staticmethod
    def _glance(constructs: Sequence[Mapping[str, Any]],
                flagged: Mapping[str, Optional[Mapping[str, Any]]]
                ) -> list[dict[str, str]]:
        """One line per reported construct for the first page, its values copied from
        that construct's own table, so the two cannot disagree."""
        lines: list[dict[str, str]] = []
        for c in constructs:
            own = {row["name"]: row["value"] for row in c["rows"]}
            f = flagged.get(c["name"])
            lines.append({
                "name": c["name"],
                "reads": own["Assigned reads"],
                "identity": own["Per-base identity"],
                "polya_called": own["Poly(A) tail length, uncorrected (median)"],
                "polya_corrected": own["Poly(A) tail length, corrected (median)"],
                "flagged": "" if f is None else f"{f['count']:,}",
            })
        return lines

    def _run_provenance(self) -> list[dict[str, Any]]:
        """The provenance rows the report prints: commit, version, run context, basecall
        model and command line. A modified working tree is stated beside the commit.
        """
        from .. import provenance

        rows: list[dict[str, Any]] = []

        def add(name: str, value: Any) -> None:
            if value not in (None, "", [], {}):
                rows.append({"name": name, "value": str(value)})

        commit = provenance.git_commit(short=True)
        dirty = provenance.git_is_dirty()
        if commit:
            add("Source commit",
                f"{commit} (working tree modified: the commit does not describe the "
                f"code that ran)" if dirty else commit)
        add(f"{PRODUCT_NAME} version", provenance._SOFTWARE_VERSION)
        # The resolved model replaces the basecall_model field, just after the Mode row.
        model = self._basecall_model()
        for name, value in self._run_context.items():
            if name == "basecall_model":
                continue
            add(name.replace("_", " ").capitalize(), value)
            if name == "mode":
                add("Basecall model", model)
                model = None
        add("Basecall model", model)     # when there was no Mode row
        add("Command", shlex.join(sys.argv))
        return rows

    def _incomplete_notice(self) -> Optional[str]:
        """The banner text for a report missing inputs, or None when none are missing.

        Leads with the share of reads analysed when it is known, then states each cause
        (failed, stopped, sampled out) with the bias it implies. With the shipped
        ``on_error: skip`` a run that lost inputs still exits 0, so this is where the
        reader learns of it.
        """
        aborted = self._aborted_inputs
        sampled = self._sampled_inputs
        if not self._failed_inputs and not aborted and not sampled:
            return None

        def _show(names: Sequence[str]) -> str:
            """The first five names and a count of the rest. Used for failures only;
            every name is in ``run_accounting``."""
            shown = ", ".join(names[:5])
            return shown + (f", … (+{len(names) - 5} more)" if len(names) > 5 else "")

        n = len(self._failed_inputs) + len(aborted) + len(sampled)
        scope = (f"{self._total_inputs - n:,} of {_inputs(self._total_inputs)}"
                 if self._total_inputs else f"all inputs except {n}")
        # Sampling alone is not a fault: the banner then leads with SAMPLED, not
        # INCOMPLETE.
        only_sampled = bool(sampled) and not self._failed_inputs and not aborted

        # Lead with the read share: POD5 files differ in read count, so a file count
        # alone misstates how much was analysed. The share nets every cause below.
        share = self._analyzed_read_fraction
        if share is not None:
            lead = (f"these numbers come from {share * 100:.1f}% of the reads the "
                    f"instrument recorded, and from {scope}.")
            if not only_sampled:
                lead += (f" The other {_inputs(n, 'is', 'are')} not represented in any "
                         f"number below.")
            # The one place a word is set in capitals: the label that opens a warning
            # (here, and OUTSIDE THE FITTED RANGE in `_ruler`). Inside a sentence nothing is.
            parts = [("SAMPLED: " if only_sampled else "INCOMPLETE: ") + lead]
        # Without a sequencing_summary the share is unknown; lead with the file count.
        elif only_sampled:
            parts = [f"SAMPLED: this report is based on part of the run, not all of it. "
                     f"It covers {scope}."]
        else:
            parts = [f"INCOMPLETE: {_inputs(n, 'is', 'are')} not represented in any number "
                     f"below. "
                     f"This report covers {scope}."]
        if self._failed_inputs:
            parts.append(
                f"Failed to process ({len(self._failed_inputs)}): "
                f"{_show(self._failed_inputs)}. Which inputs fail is not random (an "
                f"out-of-memory failure takes the largest POD5s; a basecall failure "
                f"clusters on abnormal signal), so the missing reads are correlated with "
                f"the properties being measured.")
        if aborted:
            # Not "failed": nothing was attempted on these inputs.
            parts.append(
                f"Not processed because the run was stopped ({len(aborted)}). "
                f"These are a time suffix of the run, not a data-selected "
                f"subset: they are the newest inputs, so the reads missing here are the "
                f"later, more degraded pores.")
        if sampled:
            # The sample is always uniform in time; the rates are unbiased only when
            # nothing else is missing.
            if only_sampled:
                consequence = (
                    "It is not the healthy early run with the degraded end removed, and "
                    "every rate below is an unbiased estimate of the whole run. ")
            else:
                consequence = (
                    "It is not the healthy early run with the degraded end removed, so "
                    "the sampling costs no accuracy of its own. The other inputs above "
                    "were not lost uniformly, so the rates below are still not unbiased "
                    "estimates of the whole run. ")
            parts.append(
                f"Sampled out to keep up with the sequencer ({len(sampled)}). "
                f"These were chosen by arrival index at a fixed stride, so the sample is "
                f"uniform in time. " + consequence +
                f"What a subsample costs is precision, not accuracy: rare events and tail "
                f"quantiles are supported by proportionally fewer reads, so treat a count "
                f"of something infrequent as a floor.")
        if only_sampled:
            parts.append(
                "The sampling is deliberate and is recorded in run_accounting.sampled_out. "
                "If this run needs a census rather than a sample, re-run it with sampling "
                "disabled.")
        else:
            parts.append(
                "Treat these metrics as describing the data that reached the report, not "
                "the run. See run_accounting before using it for a QC or release "
                "decision.")
        return " ".join(parts)

    def update(self, module_name: str, snapshot: dict[str, Any]) -> None:
        if self._finalized:
            raise RuntimeError(
                "CoAReporter: update() called after finalize()"
            )
        self._snapshots[module_name] = snapshot

    def report(self, label: str, force: bool = False) -> dict[str, Any]:
        # Interim: never freezes.
        return self._render(label, force=force, terminal=False)

    def finalize(self, reason: str, force: bool = False) -> dict[str, Any]:
        if reason not in _VALID_REASONS:
            raise ValueError(
                f"reason must be one of {_VALID_REASONS}, got '{reason}'"
            )
        result = self._render(reason, force=force, terminal=True)
        # Freeze only once a report was rendered: after a warning the driver may still
        # re-call finalize(reason, force=True).
        if result.get("status") == "report":
            self._finalized = True
        return result

    # --- internals ---------------------------------------------------------

    def _mapped_reads(self) -> int:
        """The sufficiency count: ``mapping.mapped`` (primary mapped reads), not total
        reads.

        From the run-summary snapshot, else the first snapshot that has it, else 0.
        """
        snap = self._snapshots.get(self.run_summary_module)
        mapped = self._extract_mapped(snap)
        if mapped is not None:
            return mapped

        for snap in self._snapshots.values():
            mapped = self._extract_mapped(snap)
            if mapped is not None:
                return mapped
        return 0

    @staticmethod
    def _extract_mapped(snapshot: Optional[dict[str, Any]]) -> Optional[int]:
        """Return ``mapping.mapped`` (primary mapped reads) from a snapshot, or None."""
        if not snapshot:
            return None
        mapping = snapshot.get("mapping")
        if isinstance(mapping, dict) and "mapped" in mapping:
            return int(mapping["mapped"])
        return None

    def _render(
        self, label: str, force: bool = False, terminal: bool = False
    ) -> dict[str, Any]:
        """Gate on read sufficiency, then write a warning or render.

        Below ``min_reads`` primary mapped reads and not forced: a warning file only,
        with ``status="insufficient", can_force=True``. Otherwise the report, with
        ``status="report"``. ``terminal`` is True for ``finalize()``, False for
        ``report()``.
        """
        mapped = self._mapped_reads()
        if mapped < self.min_reads and not force:
            return self._emit_warning(label, mapped, terminal=terminal)
        return self._emit_report(
            label, mapped, low_reads=mapped < self.min_reads, terminal=terminal)

    def _low_reads_state(self, low_reads: bool, terminal: bool) -> Optional[str]:
        """Which kind of below-threshold render this is, or None if reads suffice.

        ``accumulating``: an interim render; the run may still reach the minimum.
        ``finish_low_reads``: the final render; it never did, and waiting will not help.
        """
        if not low_reads:
            return None
        return LOW_READS_FINAL if terminal else LOW_READS_ACCUMULATING

    def _emit_warning(
        self, label: str, mapped: int, terminal: bool = False
    ) -> dict[str, Any]:
        """Write ``WARNING_{label}.txt`` instead of a report, and return the result."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        state = self._low_reads_state(True, terminal)
        if terminal:
            outcome = (
                f"The run is OVER and finished below the minimum. More reads are not "
                f"coming. To render a report anyway, set a lower min_reads "
                f"(--min-reads, or `min_reads:` in the config)."
            )
        else:
            outcome = (
                f"The run is still in progress; an interim report follows once the "
                f"minimum is reached."
            )
        message = (
            f"Insufficient mapped reads for report '{label}': {mapped:,} < "
            f"min_reads={self.min_reads:,}. Report not rendered. {outcome}"
        )
        logger.warning(message)
        warning_path = self.output_dir / f"WARNING_{label}.txt"
        warning_path.write_text(message + "\n", encoding="utf-8")
        return {
            "status": "insufficient",
            "can_force": True,
            "label": label,
            "mapped_reads": mapped,
            "min_reads": self.min_reads,
            "low_reads_state": state,
            "message": message,
            "path": str(warning_path),
        }

    def _emit_report(
        self, label: str, mapped: int, low_reads: bool = False, terminal: bool = False
    ) -> dict[str, Any]:
        """Render the report and return the result with the paths written.

        Always the HTML (``report_{label}.html`` and ``report_latest.html``); on a
        terminal render also the PDF and ``figures.json``. ``mapped`` is the sufficiency
        count.
        """
        # Lazy import: keeps the Reporter contract importable without the
        # render stack (jinja2/weasyprint/matplotlib) being installed.
        import base64

        from jinja2 import Environment, FileSystemLoader

        # Before WeasyPrint: importing it is what loads fontconfig.
        _use_this_environments_fontconfig()
        from weasyprint import HTML

        self.output_dir.mkdir(parents=True, exist_ok=True)

        metrics = self._build_metrics(mapped)
        constructs = self._build_constructs()

        # A forced below-threshold report carries a notice saying so.
        state = self._low_reads_state(low_reads, terminal)
        insufficient_notice = None
        if state == LOW_READS_FINAL:
            insufficient_notice = (
                # Names no cause: from snapshots alone a poor run cannot be told from a
                # wrong reference, a failed basecall or inputs that never arrived.
                f"This run finished with {mapped:,} mapped reads, below the "
                f"{self.min_reads:,} mapped-read minimum. This is a final result, not a "
                f"preliminary one: no further reads are coming, so these metrics will "
                f"not improve. The run did not produce enough data to support a QC or "
                f"release decision. Before concluding this was a sequencing problem, "
                f"check run_accounting in qc_result.json for inputs that failed to "
                f"process."
            )
        elif state == LOW_READS_ACCUMULATING:
            insufficient_notice = (
                f"This analysis does not yet contain enough information to diagnose or QC "
                f"this run: {mapped:,} mapped reads so far, below the {self.min_reads:,} "
                f"mapped-read minimum. This is an interim view of a run still in "
                f"progress. The metrics below are preliminary, will change as more "
                f"reads arrive, and must not be used for release decisions."
            )

        # Built on every render, so figures.json carries them to the GUI during the run
        # too (2026-11-09; ~0.1 s and 1.8-3.6 MB per render on 15-25 constructs,
        # measured). Only the final report embeds them: an interim HTML would inline
        # plotly.js into every interim file.
        interactive = self._interactive_figures()
        embedded = interactive if terminal else []
        printed = self._printed_figures(base64, embedded)
        # Each construct's flagged positions: under its per-position figure, and counted
        # on the first page.
        flagged = {ref: flagged_positions(snap.get("consensus"))
                   for ref, snap in self._reported_constructs()}

        now = datetime.now().astimezone()
        context = {
            # Final or interim in words; the label itself is only in the file name.
            "report_title": f"{PRODUCT_NAME} QC report ({'final' if terminal else 'interim'})",
            # The template reads the product name from here and never spells it.
            "product_name": PRODUCT_NAME,
            "page_identity": self._page_identity(),
            # The user's clock with its offset, and the time since the run started
            # (2026-11-10). One instant for the subtitle and the Test date rows.
            "test_date": now.date().isoformat(),
            "generated_at": self._clock_text(now),
            "elapsed": (None if self.started_monotonic is None else
                        self._elapsed_text(time.monotonic() - self.started_monotonic)),
            "metrics": metrics,
            "insufficient_notice": insufficient_notice,
            # Independent of insufficient_notice: a run can be short of reads, missing
            # inputs, or both.
            "incomplete_notice": self._incomplete_notice(),
            "signoff_scope": self._signoff_scope(),
            "run_provenance": self._run_provenance(),
            "sample": self._sample_rows(),
            # The PDF's first page (2026-11-10): the run beside the sample, and one
            # line per construct.
            "run_box": self._run_box(),
            "glance": self._glance(constructs, flagged),
            # The rule the flags were counted by, for the note under that table: the
            # run's own, not a number written into the template.
            "flag_rule": next((f for f in flagged.values() if f), None),
            "design_tokens": self._design_tokens(),
            "signatory_title": "QC reviewer",
            # Constructs with a section of their own; the rest are listed_constructs.
            "constructs": constructs,
            "listed_constructs": self._listed_constructs(),
            "n_constructs_received": len(self._construct_snapshots()),
            "min_reads_per_construct": self.min_reads_per_construct,
            # The figures the screen draws: none in a numbered interim (below).
            **self._screen_figures(embedded),
            "figure_sections": FIGURE_SECTIONS,
            # A numbered interim says where its figures are: in the live page.
            "figures_in_latest": bool(interactive) and not terminal,
            # The same specs as images for the PDF, one per selector option.
            "printed": printed,
            # The signoff then states that the figures are part of the report.
            "figures_are_signed": bool(embedded),
            # Each construct's flagged positions print under its per-position figure.
            "print_sections": self._print_sections(printed, flagged=flagged),
            # For the per-construct note on reads that matched no construct.
            "unassigned": self._snapshots.get(UNASSIGNED),
            # Interims reload themselves; the final report must not.
            "auto_refresh_seconds": None if terminal else self.auto_refresh_seconds,
        }

        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
        template = env.get_template(_TEMPLATE_NAME)
        # One template and one context, rendered once per medium.
        html_str = template.render(**context, for_print=False)

        html_path = self.output_dir / f"report_{label}.html"
        _atomic_write_text(html_path, html_str)
        # The live page, report_latest.html, is what the GUI's "Interim report" opens and
        # what a browser stays on, so it draws the figures during the run too
        # (2026-11-10). The numbered copies, one per render, stay without: with them each
        # is 1.8-4 MB, and a long run writes hundreds.
        latest_str = html_str
        if not terminal and interactive:
            latest_str = template.render(
                **{**context, **self._screen_figures(interactive),
                   "figures_in_latest": False}, for_print=False)
        _atomic_write_text(self.output_dir / LATEST_HTML_NAME, latest_str)

        # The specs, for the GUI: on every render, interim or final.
        _atomic_write_text(self.output_dir / FIGURES_JSON_NAME, _figures_json(interactive))

        # The PDF is rendered on the terminal report only.
        pdf_path: Optional[Path] = None
        if terminal:
            pdf_path = self.output_dir / f"report_{label}.pdf"
            print_str = template.render(**context, for_print=True)
            HTML(string=print_str, base_url=str(_TEMPLATE_DIR)).write_pdf(pdf_path)

        if state == LOW_READS_FINAL:
            logger.warning(
                "FINAL report '%s' rendered below the minimum (%d < %d): the run ended "
                "short of the read requirement; this is not a preliminary result",
                label, mapped, self.min_reads,
            )
        elif state == LOW_READS_ACCUMULATING:
            logger.warning(
                "Interim report '%s' rendered with low mapped reads so far (%d < %d)",
                label, mapped, self.min_reads,
            )
        if self._failed_inputs:
            logger.warning(
                "Report '%s' is INCOMPLETE: %d input(s) failed and are absent from every "
                "number in it (%s)", label, len(self._failed_inputs),
                ", ".join(self._failed_inputs[:5]))
        if self._aborted_inputs:
            logger.warning(
                "Report '%s' is INCOMPLETE: %d input(s) were not processed because the run "
                "was stopped (%s)", label, len(self._aborted_inputs),
                ", ".join(self._aborted_inputs[:5]))
        logger.info("Rendered report '%s' -> %s (pdf: %s)", label, html_path,
                    pdf_path if pdf_path else "skipped (interim)")
        return {
            "status": "report",
            "label": label,
            "mapped_reads": mapped,
            # Failed inputs only; stopped or sampled-out inputs do not set it.
            "incomplete": bool(self._failed_inputs),
            "n_failed_inputs": len(self._failed_inputs),
            "low_reads": low_reads,
            "low_reads_state": state,
            # The main file written: the PDF if terminal, else the HTML.
            "path": str(pdf_path) if pdf_path else str(html_path),
            "pdf_path": str(pdf_path) if pdf_path else None,
            "html_path": str(html_path),
            "latest_html_path": str(self.output_dir / LATEST_HTML_NAME),
            # Every render writes figures.json, interims included.
            "figures_path": str(self.output_dir / FIGURES_JSON_NAME),
        }

    # --- per-construct sections ---------------------------------------------

    def _construct_snapshots(self) -> list[tuple[str, dict[str, Any]]]:
        """``(ref_name, snapshot)`` for every tracked construct, most reads first, then
        by name, so the order is stable across renders."""
        found = [(construct_ref(k), v) for k, v in self._snapshots.items()
                 if k.startswith(CONSTRUCT_PREFIX) and isinstance(v, dict)]
        return sorted(found, key=lambda kv: (-(kv[1].get("n_reads") or 0), kv[0]))

    def _reported_constructs(self) -> list[tuple[str, dict[str, Any]]]:
        """Constructs with at least ``min_reads_per_construct`` reads: these get a
        section and per-construct figures.

        A display rule only: every construct's reads still count in the whole-run
        results, so those match qc_result.json.
        """
        return [(ref, snap) for ref, snap in self._construct_snapshots()
                if (snap.get("n_reads") or 0) >= self.min_reads_per_construct]

    def _listed_constructs(self) -> list[dict[str, Any]]:
        """Constructs below ``min_reads_per_construct``, one line each: name, reads, and
        MAPQ 0 share (reads the aligner could not tell from another target)."""
        return [{"name": ref, "n_reads": snap.get("n_reads") or 0,
                 "mapq0": self._pct((snap.get("mapq") or {}).get("mapq0_fraction"))}
                for ref, snap in self._construct_snapshots()
                if (snap.get("n_reads") or 0) < self.min_reads_per_construct]

    def _build_constructs(self) -> list[dict[str, Any]]:
        """One section per reported construct: its metric table and a depth notice.

        Its figures are built with the others, in ``_interactive_figures``.
        """
        sections: list[dict[str, Any]] = []
        for ref, snap in self._reported_constructs():
            consensus = snap.get("consensus") or {}
            pileup = snap.get("pileup") or {}
            polya = snap.get("polya") or {}
            mapq = snap.get("mapq") or {}
            n_reads = snap.get("n_reads") or 0
            sections.append({
                "name": ref,
                "n_reads": n_reads,
                "notice": self._construct_notice(ref, n_reads, consensus),
                "rows": [
                    self._row("Assigned reads", "Primary reads mapped to this construct",
                              f"{n_reads:,}"),
                    self._row("Mean depth", "Pileup depth averaged over the reference",
                              self._num(pileup.get("mean_depth"), "×", grouped=True)),
                    self._row("Reference coverage",
                              "Positions with depth > 0 / reference length",
                              self._pct(pileup.get("coverage_fraction"))),
                    self._row("Per-base identity",
                              self._construct_identity_method(consensus),
                              self._pct(_get(consensus, "consensus_identity_pct"),
                                        already_pct=True)),
                    # Uncorrected and corrected, in two rows. When the correction was
                    # withheld, the corrected row's method says why. "Uncorrected" was
                    # "as called" until 2026-11-13: one word for it everywhere.
                    self._row("Poly(A) tail length, uncorrected (median)",
                              self._polya_called(polya),
                              self._nt(_get(polya, "polya_distribution", "median"))),
                    self._row("Poly(A) tail length, corrected (median)",
                              self._ruler(polya),
                              self._nt(_get(polya, "polya_distribution_corrected", "median"))
                              if _get(polya, "correction", "applied") else "Not measured"),
                    *self._short_tail_rows(polya),
                    self._construct_full_length_row(snap, n_reads),
                    self._row("Assignment confidence (MAPQ 0)",
                              "Fraction of this construct's reads the aligner could not "
                              "distinguish from another target",
                              self._pct(mapq.get("mapq0_fraction"))),
                ],
            })
        return sections

    def _printed_figures(self, base64, interactive: Sequence[dict[str, Any]]
                         ) -> list[dict[str, Any]]:
        """The figure specs drawn as SVG panels for the PDF, one per selector option.

        Drawn by ``figrender`` in grayscale, at the size each is placed at. ``base64``
        is the stdlib module, passed in by the caller. A figure that fails to draw is
        skipped with a warning, so the report still renders.
        """
        from . import figrender

        printed: list[dict[str, Any]] = []
        for fig in interactive:
            full_width = fig["id"] in _FULL_WIDTH_FIGURES
            try:
                panels = figrender.render_spec(
                    fig["spec"], theme=figrender.GRAYSCALE,
                    figsize=(figrender.PANEL_SIZE if full_width
                             else _COLUMN_PANEL_SIZE))
            except Exception as exc:  # noqa: BLE001 - one bad figure must not lose the rest
                logger.warning("printed figure %r skipped: %s", fig["id"], exc)
                continue
            printed.append({
                "id": fig["id"], "title": fig["title"], "caption": fig["caption"],
                "full_width": full_width,
                "panels": [{
                    "label": p.label, "name": p.name, "detail": p.detail,
                    "url": ("data:image/svg+xml;base64,"
                            + base64.b64encode(p.svg).decode("ascii")),
                } for p in panels],
            })
        return printed

    @staticmethod
    def _design_tokens() -> str:
        """``figspec``'s design tokens as CSS custom properties for the template.

        Type and spacing scales, fonts, inks, rules, surfaces and status colours, so the
        stylesheet and the figures share one source. On screen the type scale is set in
        px times ``_SCREEN_TYPE_SCALE``, with the screen font.
        """
        from . import figspec

        print_steps = "\n".join(
            f"      --type-{name}: {pt:g}pt;"
            for name, pt in figspec.TYPE_SCALE_PT.items())
        screen_steps = "\n".join(
            f"        --type-{name}: {pt * 4 / 3 * _SCREEN_TYPE_SCALE:g}px;"
            for name, pt in figspec.TYPE_SCALE_PT.items())
        # Markup, because autoescaping would break the quotes in the font stacks. Every
        # value is a figspec constant, never user input.
        return Markup(
            "    :root {\n"
            f"{print_steps}\n"
            f"      --font-sans: {figspec.FONT_FAMILY_PRINT};\n"
            f"      --font-mono: {figspec.FONT_MONO};\n"
            f"      --ink-primary: {figspec.INK_PRIMARY};\n"
            f"      --ink-secondary: {figspec.INK_SECONDARY};\n"
            f"      --ink-muted: {figspec.INK_MUTED};\n"
            f"      --rule: {figspec.GRIDLINE};\n"
            f"      --rule-strong: {figspec.AXIS_LINE};\n"
            f"      --surface: {figspec.SURFACE};\n"
            f"      --surface-panel: {figspec.SURFACE_PANEL};\n"
            f"      --surface-sunken: {figspec.SURFACE_SUNKEN};\n"
            + "".join(f"      --space-{k}: {v}px;\n" for k, v in figspec.SPACE_PX.items())
            + f"      --status-warning: {figspec.STATUS_WARNING};\n"
            f"      --status-warning-ink: {figspec.STATUS_WARNING_INK};\n"
            f"      --status-warning-surface: {figspec.STATUS_WARNING_SURFACE};\n"
            f"      --status-serious: {figspec.STATUS_SERIOUS};\n"
            f"      --status-serious-ink: {figspec.STATUS_SERIOUS_INK};\n"
            f"      --status-serious-surface: {figspec.STATUS_SERIOUS_SURFACE};\n"
            f"      --status-good: {figspec.STATUS_GOOD};\n"
            "    }\n"
            "    @media screen {\n"
            "      :root {\n"
            f"{screen_steps}\n"
            f"        --font-sans: {figspec.FONT_FAMILY};\n"
            "      }\n"
            "    }"
        )

    @staticmethod
    def _width_bands(figures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Consecutive figures of the same width, grouped into alternating bands.

        A full-width figure sits between column grids rather than spanning one: in
        WeasyPrint a spanning grid item (``grid-column: 1 / -1``) collapses the grid's
        column sizing.
        """
        bands: list[dict[str, Any]] = []
        for fig in figures:
            if not bands or bands[-1]["full_width"] != fig["full_width"]:
                bands.append({"full_width": fig["full_width"], "figures": []})
            bands[-1]["figures"].append(fig)
        return bands

    @staticmethod
    def _print_sections(printed: Sequence[dict[str, Any]],
                        flagged: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """Lay out the printed figures: by section, and the per-construct ones by
        construct rather than by figure type.

        Returns the sections printed before the per-construct block (``before``) and
        after it (``after``) as width bands, one block per construct with all its panels
        (``constructs``), and the per-construct figures' titles and captions, printed
        once (``construct_key``). A construct missing a figure simply has fewer panels.
        ``flagged`` maps a construct to its ``flagged_positions``, which print under its
        per-position figure, or at the end of its block without one.
        """
        by_section: dict[str, list[dict[str, Any]]] = {k: [] for k, _ in FIGURE_SECTIONS}
        for fig in printed:
            by_section[_section_of(fig["id"])].append(fig)

        def flat(figs, *, caption: bool = True) -> list[dict[str, Any]]:
            """Run-level figures: one panel each, so the section is a flat band list."""
            return [{"id": f["id"], "title": f["title"],
                     "caption": f["caption"] if caption else "",
                     "full_width": f["full_width"], "panel": f["panels"][0]}
                    for f in figs if f["panels"]]

        # Sections listed before "per-construct" print before the construct blocks, the
        # rest (Sequencing information) after them.
        order = [k for k, _ in FIGURE_SECTIONS]
        pivot = order.index("per-construct")
        before, after = [], []
        for idx, (key, heading) in enumerate(FIGURE_SECTIONS):
            if key == "per-construct":
                continue
            figures = flat(by_section[key])
            if figures:
                (before if idx < pivot else after).append(
                    {"key": key, "name": heading,
                     "bands": CoAReporter._width_bands(figures)})

        per_construct = by_section["per-construct"]
        # _CONSTRUCT_FIGURE_ORDER first; a figure not listed there is appended, not
        # dropped.
        known = [fid for fid in _CONSTRUCT_FIGURE_ORDER
                 if fid in {f["id"] for f in per_construct}]
        order = known + [f["id"] for f in per_construct if f["id"] not in known]
        by_id = {f["id"]: f for f in per_construct}

        names: list[str] = []
        for fid in order:
            for panel in by_id[fid]["panels"]:
                if panel["label"] not in names:
                    names.append(panel["label"])

        # Captions are printed once, before the construct blocks, not under every panel.
        construct_key = [{"title": by_id[fid]["title"], "caption": by_id[fid]["caption"]}
                         for fid in order if by_id[fid]["caption"]]

        sections = []
        for name in names:
            figures = []
            for fid in order:
                fig = by_id[fid]
                for panel in fig["panels"]:
                    if panel["label"] == name:
                        figures.append({"id": fid, "title": fig["title"], "caption": "",
                                        "full_width": fig["full_width"], "panel": panel})
            if figures:
                sections.append({
                    "name": name, "figures": figures,
                    "bands": CoAReporter._width_bands(figures),
                    "flagged": (flagged or {}).get(name),
                    "has_per_position": any(f["id"] == "per-position" for f in figures)})

        return {"before": before, "after": after, "construct_key": construct_key,
                "constructs": sections,
                "per_construct_heading": dict(FIGURE_SECTIONS)["per-construct"]}

    def _interactive_figures(self) -> list[dict[str, Any]]:
        """Every figure: its id, title, caption, section, GUI panel and plotly spec.

        The HTML draws the specs with plotly, the PDF as images (``_printed_figures``),
        and the GUI reads them from figures.json; the caller builds them on the terminal
        render only. A figure whose data is missing or malformed is skipped with a
        warning, so the report still renders.
        """
        from . import figspec

        out: list[dict[str, Any]] = []

        def add(fig_id: str, build, extra: str = "") -> None:
            """Register one figure, with its title and caption from ``_FIGURE_TEXT``.

            A caption names the quantity and the population it is over. It states no
            acceptance criterion (the report sets none) and no screen instruction,
            since it is printed too. ``extra`` is a sentence only this run can supply,
            such as a count.
            """
            # Unguarded, and before the build: a figure with no copy is a programming
            # error, not a bad run.
            title, caption = _FIGURE_TEXT[fig_id]
            try:
                spec = build()
            except Exception as exc:  # noqa: BLE001 - one bad figure must not lose the rest
                logger.warning("interactive figure %r skipped: %s", fig_id, exc)
                return
            if spec is not None:
                # The panel's result colour (figspec.RESULT_COLOURS) is tagged on the
                # traces drawn in it, so print keeps slot 1's grey for every primary
                # series (figrender) while the screen shows the colour code.
                colour = figspec.RESULT_COLOURS.get(_panel_of(fig_id))
                if colour:
                    figspec.tag_result(spec, colour)
                out.append({"id": fig_id, "title": title,
                            "caption": f"{caption} {extra}" if extra else caption,
                            "section": _section_of(fig_id), "panel": _panel_of(fig_id),
                            "spec": spec})

        rs = self._snapshots.get(self.module_names["run_summary"]) or {}
        pa = self._snapshots.get(self.module_names["polya"]) or {}
        tracks = self._construct_snapshots()
        # A figure's primary series wears its panel's colour (2026-11-12: a colour
        # code per result type), looked up through the panel so the two cannot part.
        def rc(fig_id: str) -> str:
            return figspec.RESULT_COLOURS[_panel_of(fig_id)]
        # Constructs with a section (`shown`) feed the per-construct and median figures;
        # the corrected poly(A) pool, read share and MAPQ use them all (`tracks`).
        shown = self._reported_constructs()

        if rs.get("read_length_distribution"):
            add("read-length", lambda: figspec.histogram_spec(
                    [("read length", rs["read_length_distribution"], rc("read-length"))],
                    x_title="read length (nt)",
                    # Several constructs, several peaks: the fences would hide the
                    # longest construct and the short fragments.
                    window_quantiles=figspec.WINDOW_QUANTILES))
        if pa.get("polya_distribution"):
            # The correction is per construct, so the run-level corrected series pools
            # the constructs' corrected histograms. It covers fewer reads than the
            # uncorrected series: its legend name says whose reads it holds
            # (2026-11-13: the whole-run row said "withheld" beside a corrected curve), and
            # the caption says more.
            series = [("uncorrected", pa["polya_distribution"], rc("polya"))]
            note = ""
            try:
                pooled = self._pooled_corrected(tracks, onto=pa["polya_distribution"])
            except ValueError as exc:
                logger.warning("run-level corrected poly(A) not drawn: %s", exc)
                pooled = None
            if pooled is not None:
                dist, k = pooled
                series.append(("corrected (assigned reads)", dist,
                               figspec.POLYA_CORRECTED))
                note = (f"The corrected series pools the {k} construct"
                        f"{'' if k == 1 else 's'} whose correction "
                        f"applied ({dist['n']:,} reads); unassigned reads and constructs "
                        f"whose correction was withheld are not in it, so it is not the "
                        f"same set of reads. Each construct's correction stretches its bins "
                        f"differently, so their counts are shared onto these bins in "
                        f"proportion to overlap before summing, which smooths shape finer "
                        f"than one bin.")
            note = " ".join(
                s for s in (_smoothing_note(pa["polya_distribution"]), note) if s)
            add("polya", lambda: figspec.histogram_spec(
                    series, x_title="poly(A) length (nt)",
                    window_quantiles=figspec.WINDOW_QUANTILES), extra=note)
        # Read length and poly(A) again, one construct at a time.
        def _by_construct(block: str, key: str) -> list:
            found = []
            for name, snap in shown:
                dist = (snap.get(block) or {}).get(key) or {}
                if dist.get("n"):
                    found.append((name, dist))
            return found

        rl_tracks = _by_construct("read_stats", "read_length_distribution")
        if rl_tracks:
            add("read-length-by-construct", lambda: figspec.per_construct_histogram_spec(
                    rl_tracks, x_title="read length (nt)",
                    colour=rc("read-length-by-construct")))
        pa_tracks = _by_construct("polya", "polya_distribution")
        if pa_tracks:
            pa_corrected = {name: dist for name, dist in
                            ((name, self._applied_corrected(snap)) for name, snap in shown)
                            if dist}
            add("polya-by-construct", lambda: figspec.per_construct_histogram_spec(
                    pa_tracks, x_title="poly(A) length (nt)", corrected=pa_corrected,
                    window_quantiles=figspec.WINDOW_QUANTILES,
                    colour=rc("polya-by-construct"),
                    corrected_colour=figspec.POLYA_CORRECTED),
                extra=_smoothing_note(pa_tracks[0][1]))

        if rs.get("quality_score_distribution"):
            add("qscore", lambda: figspec.histogram_spec(
                    [("qscore", rs["quality_score_distribution"], rc("qscore"))],
                    x_title="mean read Q"))

        # The bars from error_hotspot; the flags, threshold and subtitle from consensus.
        per_position = [
            (name, t["error_hotspot"],
             t["consensus"] if isinstance(t.get("consensus"), dict) else None)
            for name, t in shown if isinstance(t.get("error_hotspot"), dict)]
        if per_position:
            add("per-position", lambda: figspec.per_position_spec(per_position))

        ends = [(name, t["terminal_reach"]["end_position_distribution"])
                for name, t in shown
                if isinstance(t.get("terminal_reach"), dict)
                and t["terminal_reach"].get("end_position_distribution")]
        if ends:
            add("read-end", lambda: figspec.terminal_reach_spec(ends, terminus="last",
                                                    what="read end position",
                                                    colour=rc("read-end")))
        starts = [(name, t["terminal_reach"]["start_position_distribution"])
                  for name, t in shown
                  if isinstance(t.get("terminal_reach"), dict)
                  and t["terminal_reach"].get("start_position_distribution")]
        if starts:
            add("read-start", lambda: figspec.terminal_reach_spec(starts, terminus="first",
                                                    what="read start position",
                                                    colour=rc("read-start")))
        joints = [(name, t["terminal_reach"]["joint_shortfall_distribution"],
                   t["terminal_reach"].get("ref_length"))
                  for name, t in shown
                  if isinstance(t.get("terminal_reach"), dict)
                  and t["terminal_reach"].get("joint_shortfall_distribution")]
        if joints:
            add("shortfall", lambda: figspec.joint_shortfall_spec(joints))

        if tracks:
            add("share", lambda: self._share_spec(tracks))
        if shown:
            add("identity-by-construct", lambda: figspec.ranked_bar_spec(
                    [(name, (t.get("consensus") or {}).get("consensus_identity_pct"))
                     for name, t in shown],
                    x_title="per-base identity (%)", value_suffix="%",
                    colour=rc("identity-by-construct")))
            add("median-length", lambda: figspec.ranked_bar_spec(
                    [(name, ((t.get("read_stats") or {})
                             .get("read_length_distribution") or {}).get("median"))
                     for name, t in shown],
                    x_title="read length (nt)", value_suffix=" nt",
                    colour=rc("median-length")))
        if tracks:
            add("mapq", lambda: self._mapq_spec(tracks))

        inst = self._snapshots.get("end_reason") or {}
        if inst.get("reasons"):
            add("end-reason", lambda: figspec.ranked_bar_spec(
                    [(k, v.get("n")) for k, v in inst["reasons"].items()],
                    x_title="reads"))
        volt = self._snapshots.get("voltage") or {}
        if volt.get("series"):
            add("voltage", lambda: figspec.voltage_spec(volt["series"]))
        pore = self._snapshots.get("pore_activity") or {}
        if pore.get("series"):
            add("pore", lambda: figspec.pore_activity_spec(pore["series"]))
        return out

    def _mapq_spec(self, tracks: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        """MAPQ counts summed over constructs, as a log-scale bar chart.

        Pooled because a low MAPQ usually means another construct in the pool fits as
        well, an ambiguity no single construct owns.
        """
        from . import figspec

        pooled: dict[str, int] = {}
        for _, t in tracks:
            dist = (t.get("mapq") or {}).get("distribution") or {}
            for score, count in dist.items():
                pooled[str(score)] = pooled.get(str(score), 0) + int(count)
        if not pooled:
            raise ValueError("no construct carried a MAPQ distribution")
        return figspec.category_bar_spec(
            pooled, x_title="MAPQ",
            log_y=True, colour=figspec.RESULT_COLOURS[_panel_of("mapq")])

    def _share_spec(self, tracks: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        """Each construct's share of the reads (%), with the unassigned reads as one
        more bar, so no share is overstated."""
        from . import figspec

        items = [(name, t.get("n_reads") or 0) for name, t in tracks]
        unassigned = self._snapshots.get("unassigned") or {}
        if unassigned.get("n_reads"):
            items.append(("(unassigned)", unassigned["n_reads"]))
        drawn = sum(v for _, v in items)
        if not drawn:
            raise ValueError("no reads assigned to any construct")
        # Over the run's primary reads, the count qc_result.json's share_of_reads
        # divides by. The two differ only when a read reached no bucket. Without a run
        # summary the bars' own sum is the only count there is.
        rs = self._snapshots.get(self.module_names["run_summary"])
        total = _get(rs, "mapping", "total") if isinstance(rs, dict) else None
        if total is None:
            total = drawn
        elif total < drawn:
            raise ValueError(f"the constructs and the unassigned bucket hold {drawn:,} "
                             f"reads, more reads than the run's {total:,}")
        return figspec.ranked_bar_spec(
            [(name, round(100.0 * v / total, 2)) for name, v in items],
            x_title="% of reads", value_suffix="%",
            colour=figspec.RESULT_COLOURS[_panel_of("share")])

    @staticmethod
    def _construct_notice(
        ref: str, n_reads: int, consensus: dict[str, Any]
    ) -> Optional[str]:
        """A caveat for a construct too thin for its identity figure, or None.

        Uses only the consensus module's own depth gate, no new threshold: the construct
        has fewer reads than ``min_depth`` (no position can reach it), or no position
        reached it.
        """
        min_depth = consensus.get("min_depth_threshold")
        above = consensus.get("positions_above_min_depth")
        if min_depth is None or above is None:
            return None
        if n_reads < min_depth:
            return (f"{ref}: {n_reads:,} assigned reads, fewer than the {min_depth}× "
                    f"depth gate, so no position can reach it. The identity and error "
                    f"figures below are computed from thin coverage and are indicative "
                    f"only.")
        if above == 0:
            return (f"{ref}: no reference position reached the {min_depth}× depth gate "
                    f"({n_reads:,} reads assigned). The identity and error figures below "
                    f"are computed from positions the module treats as too thin to flag "
                    f"on, and are indicative only.")
        return None

    # --- snapshot extraction (loose; missing values -> "Not measured") -------

    def _build_metrics(self, mapped: int) -> list[dict[str, Any]]:
        """The run-level metric table.

        Measurements only: the report sets no acceptance criteria, so no row has a
        criterion or a verdict (2026-11-09). The one gate Core enforces, the read
        minimum, is stated in the primary-mapped-reads row, whose result says so when a
        forced report falls below it. Missing values show as "Not measured" rather
        than raising.
        """
        # Imported here: ``analysis`` imports the reporter, so a module-level import
        # would be a cycle. The pooling lives with the modules, so this table and
        # qc_result.json agree.
        from ..analysis.modules.consensus import pooled_identity

        rs = self._snapshots.get(self.module_names["run_summary"], {})
        polya = self._snapshots.get(self.module_names["polya"], {})

        # Pooled over every construct. The flat consensus snapshot holds only the
        # construct with the most reads, so it is not read here.
        tracks = self._construct_snapshots()
        consensus = pooled_identity(
            [t.get("consensus") for _, t in tracks if isinstance(t.get("consensus"), dict)])

        mapping = rs.get("mapping", {}) if isinstance(rs, dict) else {}
        mapping_rate = mapping.get("mapping_rate")

        return [
            self._row(
                "Primary mapped reads",
                f"Primary reads mapped to the reference; a report needs "
                f"at least {self.min_reads:,} (min_reads)",
                f"{mapped:,}" if mapped >= self.min_reads
                else f"{mapped:,} (below the minimum)",
            ),
            self._row(
                "Mapping rate",
                "minimap2 primary alignment; mapped / total",
                self._pct(mapping_rate),
            ),
            self._row(
                "Per-base identity",
                self._identity_method(consensus),
                self._pct(_get(consensus, "identity_pct"), already_pct=True),
            ),
            # The run's breakdown has no figure to sit in (the per-position one is per
            # construct), so it is the row under identity: with it, they add to 100.
            self._row(
                "Substitution / deletion",
                "The rest of the same base observations: another base, or a deletion; "
                "insertions, which have no reference position, counted beside them",
                self._breakdown(consensus),
            ),
            self._row(
                "Poly(A) tail length, uncorrected (median)",
                self._polya_method(polya),
                self._nt(_get(polya, "polya_distribution", "median")),
            ),
            *self._short_tail_rows(polya),
            self._full_length_row(),
        ]

    @classmethod
    def _breakdown(cls, pooled) -> str:
        """``substitution% / deletion% (insertions N per 100 bases)``, or Not measured."""
        sub, dele = _get(pooled, "substitution_pct"), _get(pooled, "deletion_pct")
        ins = _get(pooled, "insertions_per_100_bases")
        if sub is None or dele is None or ins is None:
            return "Not measured"
        return (f"{cls._pct(sub, already_pct=True)} / {cls._pct(dele, already_pct=True)} "
                f"(insertions {float(ins):.1f} per 100 bases)")

    @staticmethod
    def _identity_method(pooled) -> str:
        """Method text for an identity row, naming what a pooled identity pools over:
        constructs and base observations."""
        n = _get(pooled, "n_constructs") or 0
        depth = _get(pooled, "scored_depth") or 0
        scope = (f"pooled over {n} construct{'' if n == 1 else 's'}, {depth:,} base "
                 f"observations"
                 if n else "no construct tracked")
        return f"Read bases vs reference, {scope} (every position scored)"

    @staticmethod
    def _construct_identity_method(consensus) -> str:
        """Method text for one construct's identity row: its base observations."""
        depth = _get(consensus, "identity_scored_depth") or 0
        return (f"Read bases vs reference, {depth:,} base observations "
                "(every position scored)")

    @staticmethod
    def _polya_called(polya) -> str:
        """Method text for an uncorrected poly(A) median, naming its denominator: the
        reads with a ``pt`` estimate, out of the primary reads processed.

        Reads without one are disproportionately truncated or degraded, so the median
        can be optimistic.
        """
        base = "Dorado's poly(A) estimate; median over reads with a pt tag"
        cov = _get(polya, "pt_coverage")
        n, total = _get(polya, "n_with_pt"), _get(polya, "reads_processed")
        # The denominator leaves these out (PolyAModule.update), so the page says so.
        left_out = sum((_get(polya, "excluded") or {}).values())
        if cov is not None and n is not None and total is not None:
            other = (f" other than {left_out:,} reverse-strand, chimeric or "
                     "Dorado-split ones" if left_out else "")
            base = (f"{base} ({n:,}/{total:,} = {cov * 100:.1f}% of primary "
                    f"reads{other})")
        elif left_out and not total:
            # Nothing measured, and the reason is the rule rather than dorado.
            base = (f"{base} (none: all {left_out:,} primary reads were reverse-strand, "
                    "chimeric or Dorado-split, and those are left out)")
        return base

    @staticmethod
    def _short_tail_rows(polya) -> list[dict[str, Any]]:
        """The short-tail row, present only when a cutoff is configured."""
        st = _get(polya, "short_tail") or {}
        if st.get("status") != "measured":
            return []
        cutoff, target = st.get("threshold"), st.get("target_length")
        fraction = st.get("short_fraction")
        derived = (target is not None and fraction is not None and cutoff is not None
                   and abs(float(target) * float(fraction) - float(cutoff)) < 1e-9)
        source = f"{float(target):g} nt × {float(fraction):g}" if derived else "set directly"
        scale = "corrected" if st.get("judged_on") == "corrected" else "uncorrected"
        method = (f"Reads with a tail shorter than {float(cutoff):g} nt ({source}), on the "
                  f"{scale} length; in brackets, every read without a length counted long, "
                  f"then short")
        ratio = st.get("ratio")
        value = ("Not measured" if ratio is None else
                 f"{CoAReporter._pct(ratio)} ({CoAReporter._pct(st.get('ratio_lower'))}"
                 f"–{CoAReporter._pct(st.get('ratio_upper'))})")
        return [CoAReporter._row("Short poly(A) tails", method, value)]

    @staticmethod
    def _polya_method(polya) -> str:
        """Method text for the run-level poly(A) row: uncorrected, with a pointer to the
        corrected values, which exist per construct only."""
        return (f"{CoAReporter._polya_called(polya)}. Uncorrected: the correction depends on "
                f"each construct's composition and modification, so it is applied per "
                f"construct, in Per-construct results.")

    @staticmethod
    def _ruler(polya) -> str:
        """Method text for the corrected poly(A) row, from the snapshot's ``correction``
        block: the correction applied (model, arm, factor, shift, range notes), or why
        it was withheld."""
        c = _get(polya, "correction")
        c = c if isinstance(c, dict) else {}
        if not c.get("applied"):
            status = c.get("status") or "not applied"
            reason = c.get("reason")
            return (f"Not corrected ({status}): {reason}" if reason
                    else f"Not corrected ({status})")

        factor = c.get("factor")
        shift = c.get("shift_nt") or 0.0
        model = c.get("run_model_resolved") or c.get("run_model")
        arm, source = c.get("arm"), c.get("arm_source")
        bits = [f"Corrected to the canonical-equivalent ruler for {model}, modification "
                f"{_modification_text(arm)}{f' ({source})' if source else ''}: "
                f"pt \u00d7 {factor:.4f} {'+' if shift >= 0 else chr(0x2212)} "
                f"{abs(shift):.2f} nt"]
        delta = c.get("delta_pct")
        if delta is not None:
            bits.append(f"delta={delta:.3f}%")
        edges = c.get("range_edges")
        if edges:
            bits.append("At the edge of the fitted range: " + "; ".join(edges))
        flags = c.get("range_flags")
        if flags:
            bits.append("OUTSIDE THE FITTED RANGE: " + "; ".join(flags))
        bits.append("The constants are provisional")
        return ". ".join(bits) + "."

    def _full_length_row(self) -> dict[str, Any]:
        """The full-length row: the run's 5'-reach fraction as a range, pooled over the
        constructs.

        The two ends differ by an assumption about the reads not admitted, not by
        sampling error, so neither is shown alone. Pooled by
        ``terminal_reach.pooled_envelope``, as in qc_result.json.
        """
        from ..analysis.modules.terminal_reach import pooled_envelope

        reaches = [snap.get("terminal_reach") for _, snap in self._construct_snapshots()]
        reaches = [r for r in reaches if isinstance(r, dict)]
        if not reaches:
            return self._row("Full length (5' reach)",
                             "Not measured in this run", "Not measured")

        rs = self._snapshots.get(self.module_names["run_summary"], {})
        total = _get(rs, "mapping", "total") if isinstance(rs, dict) else None
        env = pooled_envelope(reaches, total)

        lo, hi = env["fraction_lower"], env["fraction_upper"]
        if hi is None:
            value = "Not measured"
        elif lo is None:
            value = f"\u2264 {hi * 100:.1f}%"
        else:
            _, value = self._envelope_text(lo, hi)
        return self._row("Full length (5' reach)",
                         self._full_length_method(env), value)

    def _construct_full_length_row(self, snap: Mapping[str, Any],
                                   n_reads: int) -> dict[str, Any]:
        """One construct's full-length row: its 5'-reach fraction as a range.

        The two ends are qc_result.json's for the construct (``full_length_fraction``
        over its admitted reads, ``full_length_fraction_lower`` over every read it was
        assigned), computed by the integrator's own functions so the report, the file
        and the GUI give one range. The method says what each end divides by; what the
        range means is said once, in the whole run's row.
        """
        from ..analysis.modules.qc_integrator import _construct_full_length_lower

        name = "Full length (5' reach)"
        tr = snap.get("terminal_reach")
        if not isinstance(tr, dict):
            return self._row(name, "Not measured in this run", "Not measured")
        thr = tr.get("full_length_5p_max_short_nt")
        where = (f"Alignment starts within {thr} nt of the 5' terminus" if thr is not None
                 else "Alignment reach at the 5' terminus")
        hi = tr.get("full_length_fraction")
        lo = _construct_full_length_lower(tr, n_reads)
        if hi is None:
            return self._row(name, f"{where}; none of the {n_reads:,} reads assigned to "
                                   f"this construct was admitted", "Not measured")
        n_adm = int(tr.get("n_admitted") or 0)
        if lo is None:
            return self._row(name, f"{where}; over the {n_adm:,} admitted reads",
                             f"\u2264 {hi * 100:.1f}%")
        is_point, value = self._envelope_text(lo, hi)
        if is_point:
            return self._row(name, f"{where}; over the {n_adm:,} admitted reads", value)
        return self._row(name, f"{where}; upper over the {n_adm:,} admitted reads, lower "
                               f"over all {n_reads:,} reads assigned to this construct",
                         value)

    @staticmethod
    def _envelope_text(lo: float, hi: float) -> tuple[bool, str]:
        """``(is_a_point, text)`` for the full-length range.

        A range at the fewest decimals (up to ``_ENVELOPE_MAX_PLACES``) that tell its
        ends apart; one number when none do. ``_full_length_method`` keys on the same
        test.
        """
        for places in range(1, _ENVELOPE_MAX_PLACES + 1):
            a, b = f"{lo * 100:.{places}f}", f"{hi * 100:.{places}f}"
            if a != b:
                return False, f"{a}%\u2013{b}%"
        return True, f"{hi * 100:.1f}%"

    @staticmethod
    def _full_length_method(env: dict[str, Any]) -> str:
        """Method text for the full-length row: the 5' threshold, what each end of the
        range assumes, and the population (reads from molecules with a poly(A) tail).

        5' because DRS reads a molecule 3' to 5', so a read that stops early loses its
        5' end; a molecule with no poly(A) tail is never sequenced.
        """
        thr = env.get("five_prime_max_short_nt")
        n_adm, total = env.get("n_admitted"), env.get("primary_reads")
        excl = env.get("n_excluded_from_reach")
        parts = [
            f"Alignment starts within {thr} nt of the 5' terminus" if thr is not None
            else "Alignment reach at the 5' terminus",
            "5' because DRS threads the molecule 3'\u21925'",
        ]
        if n_adm is not None and total:
            # The two ends are explained only when they differ (see _envelope_text).
            lo, hi = env.get("fraction_lower"), env.get("fraction_upper")
            # `fraction_upper` is None when no read reached the module.
            if lo is None or hi is None:
                parts.append(f"no bound to report: none of the {total:,} primary reads "
                             f"was admitted")
            elif CoAReporter._envelope_text(lo, hi)[0]:
                parts.append(
                    f"no range: the admitted-only and all-reads denominators give the "
                    f"same figure here ({total:,} primary reads, {excl:,} excluded from "
                    f"reach)")
            else:
                parts.append(
                    f"range = an assumption envelope, not a confidence interval: upper "
                    f"over {n_adm:,} "
                    f"admitted reads (the {excl:,} excluded and the unmapped assumed "
                    f"non-analyte), lower over all {total:,} primary reads (assumed "
                    f"failed molecules)")
        parts.append("population: reads from molecules that carried a poly(A) tail and "
                     "entered a pore (DRS never sees a molecule with no poly(A) tail)")
        return "; ".join(parts)

    @staticmethod
    def _row(name, method, value) -> dict[str, Any]:
        """One metric-table row: a measurement, with no criterion or verdict."""
        return {
            "name": name,
            "method": method,
            "value": value,
        }

    @staticmethod
    def _clock_text(at: datetime) -> str:
        """``2026-11-10 14:03:27 (UTC+09:00)``: a local time and its UTC offset."""
        offset = at.strftime("%z")
        if not offset:
            raise ValueError(f"a report time needs its time zone, got {at!r}")
        return f"{at:%Y-%m-%d %H:%M:%S} (UTC{offset[:3]}:{offset[3:5]})"

    @staticmethod
    def _elapsed_text(seconds: float) -> str:
        """``14 s``, ``12 min 5 s`` or ``1 h 12 min``: the two largest units that apply."""
        if seconds < 0:
            raise ValueError(f"elapsed time must be >= 0, got {seconds!r}")
        hours, rest = divmod(round(seconds), 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours} h {minutes} min"
        if minutes:
            return f"{minutes} min {secs} s"
        return f"{secs} s"

    @staticmethod
    def _pct(value: Any, already_pct: bool = False) -> str:
        if value is None:
            return "Not measured"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "Not measured"
        return f"{v if already_pct else v * 100:.1f}%"

    @staticmethod
    def _nt(value: Any) -> str:
        if value is None:
            return "Not measured"
        try:
            return f"{float(value):.0f} nt"
        except (TypeError, ValueError):
            return "Not measured"

    @staticmethod
    def _num(value: Any, unit: str = "", grouped: bool = False) -> str:
        """One decimal; ``grouped`` writes the thousands as a read count does."""
        if value is None:
            return "Not measured"
        try:
            return f"{float(value):{',' if grouped else ''}.1f}{unit}"
        except (TypeError, ValueError):
            return "Not measured"

