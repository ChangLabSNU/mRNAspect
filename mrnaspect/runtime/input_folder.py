"""Which folder a run reads, from whatever folder the operator picked.

MinKNOW writes each run three levels under its data folder::

    <data>/<experiment>/<sample>/<YYYYMMDD_HHMM>_<position>_<flow cell>_<run id>/pod5/*.pod5

and Core reads ONE level of it: a live run reads the run folder (the live driver appends
``realtime.pod5_subdir`` itself), a POD5 run reads the folder holding the ``*.pod5``
files, and a BAM run reads the folder holding the ``*.bam`` files. Pick any other level
and Core finds nothing. That was the first live run in the redesigned GUI
(2026-11-12): which of ``demo``, ``no_sample_id``, the run folder and ``pod5`` to pick
was not clear.

:func:`describe` takes any level and says which folder Core would read, how many inputs
it holds now (counted with Core's own glob, so the number is the number Core processes),
and, when the pick holds several runs, every run below it, newest first. It moves the
pick only where there is one answer; with several runs it names them and moves nothing.
Called with no path, it looks in MinKNOW's data folder: the GUI's *find for me*.

Read-only, and cheap: a few directory listings, no file is opened.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

#: Core's input globs by mode (main._MODE_GLOB; test_input_folder.py holds them equal).
MODE_GLOB = {"bam": "*.bam", "pod5": "*.pod5"}
#: The marker MinKNOW writes when a run ends (realtime.watcher.DEFAULT_RUN_END_GLOB).
RUN_END_GLOB = "final_summary_*.txt"
#: Where MinKNOW puts BAM files when it basecalls (seen on real runs).
BAM_SUBDIR = "bam_pass"
#: MinKNOW's data folder per platform. macOS is as seen on one Mac (2026-11-12); the Linux
#: and Windows values are ONT's documented defaults and have not been seen here.
MINKNOW_DATA = {"darwin": "/Library/MinKNOW/data", "linux": "/var/lib/minknow/data",
                "win32": "C:\\data"}
#: How far below the pick to look: <data>/<experiment>/<sample>/<run> is three.
SEARCH_DEPTH = 3
#: A listing names at most this many runs, the newest.
MAX_RUNS = 50
#: MinKNOW's run folder name: date, time, position, flow cell, the run id's first 8.
_RUN_NAME = re.compile(r"^(\d{8})_(\d{4})_([^_]+)_([^_]+)_([0-9a-fA-F]{8})$")


def _count(folder: Path, pattern: str) -> tuple[int, int]:
    """(files matching ``pattern`` directly in ``folder``, their total bytes)."""
    n = size = 0
    try:
        for p in folder.glob(pattern):
            if p.is_file():
                n += 1
                size += p.stat().st_size
    except OSError:
        return 0, 0
    return n, size


def _run_facts(run: Path) -> dict[str, Any]:
    """What a MinKNOW run folder's name and contents say about it.

    A folder named as MinKNOW names a run sits at <data>/<experiment>/<sample>/<run>, and
    the operator typed the first two into MinKNOW, so they name the run in a list
    (2026-11-13: the run folder's own name is a timestamp two levels below them). A folder
    named otherwise claims neither: its parents are wherever it was copied to.
    """
    m = _RUN_NAME.match(run.name)
    started = None
    if m:
        try:
            started = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").strftime(
                "%Y-%m-%d %H:%M")
        except ValueError:
            started = None
    try:
        finished = any(run.glob(RUN_END_GLOB))
    except OSError:
        finished = False
    return {"path": str(run), "name": run.name, "started": started,
            "position": m.group(3) if m else None, "flow_cell": m.group(4) if m else None,
            "experiment": run.parent.parent.name if m else None,
            "sample": run.parent.name if m else None,
            "finished": finished}


def _input_dir_of_run(run: Path, mode: str, live: bool, pod5_subdir: str) -> Path:
    """The folder of a run that Core reads for this kind of run."""
    if live:
        return run
    if mode == "bam":
        return run / BAM_SUBDIR
    return run / pod5_subdir if pod5_subdir else run


def _inputs_of_run(run: Path, mode: str, live: bool, pod5_subdir: str) -> tuple[int, int]:
    folder = run / pod5_subdir if (live and pod5_subdir) else _input_dir_of_run(
        run, mode, live, pod5_subdir)
    return _count(folder, MODE_GLOB["pod5" if live else mode])


def _is_run(folder: Path, mode: str, live: bool, pod5_subdir: str) -> bool:
    """A MinKNOW run folder this kind of run can read.

    A live run's folder holds ``pod5_subdir``, or is named as MinKNOW names a run (date,
    time, position, flow cell, id): a run that has not written its first POD5 may have no
    ``pod5/`` yet. A final summary alone is not enough: a copy that keeps MinKNOW's files
    one level above the POD5s (seen on one run's copy, ``data/``) would read as an empty run.
    """
    try:
        if live:
            if _RUN_NAME.match(folder.name):
                return True
            if not pod5_subdir:
                return any(folder.glob(MODE_GLOB["pod5"]))
            return (folder / pod5_subdir).is_dir()
        if mode == "pod5":
            return bool(pod5_subdir) and (folder / pod5_subdir).is_dir()
        return (folder / BAM_SUBDIR).is_dir()
    except OSError:
        return False


def _runs_below(top: Path, mode: str, live: bool, pod5_subdir: str) -> list[Path]:
    """Run folders within SEARCH_DEPTH levels under ``top``; a run's own folders are not
    searched further. Unreadable folders are passed over."""
    found: list[Path] = []
    level = [top]
    for _ in range(SEARCH_DEPTH):
        nxt: list[Path] = []
        for folder in level:
            try:
                children = [Path(e.path) for e in os.scandir(folder)
                            if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for child in children:
                if _is_run(child, mode, live, pod5_subdir):
                    found.append(child)
                else:
                    nxt.append(child)
        level = nxt
    return found


def _newest_first(runs: list[Path]) -> list[Path]:
    def key(run: Path) -> tuple[str, float]:
        m = _RUN_NAME.match(run.name)
        try:
            mtime = run.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (m.group(1) + m.group(2) if m else "", mtime)
    return sorted(runs, key=key, reverse=True)


def default_data_folder() -> Optional[str]:
    """MinKNOW's data folder on this platform, or None where it has no default."""
    for prefix, path in MINKNOW_DATA.items():
        if sys.platform.startswith(prefix):
            return path
    return None


def describe(path: Optional[str], mode: str, *, live: bool = False,
             pod5_subdir: str = "pod5") -> dict[str, Any]:
    """Which folder a run of this kind reads, from any level the operator picked.

    Args:
        path: the folder picked; empty or None looks in MinKNOW's data folder.
        mode: ``pod5`` or ``bam``, Core's mode.
        live: a live run (the live driver reads the RUN folder and appends
            ``pod5_subdir`` itself).
        pod5_subdir: the run's ``realtime.pod5_subdir`` (``""`` = the run folder itself).

    Returns:
        ``picked``, ``exists``; ``use``, the folder to give Core, or None when there is
        no single answer; ``moved``, "up" or "down" when ``use`` is not the pick;
        ``inputs`` and ``bytes``, what Core would read in ``use`` now (a live run
        counts what MinKNOW has written so far); ``run``, the facts of the run ``use``
        belongs to (its name, start, position, flow cell, and the experiment and sample
        it was started under); ``runs``, every run below the pick, newest first, when there is more
        than one or none was picked; ``note``, one sentence for the operator.

    Raises:
        ValueError: a mode Core does not have, or a live run in bam mode.
    """
    if mode not in MODE_GLOB:
        raise ValueError(f"mode must be one of {sorted(MODE_GLOB)}, not {mode!r}")
    if live and mode != "pod5":
        raise ValueError("a live run reads POD5 files: mode must be pod5")
    searched_default = not (path and str(path).strip())
    picked = Path(str(path).strip()) if not searched_default else None
    if picked is None:
        root = default_data_folder()
        if root is None:
            return {"picked": None, "exists": False, "use": None, "moved": None,
                    "inputs": 0, "bytes": 0, "run": None, "runs": [],
                    "note": "MinKNOW has no default data folder on this platform."}
        picked = Path(root)
    out: dict[str, Any] = {"picked": str(picked), "exists": picked.is_dir(), "use": None,
                           "moved": None, "inputs": 0, "bytes": 0, "run": None,
                           "runs": [], "note": None}
    if not out["exists"]:
        out["note"] = (f"MinKNOW's data folder ({picked}) is not on this computer."
                       if searched_default else f"{picked} is not a folder.")
        return out

    want = MODE_GLOB["pod5" if live else mode]

    def settle(use: Path, run: Optional[Path], moved: Optional[str]) -> dict[str, Any]:
        reads_from = use / pod5_subdir if (live and pod5_subdir) else use
        out["use"], out["moved"] = str(use), moved
        out["inputs"], out["bytes"] = _count(reads_from, want)
        out["run"] = _run_facts(run) if run is not None else None
        return out

    # The pick is what Core reads.
    if live and _is_run(picked, mode, live, pod5_subdir):
        return settle(picked, picked, None)
    if not live and _count(picked, want)[0]:
        parent = picked.parent
        run = parent if _is_run(parent, mode, live, pod5_subdir) else None
        return settle(picked, run, None)
    # One level off: the inputs' own folder picked for a live run, or a run folder for
    # a POD5 or BAM run.
    if live and picked.name == pod5_subdir and _is_run(picked.parent, mode, live,
                                                       pod5_subdir):
        return settle(picked.parent, picked.parent, "up")
    if not live and _is_run(picked, mode, live, pod5_subdir):
        inner = _input_dir_of_run(picked, mode, live, pod5_subdir)
        if _count(inner, want)[0]:
            return settle(inner, picked, "down")
    # Further up: the runs below.
    runs = _newest_first(_runs_below(picked, mode, live, pod5_subdir))
    if not live:
        runs = [r for r in runs if _inputs_of_run(r, mode, live, pod5_subdir)[0]]
    if len(runs) == 1 and not searched_default:
        run = runs[0]
        return settle(_input_dir_of_run(run, mode, live, pod5_subdir), run, "down")
    listed = []
    for run in runs[:MAX_RUNS]:
        facts = _run_facts(run)
        facts["use"] = str(_input_dir_of_run(run, mode, live, pod5_subdir))
        facts["inputs"] = _inputs_of_run(run, mode, live, pod5_subdir)[0]
        listed.append(facts)
    out["runs"] = listed
    kind = "POD5" if (live or mode == "pod5") else "BAM"
    if listed:
        out["note"] = (f"{len(runs)} MinKNOW runs here"
                       f"{f' (the {MAX_RUNS} newest are listed)' if len(runs) > MAX_RUNS else ''}"
                       f": pick one.")
    elif live:
        out["note"] = (f"No MinKNOW run folder (one that holds {pod5_subdir or 'POD5'}/) at "
                       f"or below this folder.")
    else:
        out["note"] = f"No {kind} files here, or in a MinKNOW run below this folder."
    return out
