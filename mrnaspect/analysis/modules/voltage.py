"""Flow-cell bias voltage per mux scan, from MinKNOW's ``pore_scan_data_*.csv``.

MinKNOW raises the magnitude of the bias voltage over a run to offset pore wear, so the
ramp is a flow-cell health signal. Each step also changes translocation speed, and with
it every dwell-derived value such as poly(A) length.

A row is one (channel, well, scan), and the voltage columns should be equal on every
row of a scan. The first row's values are kept; scans whose rows disagree are listed in
``inconsistent_scans`` rather than averaged. The file's classification columns
(``pore``, ``strand``, ...) are sample counts, not channel counts. They are not read
here: occupancy comes from :mod:`.pore_activity`.

The file is small (one row per channel, well and scan), so it is re-read in full on
each call.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Mapping, Optional

from ..minknow import PORE_SCAN_GLOB
from ..streaming_interface import resolve_params

logger = logging.getLogger(__name__)

_SCAN = "scan_number"
_RUNNING = "scan_running_voltage_mv"
_OFFSET = "scan_offset_voltage_mv"
_SECONDS = "seconds_since_start_of_run"
_REQUIRED = (_SCAN, _RUNNING, _OFFSET, _SECONDS)

DEFAULTS: dict[str, Any] = {
    # Most scans in the emitted series; the latest are kept.
    "max_scans_reported": 200,
}


class VoltageModule:
    """Bias voltage per mux scan, from ``other_reports/pore_scan_data_*.csv``."""

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.params = resolve_params(config, DEFAULTS)
        self._finalized = False
        self._source: Optional[Path] = None
        self._unavailable: Optional[str] = None
        self._scans: dict[int, dict[str, Any]] = {}
        self._inconsistent: set[int] = set()

    # ---------------------------------------------------------------- ingest

    def update_from_run_dir(self, run_dir: Path | str) -> bool:
        """Re-read the pore-scan file under ``run_dir``; True if one was found."""
        if self._finalized:
            raise RuntimeError("VoltageModule: update after finalize()")
        run_dir = Path(run_dir)
        matches = sorted(run_dir.glob(PORE_SCAN_GLOB))
        if not matches:
            logger.info("no %s under %s - not a MinKNOW run directory, or no mux scan has "
                        "happened yet", PORE_SCAN_GLOB, run_dir)
            # No path in the reported reason; see pore_activity.
            self._unavailable = (
                f"no {PORE_SCAN_GLOB} beside the inputs - not a MinKNOW run directory, or "
                f"no mux scan has happened yet")
            return False
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} pore-scan files under {run_dir}: "
                f"{[m.name for m in matches]}. Expected exactly one.")
        self._source = matches[0]
        self._unavailable = None
        self._parse(self._source)
        return True

    def _parse(self, path: Path) -> None:
        scans: dict[int, dict[str, Any]] = {}
        inconsistent: set[int] = set()
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(
                    f"{path.name}: missing column(s) {missing}; this is not a MinKNOW "
                    f"pore-scan file, or its layout changed")
            for lineno, row in enumerate(reader, start=2):
                try:
                    scan = int(row[_SCAN])
                    running = float(row[_RUNNING])
                    offset = float(row[_OFFSET])
                    seconds = float(row[_SECONDS])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{path.name}:{lineno}: {exc}") from None
                seen = scans.get(scan)
                if seen is None:
                    scans[scan] = {"running_mv": running, "offset_mv": offset,
                                   "first_seconds": seconds}
                    continue
                # Rows of one scan should agree; a scan whose rows do not is reported,
                # not averaged.
                if seen["running_mv"] != running or seen["offset_mv"] != offset:
                    inconsistent.add(scan)
                seen["first_seconds"] = min(seen["first_seconds"], seconds)
        self._scans, self._inconsistent = scans, inconsistent

    # ------------------------------------------------------------- reporting

    def snapshot(self) -> dict[str, Any]:
        if self._unavailable is not None or not self._scans:
            return {
                "available": False,
                "unavailable_reason": self._unavailable or "no scans parsed",
                "source_file": self._source.name if self._source else None,
            }
        order = sorted(self._scans)
        cap = max(1, int(self.params["max_scans_reported"]))
        # When capped, keep the latest scans; first/latest below still span the run.
        shown = order[-cap:]
        series = [
            {"scan": s,
             "t_hours": round(self._scans[s]["first_seconds"] / 3600.0, 3),
             "running_mv": self._scans[s]["running_mv"],
             "offset_mv": self._scans[s]["offset_mv"]}
            for s in shown
        ]
        first, last = self._scans[order[0]], self._scans[order[-1]]
        return {
            "available": True,
            "source_file": self._source.name,
            "n_scans": len(order),
            "scans_reported": len(series),
            "first_running_mv": first["running_mv"],
            "latest_running_mv": last["running_mv"],
            # Signed, in mV: latest minus first. RNA004 bias is negative, so a rising
            # magnitude makes this more negative.
            "ramp_mv": round(last["running_mv"] - first["running_mv"], 3),
            "series": series,
            # Scans whose rows disagreed; the series shows each one's first row.
            "inconsistent_scans": sorted(self._inconsistent),
        }

    def finalize(self) -> dict[str, Any]:
        self._finalized = True
        return self.snapshot()
