"""Read-by-read ingest of aligned BAMs into the QC modules.

Every primary read lands in exactly one bucket: the **construct track** of the reference
contig it mapped to, or the **unassigned track** (unmapped, or mapped to a contig the
reference lacks). Where a metric exists at both levels, a track uses the run level's
module class over its own reads, so a per-construct value is the run-level one over a
subset. The reference is required: every pileup metric is defined against it.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import weakref
from pathlib import Path
from typing import Any, Mapping, Optional

import pysam

from .arms import resolve_arms
from .modules.polya import MixedModelError, resolve_target_lengths
from .read_stats import RunSummaryModule
from .shared_pileup import SharedPileup
from .modules.polya import PolyAModule
from .modules.consensus import ConsensusIdentityModule
from .modules.error_hotspot import ErrorHotspotModule
from .modules.mapq import MapqModule
from .modules.terminal_reach import TerminalReachModule
from ..reporting.reporter import CoAReporter
from ..runtime.reference_check import ReferenceError, reference_contigs
from .. import snapshot_keys as keys

logger = logging.getLogger(__name__)


_RUN_SUMMARY = keys.RUN_SUMMARY
_POLYA = keys.POLYA
_CONSENSUS = keys.CONSENSUS
_ERROR_HOTSPOT = keys.ERROR_HOTSPOT

# Most reference contigs tracked at once (``analysis.max_constructs``). Each track holds
# O(ref_length) pileup arrays, so a transcriptome-scale reference fails at the first
# extra contig instead of exhausting memory mid-run.
DEFAULT_MAX_CONSTRUCTS = 64

# Most primary read IDs held for duplicate detection (``analysis.max_tracked_reads``;
# 0 disables it). Past the cap, checking stops and ``dedup_status`` becomes ``capped``.
DEFAULT_MAX_TRACKED_READS = 10_000_000

# The programs that make Core's own alignment (run_align.sh). In a bam-mode input their
# lines describe an alignment Core discards.
_CORE_ALIGN_PROGRAMS = frozenset({"minimap2", "samtools"})


class MaxConstructsExceeded(ValueError):
    """More distinct reference contigs received reads than ``max_constructs`` allows.

    A configuration error, so ``ingest_bam`` re-raises it instead of skipping the read.
    """


class _UnassignedTrack:
    """Primary reads that match no construct: unmapped, or mapped off-reference.

    Run-summary and poly(A) modules only; there is no reference sequence to pile onto.
    """

    def __init__(self, module_params: Mapping[str, Any]) -> None:
        self.read_stats = RunSummaryModule(config=module_params.get("run_summary"))
        self.polya = PolyAModule(config=module_params.get("polya"))
        self.n_unmapped = 0
        self.n_off_reference = 0
        # Contigs the BAM mapped to but the reference lacks -> read count.
        self.off_reference_contigs: dict[str, int] = {}

    def update(self, read: pysam.AlignedSegment) -> None:
        if read.is_unmapped:
            self.n_unmapped += 1
        else:
            self.n_off_reference += 1
            name = read.reference_name
            self.off_reference_contigs[name] = self.off_reference_contigs.get(name, 0) + 1
        self.read_stats.update(read)
        self.polya.update(read)

    @property
    def n_reads(self) -> int:
        return self.n_unmapped + self.n_off_reference

    def snapshot(self, *, unprocessable: int = 0) -> dict[str, Any]:
        """Snapshot the bucket.

        ``unprocessable`` is the engine's count of reads rejected by its per-read guard,
        reported here because it is the only allowed gap between the buckets' total and
        the run's primary reads (see ``AnalysisEngine.verify_partition``).
        """
        return {
            "n_reads": self.n_reads,
            "unmapped": self.n_unmapped,
            "off_reference": self.n_off_reference,
            "unprocessable": unprocessable,
            "off_reference_contigs": dict(sorted(self.off_reference_contigs.items())),
            "read_stats": self.read_stats.snapshot(),
            "polya": self.polya.snapshot(),
        }

    def finalize(self) -> None:
        self.read_stats.finalize()
        self.polya.finalize()


class _ConstructTrack:
    """Every accumulator for one reference construct, fed only its reads."""

    def __init__(
        self,
        ref_name: str,
        ref_seq: str,
        *,
        module_params: Mapping[str, Any],
        arm: Optional[str] = None,
        target_length: Optional[float] = None,
    ) -> None:
        self.ref_name = ref_name
        self.n_assigned = 0
        self.pileup = SharedPileup(ref_name, ref_seq)
        self.consensus = ConsensusIdentityModule(
            self.pileup,
            config=module_params.get("consensus"),
            )
        # The model is known only once the poly(A) module has read it off the reads, so
        # it is looked up at snapshot.
        self.hotspot = ErrorHotspotModule(
            self.pileup, config=module_params.get("error_hotspot"), arm=arm,
            model_of=lambda: self.polya.run_model_resolved)
        # Unless configured, its read-length histogram is sized by this construct.
        self.read_stats = RunSummaryModule(
            config=module_params.get("run_summary"), ref_length=len(ref_seq))
        # ``arm`` is declared per construct by the operator (no BAM records it). None
        # withholds whatever depends on the arm rather than assuming one.
        # This construct's own target, if it has one; target_lengths stays with the
        # engine, so this instance judges as a single construct.
        polya_cfg = {k: v for k, v in (module_params.get("polya") or {}).items()
                     if k != "target_lengths"}
        if target_length is not None:
            polya_cfg["target_length"] = target_length
        self.polya = PolyAModule(config=polya_cfg, arm=arm, ref_seq=ref_seq)
        self.mapq = MapqModule(config=module_params.get("mapq"))
        # ref_length comes from the loaded sequence, not a recorded value: every
        # terminal distance is measured against it.
        self.terminal_reach = TerminalReachModule(
            ref_length=len(ref_seq), config=module_params.get("terminal_reach"))

    def update(self, read: pysam.AlignedSegment) -> None:
        """Route one primary read assigned to this construct through every accumulator."""
        # Counted first: the read is assigned even if a module below rejects it.
        self.n_assigned += 1
        self.pileup.update(read)  # consensus/hotspot read it lazily
        self.read_stats.update(read)
        self.polya.update(read)
        self.mapq.update(read)
        self.terminal_reach.update(read)

    @property
    def n_reads(self) -> int:
        """Primary reads assigned to this construct.

        Includes reads the pileup left out by rule (its ``excluded``) and those it
        rejected as malformed (``n_not_in_pileup`` in the snapshot).
        """
        return self.n_assigned

    def snapshot(self) -> dict[str, Any]:
        pileup = self.pileup.snapshot()
        return {
            "ref_name": self.ref_name,
            "n_reads": self.n_reads,
            # Assigned here but rejected by the pileup (malformed CIGAR/SEQ).
            "n_not_in_pileup": (self.n_assigned - self.pileup._read_count
                                - sum(pileup["excluded"].values())),
            "pileup": pileup,
            "consensus": self.consensus.snapshot(),
            "error_hotspot": self.hotspot.snapshot(),
            "read_stats": self.read_stats.snapshot(),
            "polya": self.polya.snapshot(),
            "mapq": self.mapq.snapshot(),
            "terminal_reach": self.terminal_reach.snapshot(),
        }

    def finalize(self) -> None:
        self.pileup.finalize()
        self.consensus.finalize()
        self.hotspot.finalize()
        self.read_stats.finalize()
        self.polya.finalize()
        self.mapq.finalize()
        self.terminal_reach.finalize()


class AnalysisEngine:
    """Ingest aligned BAMs read by read and route each read to the QC modules.

    * **Run level**: one ``RunSummaryModule`` and one ``PolyAModule`` see every record
      except a duplicate primary read, which is skipped before any module.
    * **Per bucket**: each primary read also joins one ``_ConstructTrack`` (mapped to a
      reference contig) or the single ``_UnassignedTrack`` (unmapped or off-reference).
      Tracks are created lazily, up to ``max_constructs``.

    The buckets partition the primary reads: their total equals the run-level
    ``mapping.total``, less at most the reads the per-read guard rejected.
    ``verify_partition`` checks it.
    """

    def __init__(
        self,
        reporter: CoAReporter,
        reference: Path | str,
        module_params: Optional[Mapping[str, Any]] = None,
        sample: Optional[Mapping[str, Any]] = None,
        max_constructs: int = DEFAULT_MAX_CONSTRUCTS,
        max_tracked_reads: int = DEFAULT_MAX_TRACKED_READS,
    ) -> None:
        self.reporter = reporter
        self.reference = Path(reference)

        # {module_name: {param: value}}; anything missing takes the module's default.
        self._module_params = dict(module_params or {})
        # Validated now, so an unusable reference fails at construction rather than as
        # a report without its pileup metrics.
        self._reference_contigs = set(reference_contigs(self.reference))
        # An arm declared for a contig the reference lacks would match no track, so it
        # is rejected here.
        self._arms = resolve_arms(sample, self._reference_contigs)
        # Likewise per-construct designed tail lengths.
        self._targets = resolve_target_lengths(self._module_params.get("polya"),
                                               self._reference_contigs)
        self._fasta: Optional[pysam.FastaFile] = None
        # Where the reference's private index lives, once it is opened (_fasta_handle).
        self._fasta_index_dir: Optional[str] = None
        self._header_checked = False
        # Tools named in the BAM headers (``_capture_tool_provenance``); the first entry
        # per program is kept.
        self._tools: dict[str, dict[str, Any]] = {}
        if int(max_constructs) < 1:
            raise ValueError(f"max_constructs must be >= 1, got {max_constructs}")
        self.max_constructs = int(max_constructs)
        self.run_summary = RunSummaryModule(
            config=self._module_params.get("run_summary"),
            ref_length=self._longest_reference_length())
        self.polya = PolyAModule(config=self._module_params.get("polya"))
        # ref_name -> _ConstructTrack, created lazily on the first read that maps there.
        self._constructs: dict[str, _ConstructTrack] = {}
        self._unassigned = _UnassignedTrack(self._module_params)
        # Reads rejected by the per-read guard in ``ingest_bam``. Such a read may have
        # reached some modules and not others, so it is the partition's only slack.
        self._unprocessable = 0

        # -- duplicate primary-read detection --------------------------------------
        # Hashes of the primary read names seen this run. A read ingested twice would
        # count twice towards every depth and depth gate, so repeats are skipped.
        # Primary records only: secondary/supplementary records legitimately share a
        # name, and their non_primary counts are not deduplicated. Hashes save memory;
        # a 64-bit collision is negligible at run scale.
        self.max_tracked_reads = int(max_tracked_reads)
        if self.max_tracked_reads < 0:
            raise ValueError(
                f"max_tracked_reads must be >= 0, got {max_tracked_reads}")
        self._seen_primary: set[int] = set()
        self._duplicate_primary = 0
        self._dedup_status = "active" if self.max_tracked_reads else "disabled"

    # -- reference handling --------------------------------------------------

    def _longest_reference_length(self) -> Optional[int]:
        """Longest contig in the reference, or None if it cannot be read.

        Sizes the run-level read-length histogram, which pools the reads of every
        construct. Best effort: a reference that cannot be indexed falls back to the
        histogram's default range rather than stopping the run here.
        """
        try:
            lengths = self._fasta_handle().lengths
        except Exception as exc:   # noqa: BLE001 - any failure degrades, none is fatal
            logger.info("Could not read reference lengths (%s); the read-length "
                        "histogram uses its fixed default range.", exc)
            return None
        return max(lengths) if lengths else None

    def _fasta_handle(self) -> pysam.FastaFile:
        """The reference FASTA, opened on first use (raises if it cannot be opened).

        Indexed afresh into a private directory, never through a ``.fai`` beside the
        FASTA. htslib trusts an existing index without checking it against the file, so
        after the FASTA is edited a stale one hands back another contig's bases under a
        name, or newlines in mid-sequence, with no error (2026-11-04). An index built from
        the file as it stands cannot be stale, and nothing is written next to the
        reference, whose directory may be read-only.
        """
        if self._fasta is None:
            index_dir = Path(tempfile.mkdtemp(prefix="mrnaspect-fai-"))
            # Removed with the engine, or at exit at the latest.
            weakref.finalize(self, shutil.rmtree, str(index_dir), True)
            self._fasta_index_dir = str(index_dir)
            fai, gzi = index_dir / "reference.fai", index_dir / "reference.gzi"
            # A .gzi is written only for a bgzip-compressed reference.
            pysam.faidx(str(self.reference), "--fai-idx", str(fai), "--gzi-idx", str(gzi))
            self._fasta = pysam.FastaFile(
                str(self.reference), filepath_index=str(fai),
                filepath_index_compressed=str(gzi) if gzi.exists() else None)
        return self._fasta

    def _check_bam_matches_reference(self, bam: pysam.AlignmentFile) -> None:
        """Fail if the BAM was aligned against a *different* reference.

        Checked once, on the first BAM. Raises ``ReferenceError`` if the BAM has no
        ``@SQ`` lines or shares no contig with the reference: every read would still
        count as mapped while every pileup metric stayed empty. A partial overlap is
        allowed (a run need not produce every construct of a panel); BAM contigs the
        reference lacks are logged.
        """
        if self._header_checked:
            return
        self._header_checked = True
        bam_contigs = set(bam.header.references or ())
        if not bam_contigs:
            raise ReferenceError(
                f"BAM {bam.filename!r} has no @SQ header lines — it was aligned against "
                f"an empty reference index. Nothing can be compared to {self.reference}.")
        overlap = bam_contigs & self._reference_contigs
        if not overlap:
            raise ReferenceError(
                f"BAM/reference mismatch: this BAM was aligned against contigs "
                f"{sorted(bam_contigs)[:4]} but the configured reference "
                f"{self.reference} contains {sorted(self._reference_contigs)[:4]}. They "
                f"share none, so every pileup metric (sequence identity, error hotspots) "
                f"would be silently empty while the reads still counted as mapped.")
        missing = bam_contigs - self._reference_contigs
        if missing:
            logger.warning(
                "%d BAM contig(s) absent from the reference (reads mapped to them get no "
                "pileup metrics): %s", len(missing), ", ".join(sorted(missing)[:8]))

    def capture_input_provenance(self, bam_path: Path | str) -> None:
        """Record the basecaller named in a bam-mode input, before Core aligns it.

        Core's alignment writes a new header naming only minimap2 and samtools, so
        dorado's ``@PG`` and the model in ``@RG`` ``DS`` are read from the input. Its
        own minimap2 and samtools lines are left out: they did not make the alignment
        this run reports, and only the first entry per program is kept.
        """
        with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as bam:
            self._capture_tool_provenance(bam, skip=_CORE_ALIGN_PROGRAMS)

    def _capture_tool_provenance(self, bam: pysam.AlignmentFile, *,
                                 skip: frozenset[str] = frozenset()) -> None:
        """Record the tools that produced this BAM, from its own header.

        ``@PG`` lines give each program's version and command line, and an ``@RG``
        ``DS`` field the basecall model. The header states what actually ran, which the
        binaries installed on this host need not match. Best effort: a header without
        these lines adds nothing. Programs named in ``skip`` are left out.
        """
        try:
            header = bam.header.to_dict()
        except Exception:  # pragma: no cover — a header that will not serialize
            logger.warning("Could not read BAM header for tool provenance")
            return
        for pg in header.get("PG", []) or []:
            name = pg.get("PN") or pg.get("ID")
            if not name or str(name) in skip:
                continue
            entry = {"version": pg.get("VN"), "id": pg.get("ID")}
            cl = pg.get("CL")
            if cl:
                # The command line records the flags actually used.
                entry["command_line"] = cl
            self._tools.setdefault(str(name), entry)
        for rg in header.get("RG", []) or []:
            model = rg.get("DS")
            if model and "basecall_model" in str(model):
                self._tools.setdefault(
                    "basecall_model_from_bam",
                    {"read_group": rg.get("ID"), "description": str(model)})

    @property
    def dedup_accounting(self) -> dict[str, Any]:
        """Duplicate-read counts for the run accounting.

        Kept by the engine, not a module: duplicates are dropped before any module.
        """
        return {
            "duplicate_primary_reads": self._duplicate_primary,
            # active | capped | disabled — a 0 count under 'capped' is not a clean bill.
            "status": self._dedup_status,
            "tracked_read_ids": len(self._seen_primary),
            "max_tracked_reads": self.max_tracked_reads,
        }

    @property
    def tool_provenance(self) -> dict[str, dict[str, Any]]:
        """Tools recorded in the ingested BAM headers — for the provenance stamp."""
        return dict(self._tools)

    def _construct_for(self, ref_name: str) -> Optional[_ConstructTrack]:
        """The track for a contig, built on first use; None if the reference lacks it.

        Raises:
            MaxConstructsExceeded: a new track would exceed ``max_constructs``.
        """
        if ref_name in self._constructs:
            return self._constructs[ref_name]
        fasta = self._fasta_handle()
        if ref_name not in fasta.references:
            return None
        if len(self._constructs) >= self.max_constructs:
            raise MaxConstructsExceeded(
                f"reads map to more than max_constructs={self.max_constructs} distinct "
                f"reference contigs (next was {ref_name!r}). Each construct allocates "
                f"O(ref_length) pileup arrays, so this is refused rather than allowed to "
                f"exhaust memory mid-run. Point at a panel reference, or raise "
                f"analysis.max_constructs if this many constructs is genuinely expected.")
        ref_seq = fasta.fetch(ref_name)
        track = _ConstructTrack(
            ref_name, ref_seq,
            module_params=self._module_params,
            arm=self._arms.get(ref_name),
            target_length=self._targets.get(ref_name))
        self._constructs[ref_name] = track
        logger.info("Tracking construct %s (%d nt) [%d/%d]",
                    ref_name, len(ref_seq), len(self._constructs), self.max_constructs)
        return track

    # -- ingest --------------------------------------------------------------

    def ingest_bam(self, bam_path: Path | str) -> int:
        """Stream every read of one aligned BAM through the modules.

        A duplicate primary read is skipped before any module sees it. Every other
        record reaches the run-level modules, and each primary read also joins exactly
        one bucket: its construct track or the unassigned track.

        Returns:
            Records processed, excluding skipped duplicates and reads the per-read guard
            rejected.
        """
        n = 0
        bad = 0
        dup = 0
        with pysam.AlignmentFile(str(bam_path), "rb") as bam:
            # Once per run, before any read: is this BAM even about our reference?
            self._check_bam_matches_reference(bam)
            self._capture_tool_provenance(bam)
            for read in bam.fetch(until_eof=True):
                # Before any module, so a duplicate is counted nowhere.
                if not (read.is_secondary or read.is_supplementary):
                    if not self._note_primary(read.query_name):
                        dup += 1
                        continue
                # Per-read guard: a malformed read is logged and skipped. A failure to
                # open or read the BAM itself propagates.
                try:
                    self.run_summary.update(read)
                    self.polya.update(read)
                    if not (read.is_secondary or read.is_supplementary):
                        # Primary reads only: the population ``mapping.total`` counts.
                        track = (None if read.is_unmapped
                                 else self._construct_for(read.reference_name))
                        if track is not None:
                            track.update(read)
                        else:
                            self._unassigned.update(read)
                except (ReferenceError, MaxConstructsExceeded, MixedModelError):
                    raise  # configuration errors, never a "bad read"
                except Exception:
                    bad += 1
                    self._unprocessable += 1
                    if bad <= 5:  # cap log spam; count is reported below
                        logger.exception(
                            "Skipping unprocessable read %s", read.query_name
                        )
                n += 1
        if bad:
            logger.warning("Ingest of %s: skipped %d/%d bad reads",
                           Path(bam_path).name, bad, n)
        if dup:
            # An error, not a warning: an input was ingested twice.
            logger.error(
                "Ingest of %s: %d duplicate primary read(s) SKIPPED (already seen this "
                "run). Duplicates inflate depth and falsely tighten every depth-gated "
                "metric — check for a repeated input (e.g. an output dir nested under the "
                "input dir, or both chunk and merged BAMs supplied).",
                Path(bam_path).name, dup)
        logger.info("Ingested %d reads (%d skipped) from %s", n - bad, bad, Path(bam_path).name)
        self.push_snapshots()
        return n - bad

    def _note_primary(self, query_name: Optional[str]) -> bool:
        """Record a primary read name; False if it was already seen this run.

        True whenever tracking is off or at its cap, or the name is missing, so the cap
        never drops reads.
        """
        if self._dedup_status != "active" or query_name is None:
            return True
        key = hash(query_name)
        if key in self._seen_primary:
            self._duplicate_primary += 1
            return False
        if len(self._seen_primary) >= self.max_tracked_reads:
            self._dedup_status = "capped"
            logger.error(
                "Duplicate detection stopped at %d tracked reads (max_tracked_reads). "
                "Reads past this point are NOT checked for duplication; the report records "
                "dedup_status='capped' so this is not mistaken for 'no duplicates found'.",
                self.max_tracked_reads)
            return True
        self._seen_primary.add(key)
        return True

    @property
    def primary_total(self) -> int:
        """Primary reads counted so far, unmapped included; equals ``mapping.total``.

        Use this for read-count gates, not ``ingest_bam``'s return value, which also
        counts secondary and supplementary records.
        """
        return self.run_summary._total

    def _primary_construct(self) -> Optional[_ConstructTrack]:
        """The construct with the most assigned reads, or None if none is tracked.

        It fills the flat ``consensus``/``error_hotspot`` snapshot keys; every
        construct, this one included, is also reported under its own key. A tie goes
        to the lexicographically last reference name, so read order cannot change it.
        """
        if not self._constructs:
            return None
        return max(self._constructs.values(),
                   key=lambda track: (track.n_reads, track.ref_name))

    def verify_partition(self) -> dict[str, Any]:
        """Check that every primary read landed in exactly one bucket.

        The buckets' total may fall short of the run's primary reads by at most the
        reads the per-read guard rejected, since such a read may have reached some
        modules and not others.

        Returns:
            The accounting, for the report.

        Raises:
            ValueError: the shortfall is negative or exceeds the rejected reads.
        """
        by_construct = {name: t.n_reads for name, t in sorted(self._constructs.items())}
        bucket_total = sum(by_construct.values()) + self._unassigned.n_reads
        expected = self.run_summary._total
        residual = expected - bucket_total
        if residual < 0 or residual > self._unprocessable:
            raise ValueError(
                f"construct buckets do not partition the primary reads: "
                f"{bucket_total} bucketed ({len(by_construct)} construct(s) + "
                f"{self._unassigned.n_reads} unassigned) vs {expected} primary reads in "
                f"the run summary, residual {residual}, but only {self._unprocessable} "
                f"read(s) were rejected by the per-read guard. A residual outside "
                f"[0, {self._unprocessable}] means reads were routed twice or not at all.")
        return {
            "primary_reads": expected,
            "bucketed": bucket_total,
            "by_construct": by_construct,
            "unassigned": self._unassigned.n_reads,
            "unprocessable": self._unprocessable,
            "unaccounted": residual,
            # Dropped before any module, so not in primary_reads; non-zero means an
            # input was ingested twice.
            "duplicate_primary_reads": self._duplicate_primary,
            # active | capped | disabled. 'capped' is why a 0 count is not a clean bill.
            "dedup_status": self._dedup_status,
            "tracked_read_ids": len(self._seen_primary),
        }

    def push_snapshots(self) -> None:
        """Send each module's current snapshot to the reporter.

        Keys: ``run_summary`` and ``polya`` (run level), ``construct:<ref>`` per
        construct, ``unassigned``, and the flat ``consensus``/``error_hotspot`` of the
        construct with the most reads.
        """
        self.reporter.update(_RUN_SUMMARY, self.run_summary.snapshot())
        self.reporter.update(_POLYA, self.polya.snapshot())
        for name, track in self._constructs.items():
            self.reporter.update(keys.construct_key(name), track.snapshot())
        self.reporter.update(
            keys.UNASSIGNED,
            self._unassigned.snapshot(unprocessable=self._unprocessable))
        primary = self._primary_construct()
        if primary is not None:
            self.reporter.update(_CONSENSUS, primary.consensus.snapshot())
            self.reporter.update(_ERROR_HOTSPOT, primary.hotspot.snapshot())

    def finalize_all(self) -> None:
        """Check the partition, freeze every module, then push the final snapshots.

        The partition is checked first, so a routing error raises before the final
        snapshots exist.
        """
        accounting = self.verify_partition()
        logger.info(
            "Construct accounting: %d primary read(s) -> %d construct(s) %s + %d "
            "unassigned (%d unprocessable)",
            accounting["primary_reads"], len(accounting["by_construct"]),
            accounting["by_construct"], accounting["unassigned"],
            accounting["unprocessable"])
        self.run_summary.finalize()
        self.polya.finalize()
        for track in self._constructs.values():
            track.finalize()
        self._unassigned.finalize()
        self.push_snapshots()
