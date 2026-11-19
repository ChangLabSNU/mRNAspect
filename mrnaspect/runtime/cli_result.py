"""The one machine-readable result Core's CLIs print on stdout.

Every exit path (a report, too few reads, a bad reference, a usage mistake, a run in
which every input failed) prints one JSON object with the keys in :data:`RESULT_KEYS` to
stdout, and one line for a person to stderr. A pipe therefore receives only the JSON::

    $ mrnaspect --input-dir run1 ... | jq .status

``exit_code`` is repeated in the object for callers that read only stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, TextIO

# Statuses that exit 0. `insufficient` is a verdict (too few reads to diagnose, as the
# report says), not a failure.
OK_STATUSES: tuple[str, ...] = ("report", "insufficient")

# The keys of every emission, in the order written. An absent value is null, never
# omitted, so a consumer can index the object unconditionally.
RESULT_KEYS: tuple[str, ...] = (
    "status",        # "report" | "insufficient" | "error" | whatever finalize returned
    "exit_code",     # the process's own exit status, repeated here for stdout-only callers
    "run_dir",       # absolute; null only for failures raised before the run dir resolved
    "report",        # absolute path to the rendered CoA, or null
    "qc_result",     # absolute path to qc_result.json, or null if it was not written
    "mapped_reads",  # primary mapped reads the verdict is based on, or null
    "message",       # one human sentence (low-reads banner, "recovered", ...) or null
    "error",         # the failure text, or null
    "reason",        # WHY it ended: "run_end" | "user_abort" | null for a refusal
    "inputs_processed",  # how many inputs the numbers actually cover
    "inputs_total",  # how many there were; null in real time, where it is unknowable
)


def stamp_run_outcome(result: dict[str, Any], *, reason: Any,
                      processed: Optional[int], total: Optional[int]) -> dict[str, Any]:
    """Add ``reason``, ``inputs_processed`` and ``inputs_total`` to a run's result dict.

    They tell a caller whether the run was stopped early and how many inputs its numbers
    cover. Call after ``write_result_json``, so ``qc_result.json`` is unchanged.
    ``total`` is None in real time, where the input set is not known until the run ends.
    """
    result["reason"] = getattr(reason, "value", None) or str(reason)
    result["inputs_processed"] = processed
    result["inputs_total"] = total
    return result


def exit_code_for(status: Optional[str]) -> int:
    """0 if the run reached a verdict, 1 otherwise."""
    return 0 if status in OK_STATUSES else 1


def _emit(payload: dict[str, Any], human: str, *,
          out: Optional[TextIO], err: Optional[TextIO]) -> int:
    # Resolved per call, not as default arguments, which are bound at import: a caller
    # (or pytest) may redirect the streams after importing this module.
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    # In RESULT_KEYS order, the documented one; not sorted.
    ordered = {k: payload.get(k) for k in RESULT_KEYS}
    print(json.dumps(ordered), file=out, flush=True)
    print(human, file=err, flush=True)
    return int(ordered["exit_code"])


def _canonical(path: Optional[Path | str]) -> Optional[str]:
    """Absolute and symlink-free, as a string, or None.

    Every path in the object goes through here, so one directory never appears under two
    spellings (``/tmp`` and ``/private/tmp`` on macOS).
    """
    if path is None:
        return None
    try:
        return str(Path(path).resolve())
    except OSError:  # unresolvable; the unresolved absolute form still beats nothing
        return str(Path(path).absolute())


def _qc_result_path(run_dir: Optional[Path]) -> Optional[str]:
    """The run's ``qc_result.json`` path if it was written, else None.

    The filename is imported locally, keeping the reporting stack out of ``runtime``'s
    imports.
    """
    if run_dir is None:
        return None
    from ..reporting.summary_writer import RESULT_JSON_NAME

    path = Path(run_dir) / RESULT_JSON_NAME
    return _canonical(path) if path.exists() else None


def emit_result(result: Mapping[str, Any], *, run_dir: Path,
                exit_code: Optional[int] = None, message: Optional[str] = None,
                out: Optional[TextIO] = None, err: Optional[TextIO] = None) -> int:
    """Emit a finished run and return its exit code.

    ``result`` is the dict from ``CoAReporter.finalize()``. Pass ``exit_code`` only to
    override the status-derived value — the offline driver does this for a run in which
    every input failed, which renders a report shell over no data and so must not exit 0.
    """
    status = result.get("status")
    code = exit_code_for(status) if exit_code is None else exit_code
    report = result.get("path")
    mapped = result.get("mapped_reads")
    note = message or result.get("message")
    payload = {
        "status": status,
        "exit_code": code,
        "run_dir": _canonical(run_dir),
        "report": _canonical(report),
        "qc_result": _qc_result_path(run_dir),
        "mapped_reads": mapped,
        "message": note,
        "error": None,
        "reason": result.get("reason"),
        "inputs_processed": result.get("inputs_processed"),
        "inputs_total": result.get("inputs_total"),
    }
    human = f"{status}"
    # Lead with the stop, so the numbers after it read as partial.
    if payload["reason"] == "user_abort":
        human = f"stopped by operator ({status})"
    done, total_in = payload["inputs_processed"], payload["inputs_total"]
    if done is not None and total_in is not None and done != total_in:
        human += f" · covers {done} of {total_in} inputs"
    if mapped is not None:
        human += f" · {mapped:,} mapped reads"
    if report:
        human += f" · {report}"
    if note:
        human += f" · {note}"
    return _emit(payload, human, out=out, err=err)


def emit_error(error: str, *, exit_code: int, run_dir: Optional[Path] = None,
               out: Optional[TextIO] = None, err: Optional[TextIO] = None) -> int:
    """Emit a refusal and return ``exit_code``.

    By convention in these CLIs: **2** is "you asked for something impossible" (bad flags,
    a missing input directory, a config the driver will not start on), fixed by editing
    the command; **1** is "the run started and could not produce a verdict".
    """
    payload = {
        "status": "error",
        "exit_code": exit_code,
        "run_dir": _canonical(run_dir),
        "report": None,
        "qc_result": _qc_result_path(run_dir),
        "mapped_reads": None,
        "message": None,
        "error": error,
        # A refusal never reached a run, so there is no reason and nothing was covered.
        "reason": None,
        "inputs_processed": None,
        "inputs_total": None,
    }
    return _emit(payload, f"ERROR: {error}", out=out, err=err)
