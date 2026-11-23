"""Which input a run folder's run is of, so that a run name cannot continue it with another.

A run name that names an existing folder continues that run. Nothing checked that the
continuing run read the same input. A live run restored the earlier run's files from its
manifest and went on with another MinKNOW run's, so its report covered both (2026-11-23,
reproduced with two mimic runs: 2 + 3 files in one report); offline, the folder's report
was replaced by another input's, under the earlier run's name.

The first start of a run in a folder writes ``run_input.json``: which driver (live or
offline), which mode (POD5 or BAM), the input folder (resolved), and the reference: its
path, which the messages name, and the sha256 of its content, which is what is compared,
because the app writes a pasted reference to a new file at every start. A later start in
the same folder must match on all four, or :func:`check_run_input` refuses it before
anything is processed (exit 2). A folder that holds an earlier run's files but no record
was made before this rule and cannot be checked, so it is refused too; an empty one is a
new run. An input folder that has since been moved counts as another: the safe side.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .checkpoint import file_sha256

RUN_INPUT_NAME = "run_input.json"
SCHEMA_VERSION = 1
#: Files a run writes into its folder. One of them there with no record means a run made
#: before the record existed. (``run.log`` is not one: a refused start writes it too.)
EARLIER_RUN_FILES = ("ingest_manifest.tsv", "basecall_cache.tsv", "latest_snapshot.json",
                     "qc_result.json", "run_status.json")
#: What is compared, and how a message names it.
COMPARED = (("driver", "kind of run"), ("mode", "files read"),
            ("input_dir", "input folder"), ("reference_sha256", "reference"))
_DRIVERS = ("live", "offline")


class RunInputMismatch(ValueError):
    """A continued run's input is not the earlier run's, or cannot be checked.

    A ``ValueError``, so both command lines end it as a startup error (exit 2).
    """


def run_input(*, driver: str, mode: str, input_dir: Path | str,
              reference: Optional[Path | str]) -> dict[str, Any]:
    """The record of a run's input.

    ``reference`` may be None only for a question asked before a run (the app's check
    before Start, with a pasted reference not yet written); its two fields are then None
    and it is not compared. A run always has one.

    Raises:
        ValueError: a driver other than ``live`` or ``offline``.
        OSError: the reference cannot be read.
    """
    if driver not in _DRIVERS:
        raise ValueError(f"driver must be one of {_DRIVERS}, not {driver!r}")
    ref = Path(reference) if reference else None
    return {"schema_version": SCHEMA_VERSION, "driver": driver, "mode": mode,
            "input_dir": str(Path(input_dir).resolve()),
            "reference": str(ref.resolve()) if ref else None,
            "reference_sha256": file_sha256(ref) if ref else None}


def differences(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """What differs between two records, one phrase each; a reference not given in
    ``current`` is not compared."""
    out = []
    for key, what in COMPARED:
        if key == "reference_sha256" and current.get(key) is None:
            continue
        if recorded.get(key) != current.get(key):
            shown = "reference" if key == "reference_sha256" else key
            out.append(f"{what}: {recorded.get(shown)} before, {current.get(shown)} now"
                       + (" (its content differs)" if key == "reference_sha256" else ""))
    return out


def read_run_input(run_dir: Path | str) -> Optional[dict[str, Any]]:
    """The folder's record, or None when it has none.

    Raises:
        RunInputMismatch: the record is there but cannot be read.
    """
    path = Path(run_dir) / RUN_INPUT_NAME
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunInputMismatch(
            f"{path} cannot be read ({exc}), so whether this run's input is the earlier "
            f"run's cannot be checked. Give another run name for a new run.") from exc
    if not isinstance(record, dict):
        raise RunInputMismatch(f"{path} is not a record of a run's input. Give another "
                               f"run name for a new run.")
    return record


def refusal(run_dir: Path | str, current: dict[str, Any]) -> Optional[str]:
    """Why a run with input ``current`` may not use ``run_dir``, or None when it may."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return None
    try:
        recorded = read_run_input(run_dir)
    except RunInputMismatch as exc:
        return str(exc)
    if recorded is not None:
        diff = differences(recorded, current)
        if not diff:
            return None
        return (f"The run name {run_dir.name} is a run of another input, so this run "
                f"cannot continue it: {'; '.join(diff)}. Give another run name for a new "
                f"run, or the same input and reference to continue it.")
    earlier = [name for name in EARLIER_RUN_FILES if (run_dir / name).exists()]
    if earlier:
        return (f"The folder of the run name {run_dir.name} holds an earlier run "
                f"({', '.join(earlier)}) that recorded no input: it was made before "
                f"mRNAspect recorded a run's input, so whether this run's input is the "
                f"same cannot be checked. Give another run name for a new run.")
    return None


def check_run_input(run_dir: Path | str, current: dict[str, Any]) -> str:
    """Refuse a run whose input is not the earlier run's in ``run_dir``; else record it.

    Called by both command lines after the run folder is resolved and before anything is
    processed.

    Returns:
        ``"recorded"`` for the first start in the folder, ``"same"`` for a run that
        continues the earlier run with the same input.

    Raises:
        RunInputMismatch: the reason, in the operator's words (see :func:`refusal`).
        ValueError: ``current`` carries no reference.
    """
    if current.get("reference_sha256") is None:
        raise ValueError("a run's input record needs its reference")
    run_dir = Path(run_dir)
    why = refusal(run_dir, current)
    if why is not None:
        raise RunInputMismatch(why)
    if read_run_input(run_dir) is not None:
        return "same"
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / RUN_INPUT_NAME
    tmp = path.with_name(f".{RUN_INPUT_NAME}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return "recorded"
