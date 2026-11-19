"""The run-level files MinKNOW writes beside the data, and how to find them."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

#: Channel-state occupancy, one row per state per minute. See `modules.pore_activity`.
PORE_ACTIVITY_GLOB = "pore_activity_*.csv"

#: Per-read table with an `end_reason` column; hundreds of MB on a real run.
SEQUENCING_SUMMARY_GLOB = "sequencing_summary*.txt"

#: Mux scans, with the bias voltage of each. See `modules.voltage`.
PORE_SCAN_GLOB = "other_reports/pore_scan_data_*.csv"


def find_run_root(data_dir: Path | str, pattern: str) -> Optional[Path]:
    """The MinKNOW run directory for a directory of POD5/BAM inputs, or None.

    MinKNOW writes its run-level files at the run root, beside ``pod5/``, ``bam_pass/``
    and the like, so ``data_dir`` and its parent are checked and nothing higher, which
    could find another run's files.
    """
    data_dir = Path(data_dir)
    for candidate in (data_dir, data_dir.parent):
        if any(candidate.glob(pattern)):
            return candidate
    return None


#: MinKNOW's run-end summary (instrument, flow cell, protocol, ...), one ``key=value``
#: per line.
FINAL_SUMMARY_GLOB = "final_summary_*.txt"

#: The final-summary fields the report lists, with their labels. They are MinKNOW's
#: record of the run, shown apart from what the user declares in config.
FINAL_SUMMARY_FIELDS: tuple[tuple[str, str], ...] = (
    ("protocol_group_id", "Experiment"),
    ("sample_id", "Sample ID (MinKNOW)"),
    ("instrument", "Instrument"),
    ("flow_cell_id", "Flow cell"),
    ("protocol", "Protocol"),
    ("started", "Sequencing started"),
    ("acquisition_stopped", "Sequencing stopped"),
)


def read_final_summary(data_dir: Path | str) -> dict[str, str]:
    """MinKNOW's run-end summary as a dict, or ``{}`` when there is none.

    Empty values are dropped, so a field left blank in MinKNOW (``sample_id=``) is
    absent rather than "". Lines that do not parse are skipped, not raised on: this
    names the run and produces no number. With several files, the first by name is read.
    """
    root = find_run_root(data_dir, FINAL_SUMMARY_GLOB)
    if root is None:
        return {}
    found = sorted(root.glob(FINAL_SUMMARY_GLOB))
    if not found:
        return {}
    out: dict[str, str] = {}
    for line in found[0].read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out
