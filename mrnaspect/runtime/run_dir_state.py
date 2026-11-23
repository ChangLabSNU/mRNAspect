"""What is already in a run folder, before a run of the same name continues it.

A run name that names an existing folder CONTINUES that run: the live driver restores the
inputs on its manifest instead of processing them again, the offline driver reuses the
basecalls on record, and either replaces the report already there. Nothing said so
before the run started, nor while it ran (2026-11-23: "it's hard to know if the run is
continued or not"). ``--print-config`` now answers with :func:`describe_run_dir` for the
folder a Start would write, so the window can say it before Start is pressed; the drivers
say what they carried over in ``run_status.json`` (``continued``).

Read-only, and cheap: a few small files are read, nothing is created or changed. A file
that is there but cannot be read is named in ``unreadable`` rather than raised, because
this answers a hint, not a run; the run itself reads the same files and fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..realtime.manifest import MANIFEST_NAME, IngestManifest, ManifestIntegrityError
from ..reporting.summary_writer import RESULT_JSON_NAME
from .checkpoint import DEFAULT_STATE_NAME, Checkpoint
from .run_input import RUN_INPUT_NAME, RunInputMismatch, read_run_input, refusal
from .run_status import RUN_STATUS_NAME


def describe_run_dir(run_dir: Path | str, current: Optional[dict[str, Any]] = None,
                     not_compared: Optional[list[str]] = None) -> dict[str, Any]:
    """Describe the run folder ``run_dir``: whether it exists and what an earlier run left.

    ``current`` is the input a Start would give (:func:`run_input.run_input`), when the
    asker knows it; ``not_compared`` names what it could not give (the reference, when it
    is pasted and not yet written).

    Returns:
        ``recorded_input``: the earlier run's record of its input (``run_input.json``),
        or None. ``refusal``: why a Start with ``current`` would be refused, in Core's
        own words (the same function the run calls), or None. ``not_compared``: as
        given.
        ``exists``: the folder is there, so a run of this name continues it.
        ``inputs_processed``, ``inputs_found``, ``inputs_total``: the earlier attempt's
        last progress (``run_status.json``), or ``None`` when it wrote none.
        ``report``: the reason of the report already there (``run_end``: it finished;
        ``user_abort``: it was stopped), or ``None`` when there is no final report.
        ``inputs_on_record``: inputs on the live driver's manifest, which a live run
        restores without processing them again.
        ``basecalls_cached``: basecalls on the offline driver's record, which a POD5 run
        reuses where the POD5 file and the model are unchanged.
        ``unreadable``: names of files that are there but could not be read.
    """
    run_dir = Path(run_dir)
    state: dict[str, Any] = {
        "exists": run_dir.is_dir(), "inputs_processed": None, "inputs_found": None,
        "inputs_total": None, "report": None, "inputs_on_record": 0,
        "basecalls_cached": 0, "unreadable": [], "recorded_input": None,
        "refusal": None, "not_compared": list(not_compared or [])}
    if not state["exists"]:
        return state

    try:
        state["recorded_input"] = read_run_input(run_dir)
    except RunInputMismatch as exc:
        state["unreadable"].append(f"{RUN_INPUT_NAME}: {exc}")
    if current is not None:
        state["refusal"] = refusal(run_dir, current)

    status = _read_json(run_dir / RUN_STATUS_NAME, state)
    if status is not None:
        for key in ("inputs_processed", "inputs_found", "inputs_total"):
            state[key] = status.get(key)
    result = _read_json(run_dir / RESULT_JSON_NAME, state)
    if result is not None:
        state["report"] = (result.get("report") or {}).get("reason")

    manifest = run_dir / MANIFEST_NAME
    if manifest.exists():
        try:
            entries, _torn = IngestManifest(manifest, root=run_dir).read()
            state["inputs_on_record"] = len({e.pod5_name for e in entries})
        except (OSError, ManifestIntegrityError) as exc:
            state["unreadable"].append(f"{MANIFEST_NAME}: {exc}")
    cache = run_dir / DEFAULT_STATE_NAME
    if cache.exists():
        try:
            state["basecalls_cached"] = len(Checkpoint(cache))
        except OSError as exc:
            state["unreadable"].append(f"{DEFAULT_STATE_NAME}: {exc}")
    return state


def _read_json(path: Path, state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """``path`` as a JSON object, or ``None`` when it is absent or unreadable (named in
    ``state["unreadable"]``)."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        state["unreadable"].append(f"{path.name}: {exc}")
        return None
    if not isinstance(value, dict):
        state["unreadable"].append(f"{path.name}: not a JSON object")
        return None
    return value
