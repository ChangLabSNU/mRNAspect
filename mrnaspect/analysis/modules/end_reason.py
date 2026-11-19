"""Read end reasons from MinKNOW's ``sequencing_summary*.txt``.

``end_reason`` tells a molecule that ended from a read the instrument cut short. A read
ending ``unblock_mux_change`` was ejected from the pore mid-molecule: it is truncated
for reasons unrelated to the mRNA, a confounder for the full-length ratio and poly(A)
length. ``ejected_fraction`` reports its share.

The file can be hundreds of MB, so it is read from a byte offset and only whole lines
are consumed; a partial last line is left for the next call. Not a
:class:`StreamingModule`: its input is not reads.

The BAM tag ``er:Z`` carries the same value per read, but MinKNOW's ``bam_pass`` BAMs
and Core's POD5 mode (``ont_basecall_client``) lack it. The summary file is there
whoever basecalled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from ..minknow import SEQUENCING_SUMMARY_GLOB as SUMMARY_GLOB
from ..streaming_interface import resolve_params

logger = logging.getLogger(__name__)

# realtime.sealing keeps its own copy of this glob; keep the two equal.

_COLUMN = "end_reason"

#: The end reason of a read the instrument ejected mid-molecule (``ejected_fraction``).
EJECTED = "unblock_mux_change"

DEFAULTS: dict[str, Any] = {
    # Bytes per read from the file (at least 1024). Bounds memory only: each call reads
    # chunk by chunk to the end of the file.
    "chunk_bytes": 8 << 20,
}


class EndReasonModule:
    """Per-run tally of MinKNOW's read end reasons, read incrementally."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.params = resolve_params(config, DEFAULTS)
        self._finalized = False
        self._source: Optional[Path] = None
        self._unavailable: Optional[str] = None
        self._offset = 0          # byte position of the first unconsumed line
        self._col: Optional[int] = None
        self._counts: dict[str, int] = {}

    # ---------------------------------------------------------------- ingest

    def update_from_run_dir(self, run_dir: Path | str) -> bool:
        """Count the rows added since the last call; return whether the file was found.

        No file is not an error: the snapshot then says ``available: False`` and why.
        More than one file, or one that changes name between calls, raises ValueError.
        """
        if self._finalized:
            raise RuntimeError("EndReasonModule: update after finalize()")
        run_dir = Path(run_dir)
        matches = sorted(run_dir.glob(SUMMARY_GLOB))
        if not matches:
            logger.info("no %s under %s - not a MinKNOW run directory, or the run has "
                        "not written one yet", SUMMARY_GLOB, run_dir)
            # No path in the reported reason; see pore_activity.
            self._unavailable = (
                f"no {SUMMARY_GLOB} beside the inputs - not a MinKNOW run directory, or "
                f"the run has not yet written one")
            return False
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} sequencing-summary files under {run_dir}: "
                f"{[m.name for m in matches]}. Expected exactly one.")
        path = matches[0]
        if self._source is not None and path != self._source:
            raise ValueError(
                f"the sequencing summary changed name mid-run "
                f"({self._source.name} -> {path.name}); two runs cannot share a tally")
        self._source = path
        self._unavailable = None
        self._consume(path)
        return True

    def _consume(self, path: Path) -> None:
        size = path.stat().st_size
        if size < self._offset:
            # Shorter than what was consumed (a new run, or truncated): start over.
            logger.warning("%s shrank (%d < %d bytes); restarting the end-reason tally",
                           path.name, size, self._offset)
            self._offset, self._col, self._counts = 0, None, {}
        # Binary, so the offset is a byte count; text mode would count characters.
        with path.open("rb") as fh:
            if self._col is None:
                header = fh.readline()
                if not header:
                    raise ValueError(f"{path} is empty")
                names = header.decode("utf-8", "replace").rstrip("\n").split("\t")
                if _COLUMN not in names:
                    raise ValueError(
                        f"{path.name}: no {_COLUMN!r} column "
                        f"(columns: {', '.join(names[:6])}...)")
                self._col = names.index(_COLUMN)
                self._offset = fh.tell()
            fh.seek(self._offset)
            while True:
                chunk = fh.read(self._params_chunk())
                if not chunk:
                    break
                cut = chunk.rfind(b"\n")
                if cut < 0:
                    # No complete line in this chunk; wait for more to be written.
                    break
                self._tally(chunk[:cut].decode("utf-8", "replace"))
                self._offset += cut + 1
                fh.seek(self._offset)

    def _params_chunk(self) -> int:
        return max(1024, int(self.params["chunk_bytes"]))

    def _tally(self, block: str) -> None:
        col = self._col
        assert col is not None
        for line in block.split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) <= col:
                # Partial lines never reach here, so a short row is malformed.
                raise ValueError(
                    f"sequencing summary row has {len(parts)} fields, needs > {col}")
            value = parts[col].strip()
            # Unknown values are kept: dropping one would shrink the denominator.
            self._counts[value] = self._counts.get(value, 0) + 1

    # ------------------------------------------------------------- reporting

    def snapshot(self) -> dict[str, Any]:
        if self._unavailable is not None or not self._counts:
            return {
                "available": False,
                "unavailable_reason": self._unavailable or "no rows parsed yet",
                "source_file": self._source.name if self._source else None,
            }
        total = sum(self._counts.values())
        reasons = {
            name: {"n": n, "fraction": round(n / total, 6)}
            for name, n in sorted(self._counts.items(), key=lambda kv: -kv[1])
        }
        return {
            "available": True,
            # The name, not the path; see pore_activity.
            "source_file": self._source.name,
            "n_reads": total,
            "reasons": reasons,
            # Share of reads ejected mid-molecule; no threshold is applied here.
            "ejected_fraction": reasons.get(EJECTED, {}).get("fraction", 0.0),
        }

    def finalize(self) -> dict[str, Any]:
        self._finalized = True
        return self.snapshot()
