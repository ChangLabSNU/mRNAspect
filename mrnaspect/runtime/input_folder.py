"""Which folder a run reads, from whatever folder the operator picked.

MinKNOW writes each run three levels under its data folder::

    <data>/<experiment>/<sample>/<YYYYMMDD_HHMM>_<position>_<flow cell>_<run id>/pod5/*.pod5

and Core reads ONE level of it: a live run reads the run folder (the live driver appends
``realtime.pod5_subdir`` itself), a POD5 run reads the folder holding the ``*.pod5``
files, and a BAM run reads the folder holding the ``*.bam`` files. Pick any other level
and Core finds nothing. That was the first live run in the redesigned GUI
(2026-11-12): which of ``demo``, ``no_sample_id``, the run folder and ``pod5`` to pick
was not clear.

A live run can also follow MinKNOW's own BAM files (mode ``bam``): given the run folder,
it follows ``bam_pass/`` and ``bam_fail/`` in it; given one of the two, that one only.

A BAM run has one more case. MinKNOW splits the reads it basecalls on their qscore, into
``bam_pass/`` and ``bam_fail/``. The run folder holds no BAM itself, so it means the run:
both folders are read (:func:`bam_folders`). ``bam_pass/`` picked by itself is the passed
reads only, and :func:`describe` then says how many failed-read files are left out. One
folder was read before, ``bam_pass/``, whichever was picked, and nothing said so.

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
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .ordering import natural_key, natural_sorted

#: Core's input globs by mode (main._MODE_GLOB; test_input_folder.py holds them equal).
MODE_GLOB = {"bam": "*.bam", "pod5": "*.pod5"}
#: The marker MinKNOW writes when a run ends (realtime.watcher.DEFAULT_RUN_END_GLOB).
RUN_END_GLOB = "final_summary_*.txt"
#: Where MinKNOW puts BAM files when it basecalls: the reads that pass its qscore filter
#: (seen on real runs) and those that fail it. A barcoded run has one folder per barcode
#: under each (ONT's documented layout; not seen here).
BAM_SUBDIR = "bam_pass"
BAM_FAIL_SUBDIR = "bam_fail"
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


def bam_folders(input_dir: Path | str) -> list[Path]:
    """The folders a BAM run reads for ``input_dir``, in reading order.

    A folder that holds BAM files is read as it is, and only it: that is how
    ``bam_pass/`` picked by itself gives the passed reads only, and how one barcode's
    folder stays apart from the others. A folder that holds none but holds MinKNOW's
    ``bam_pass/`` or ``bam_fail/`` is a run folder: both are read, the passed reads
    first. MinKNOW fails a read on its qscore, so leaving ``bam_fail/`` out of a run is
    a qscore filter. Nothing below these folders is read.
    """
    input_dir = Path(input_dir)
    if _count(input_dir, MODE_GLOB["bam"])[0]:
        return [input_dir]
    found = [input_dir / name for name in (BAM_SUBDIR, BAM_FAIL_SUBDIR)
             if (input_dir / name).is_dir()]
    return found or [input_dir]


def discover_inputs(input_dir: Path | str, mode: str) -> list[Path]:
    """The files a run reads from ``input_dir``, in the order it reads them.

    Each folder in MinKNOW's own order (``_2`` before ``_10``); for a run folder in BAM
    mode, ``bam_pass/`` and then ``bam_fail/`` (:func:`bam_folders`). Empty when there
    are none: the caller says so.

    Raises:
        ValueError: two BAM files of one name in a run's two folders. Everything a run
            keeps for an input is named after the file, so one would replace the other.
    """
    input_dir = Path(input_dir)
    if mode != "bam":
        return natural_sorted(input_dir.glob(MODE_GLOB[mode]))
    files: list[Path] = []
    for folder in bam_folders(input_dir):
        files += natural_sorted(folder.glob(MODE_GLOB["bam"]))
    twice = sorted(name for name, n in Counter(p.name for p in files).items() if n > 1)
    if twice:
        raise ValueError(
            f"{', '.join(twice[:5])}{' and others' if len(twice) > 5 else ''}: in both "
            f"{BAM_SUBDIR}/ and {BAM_FAIL_SUBDIR}/ of {input_dir}. Each input's results "
            f"are kept under its file name, so two files of one name cannot be read in "
            f"one run. Rename one, or give one of the two folders by itself.")
    return files


def barcode_folders(run: Path | str) -> list[Path]:
    """The folders directly under a run's ``bam_pass/`` that hold BAM files.

    A barcoded run has one per barcode, and no BAM file directly in ``bam_pass/``.
    They are listed for the operator to pick one; they are never read together.
    """
    top = Path(run) / BAM_SUBDIR
    try:
        subs = [Path(e.path) for e in os.scandir(top) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted((f for f in subs if _count(f, MODE_GLOB["bam"])[0]),
                  key=lambda f: natural_key(f.name))


def no_inputs_message(input_dir: Path | str, mode: str) -> str:
    """Why a run has nothing to read in ``input_dir``, with the way on where there is one."""
    input_dir = Path(input_dir)
    said = f"No {MODE_GLOB[mode]} files in {input_dir} (mode={mode})"
    if mode != "bam":
        return said
    barcodes = barcode_folders(input_dir)
    if barcodes:
        names = ", ".join(f.name for f in barcodes[:6])
        said += (f". This run is barcoded: its {BAM_SUBDIR}/ holds one folder per barcode "
                 f"({names}{', ...' if len(barcodes) > 6 else ''}). Give one of them; "
                 f"barcodes are not read together")
    return said


def _mirror(folder: Path, own: str, other: str) -> Optional[Path]:
    """The folder that mirrors ``folder`` under the run's other BAM folder, or None.

    With ``own`` ``bam_pass`` and ``other`` ``bam_fail``: ``<run>/bam_pass`` gives
    ``<run>/bam_fail``, and ``<run>/bam_pass/barcode01`` gives
    ``<run>/bam_fail/barcode01``. None for a folder that is not in an ``own``.
    """
    parts = folder.parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == own:
            return Path(*parts[:i], other, *parts[i + 1:])
    return None


def _files(n: int) -> str:
    return f"{n} BAM file{'' if n == 1 else 's'}"


def _all_in(n: int, folder: str) -> str:
    """"its 1 BAM file is in bam_pass/", "its 5 BAM files are all in bam_pass/"."""
    return f"its {_files(n)} {'is' if n == 1 else 'are all'} in {folder}/"


def _bam_facts(use: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """What a BAM run of ``use`` does with the passed and the failed reads.

    Returns ``(bam, note)``. ``bam`` is None for a folder with nothing of MinKNOW's about
    it; else ``pass`` and ``fail`` (each ``dir``, ``inputs``, ``bytes``),
    ``pass_included`` and ``fail_included``, and ``switch``, the folder to pick for the
    other choice about the failed reads, or None when there is none to offer. ``note`` is
    one or two sentences for the operator.

    ``bam_fail/`` picked by itself is the failed reads only (``pass_included`` False): the
    note says how many passed-read files are left out. Nothing was said of it before,
    while ``bam_pass/`` picked by itself named what it left out.
    """
    want = MODE_GLOB["bam"]

    def facts(folder: Path) -> dict[str, Any]:
        n, size = _count(folder, want)
        return {"dir": str(folder), "inputs": n, "bytes": size}

    if bam_folders(use) != [use]:
        # A run folder: both are read.
        passed, failed = facts(use / BAM_SUBDIR), facts(use / BAM_FAIL_SUBDIR)
        both = passed["inputs"] and failed["inputs"]
        bam = {"pass": passed, "fail": failed, "pass_included": True,
               "fail_included": True, "switch": passed["dir"] if both else None}
        if both:
            note = (f"Passed and failed reads together: {_files(passed['inputs'])} in "
                    f"{BAM_SUBDIR}/ and {failed['inputs']} in {BAM_FAIL_SUBDIR}/. For the "
                    f"passed reads only, pick {BAM_SUBDIR}/.")
        elif passed["inputs"]:
            note = (f"This run has no BAM files in {BAM_FAIL_SUBDIR}/: "
                    f"{_all_in(passed['inputs'], BAM_SUBDIR)}.")
        elif failed["inputs"]:
            note = (f"This run has no BAM files in {BAM_SUBDIR}/: "
                    f"{_all_in(failed['inputs'], BAM_FAIL_SUBDIR)}, the reads MinKNOW "
                    f"failed.")
        else:
            note = None
        return bam, note
    mirror = _mirror(use, BAM_SUBDIR, BAM_FAIL_SUBDIR)
    if mirror is not None:
        failed = facts(mirror)
        whole_run = use.name == BAM_SUBDIR
        bam = {"pass": facts(use), "fail": failed, "pass_included": True,
               "fail_included": False,
               "switch": str(use.parent) if (failed["inputs"] and whole_run) else None}
        if not failed["inputs"]:
            return bam, None
        note = (f"Passed reads only: {_files(failed['inputs'])} of failed reads in "
                f"{mirror} {'is' if failed['inputs'] == 1 else 'are'} not included. "
                f"MinKNOW fails a read on its qscore, so this is a qscore filter. To "
                f"include them, ")
        note += (f"pick the run folder: {use.parent}" if whole_run else
                 "put the BAM files of both folders in one folder and pick it.")
        return bam, note
    mirror = _mirror(use, BAM_FAIL_SUBDIR, BAM_SUBDIR)
    if mirror is None:
        return None, None
    # bam_fail/ by itself, or one barcode's folder in it: the failed reads only. No
    # `switch`: that is the choice about the failed reads, and here they are what is read.
    passed = facts(mirror)
    bam = {"pass": passed, "fail": facts(use), "pass_included": False,
           "fail_included": True, "switch": None}
    if not passed["inputs"]:
        return bam, None
    note = (f"Failed reads only, the reads MinKNOW failed on their qscore: "
            f"{_files(passed['inputs'])} of passed reads in {mirror} "
            f"{'is' if passed['inputs'] == 1 else 'are'} not included. To include them, ")
    note += (f"pick the run folder: {use.parent}" if use.name == BAM_FAIL_SUBDIR else
             "put the BAM files of both folders in one folder and pick it.")
    return bam, note


def _run_above(folder: Path, mode: str, live: bool, pod5_subdir: str) -> Optional[Path]:
    """The MinKNOW run a folder of inputs belongs to, or None.

    The folder's parent, or for a folder under ``bam_pass/`` or ``bam_fail/`` (one
    barcode's) the folder above that.
    """
    candidates = [folder.parent]
    parts = folder.parts
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] in (BAM_SUBDIR, BAM_FAIL_SUBDIR):
            candidates.append(Path(*parts[:i]))
            break
    for candidate in candidates:
        if _is_run(candidate, mode, live, pod5_subdir):
            return candidate
    return None


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
    """The folder of a run that Core is given for this kind of run.

    A BAM run is given the run folder itself, which reads ``bam_pass/`` and
    ``bam_fail/`` (:func:`bam_folders`).
    """
    if live or mode == "bam":
        return run
    return run / pod5_subdir if pod5_subdir else run


def _n_inputs(folder: Path, mode: str) -> tuple[int, int]:
    """(files, bytes) an offline run of ``mode`` reads when given ``folder``."""
    if mode != "bam":
        return _count(folder, MODE_GLOB[mode])
    n = size = 0
    for sub in bam_folders(folder):
        a, b = _count(sub, MODE_GLOB["bam"])
        n, size = n + a, size + b
    return n, size


def _inputs_of_run(run: Path, mode: str, live: bool, pod5_subdir: str) -> tuple[int, int]:
    if live and mode == "pod5":
        return _count(run / pod5_subdir if pod5_subdir else run, MODE_GLOB["pod5"])
    return _n_inputs(_input_dir_of_run(run, mode, live, pod5_subdir), mode)


def _is_run(folder: Path, mode: str, live: bool, pod5_subdir: str) -> bool:
    """A MinKNOW run folder this kind of run can read.

    A live run's folder holds ``pod5_subdir`` (for a live BAM run, ``bam_pass/`` or
    ``bam_fail/``), or is named as MinKNOW names a run (date, time, position, flow cell,
    id): a run that has not written its first file may have neither folder yet. A final
    summary alone is not enough: a copy that keeps MinKNOW's files one level above the
    POD5s (seen on one run's copy, ``data/``) would read as an empty run.
    """
    try:
        if live:
            if _RUN_NAME.match(folder.name):
                return True
            if mode == "bam":
                return (folder / BAM_SUBDIR).is_dir() or (folder / BAM_FAIL_SUBDIR).is_dir()
            if not pod5_subdir:
                return any(folder.glob(MODE_GLOB["pod5"]))
            return (folder / pod5_subdir).is_dir()
        if mode == "pod5":
            return bool(pod5_subdir) and (folder / pod5_subdir).is_dir()
        return (folder / BAM_SUBDIR).is_dir() or (folder / BAM_FAIL_SUBDIR).is_dir()
    except OSError:
        return False


def _runs_below(top: Path, mode: str, live: bool, pod5_subdir: str,
                unreadable: Optional[list[Path]] = None) -> list[Path]:
    """Run folders within SEARCH_DEPTH levels under ``top``; a run's own folders are not
    searched further. A folder that cannot be listed is passed over and appended to
    ``unreadable``, so the answer can say runs may be inside it: passed over in silence,
    one folder this account may not read made MinKNOW's whole data folder read as holding
    no run (2026-11-23)."""
    found: list[Path] = []
    level = [top]
    for _ in range(SEARCH_DEPTH):
        nxt: list[Path] = []
        for folder in level:
            try:
                children = [Path(e.path) for e in os.scandir(folder)
                            if e.is_dir(follow_symlinks=False)]
            except OSError:
                if unreadable is not None:
                    unreadable.append(folder)
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


def _same_names(listed: list[dict[str, Any]]) -> str:
    """A sentence for a list in which several runs carry one experiment and sample.

    The list titles a run by those two names, the ones typed into MinKNOW. MinKNOW makes a
    new run folder each time a run is started, so a run started twice gives two rows of
    one title, and the list read as one run shown twice (2026-11-22). Empty when every
    run's names are its own.
    """
    counts = Counter((r["experiment"], r["sample"]) for r in listed
                     if r.get("experiment") and r.get("sample"))
    shared = [(names, n) for names, n in counts.items() if n > 1]
    if not shared:
        return ""
    if len(shared) == 1:
        (experiment, sample), n = shared[0]
        if n < len(listed):
            which = f"{n} of them"
        else:
            which = "Both" if n == 2 else f"All {n}"
        which += f" were started as {experiment} / {sample}"
    else:
        which = "Several were started under one experiment and sample"
    return (f" {which}: MinKNOW makes a new run folder each time a run is started, so "
            f"they are separate runs. The start time tells them apart.")


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
            ``pod5_subdir`` itself, or for mode ``bam`` follows the run's two BAM folders).
        pod5_subdir: the run's ``realtime.pod5_subdir`` (``""`` = the run folder itself).

    Returns:
        ``picked``, ``exists``; ``use``, the folder to give Core, or None when there is
        no single answer; ``moved``, "up" or "down" when ``use`` is not the pick;
        ``inputs`` and ``bytes``, what Core would read in ``use`` now (a live run
        counts what MinKNOW has written so far); ``run``, the facts of the run ``use``
        belongs to (its name, start, position, flow cell, and the experiment and sample
        it was started under); ``runs``, every run below the pick, newest first, when there is more
        than one or none was picked (for a barcoded run in BAM mode, its barcode
        folders); ``bam``, for a BAM run of MinKNOW's folders, what it does with the
        passed and the failed reads (:func:`_bam_facts`), else None; ``unreadable``, the
        folders the search for runs could not list (the note names them); ``note``, a sentence
        or two for the operator.

    Raises:
        ValueError: a mode Core does not have.
    """
    if mode not in MODE_GLOB:
        raise ValueError(f"mode must be one of {sorted(MODE_GLOB)}, not {mode!r}")
    searched_default = not (path and str(path).strip())
    picked = Path(str(path).strip()) if not searched_default else None
    if picked is None:
        root = default_data_folder()
        if root is None:
            return {"picked": None, "exists": False, "use": None, "moved": None,
                    "inputs": 0, "bytes": 0, "run": None, "runs": [], "bam": None,
                    "unreadable": [],
                    "note": "MinKNOW has no default data folder on this platform."}
        picked = Path(root)
    out: dict[str, Any] = {"picked": str(picked), "exists": picked.is_dir(), "use": None,
                           "moved": None, "inputs": 0, "bytes": 0, "run": None,
                           "runs": [], "bam": None, "unreadable": [], "note": None}
    if not out["exists"]:
        out["note"] = (f"MinKNOW's data folder ({picked}) is not on this computer."
                       if searched_default else f"{picked} is not a folder.")
        return out

    want = MODE_GLOB[mode]
    live_bam = live and mode == "bam"

    def settle(use: Path, run: Optional[Path], moved: Optional[str]) -> dict[str, Any]:
        out["use"], out["moved"] = str(use), moved
        if live and not live_bam:
            out["inputs"], out["bytes"] = _count(
                use / pod5_subdir if pod5_subdir else use, want)
        else:
            out["inputs"], out["bytes"] = _n_inputs(use, mode)
        out["run"] = _run_facts(run) if run is not None else None
        if mode == "bam":
            out["bam"], out["note"] = _bam_facts(use)
        if live_bam:
            # The run folder follows both folders; its bam_pass/ or bam_fail/, that one
            # only, as offline. Which, and what is in each so far, is said either way.
            # The choice is offered from the start, before MinKNOW has written a failed
            # read: a live run is set up before its files exist.
            top = run if run is not None else use
            passed = dict(zip(("inputs", "bytes"), _count(top / BAM_SUBDIR, want)))
            failed = dict(zip(("inputs", "bytes"), _count(top / BAM_FAIL_SUBDIR, want)))
            n, m = passed["inputs"], failed["inputs"]
            only = use.name if use != top else None
            out["bam"] = {"pass": {"dir": str(top / BAM_SUBDIR), **passed},
                          "fail": {"dir": str(top / BAM_FAIL_SUBDIR), **failed},
                          "pass_included": only != BAM_FAIL_SUBDIR,
                          "fail_included": only != BAM_SUBDIR,
                          # bam_fail/ by itself has no switch: the choice is about the
                          # failed reads, and there they are what is followed.
                          "switch": (str(top / BAM_SUBDIR) if only is None
                                     else str(top) if only == BAM_SUBDIR else None)}
            so_far = lambda k: _files(k) if k else "no BAM file"  # noqa: E731
            if only is None:
                follows = (f"Follows {BAM_SUBDIR}/ and {BAM_FAIL_SUBDIR}/ together, the "
                           f"passed and the failed reads")
                out["note"] = (f"{follows}: so far {so_far(n)} in {BAM_SUBDIR}/ and "
                               f"{m or 'none'} in {BAM_FAIL_SUBDIR}/." if n or m else
                               f"{follows}. MinKNOW has written no BAM file there yet.")
            elif only == BAM_SUBDIR:
                out["note"] = (f"Follows {BAM_SUBDIR}/ only, the passed reads: so far "
                               f"{so_far(n)}. {BAM_FAIL_SUBDIR}/ ({m or 'none'} so far) is "
                               f"not followed. MinKNOW fails a read on its qscore, so this "
                               f"is a qscore filter. To include them, pick the run folder: "
                               f"{top}")
            else:
                out["note"] = (f"Follows {BAM_FAIL_SUBDIR}/ only, the reads MinKNOW failed "
                               f"on their qscore: so far {so_far(m)}. {BAM_SUBDIR}/ "
                               f"({n or 'none'} so far) is not followed. To include them, "
                               f"pick the run folder: {top}")
            barcodes = barcode_folders(top)
            if barcodes:
                # One barcode is one sample; a live run of this folder would pool them.
                out["use"] = None
                out["note"] = (
                    f"This run is barcoded: {BAM_SUBDIR}/ holds one folder per barcode "
                    f"({', '.join(f.name for f in barcodes[:6])}"
                    f"{', ...' if len(barcodes) > 6 else ''}). A live run follows one of "
                    f"them: start it from the command line with --mode bam --barcode "
                    f"<name>.")
        return out

    def barcoded(run: Path) -> bool:
        """A run folder whose BAM files are all in barcode folders (BAM mode only)."""
        return (mode == "bam" and not _n_inputs(run, mode)[0]
                and bool(barcode_folders(run)))

    def offer_barcodes(run: Path) -> dict[str, Any]:
        """The answer for a barcoded run in an offline BAM run: its barcode folders to
        pick from. One barcode is one sample: they are offered, never read together."""
        barcodes = barcode_folders(run)
        facts = _run_facts(run)
        out["runs"] = [{**facts, "path": str(f), "name": f.name, "use": str(f),
                        "experiment": None, "sample": None,
                        "inputs": _count(f, want)[0]} for f in barcodes[:MAX_RUNS]]
        out["note"] = (
            f"This run is barcoded: {BAM_SUBDIR}/ holds one folder per barcode "
            f"({', '.join(f.name for f in barcodes[:6])}"
            f"{', ...' if len(barcodes) > 6 else ''}). Pick one; barcodes are not "
            f"read together.")
        return out

    # The pick is what Core reads.
    if live and _is_run(picked, mode, live, pod5_subdir):
        return settle(picked, picked, None)
    if not live and _count(picked, want)[0]:
        return settle(picked, _run_above(picked, mode, live, pod5_subdir), None)
    # A live BAM run of one of the run's two BAM folders follows that folder only.
    if (live_bam and picked.name in (BAM_SUBDIR, BAM_FAIL_SUBDIR)
            and _is_run(picked.parent, mode, live, pod5_subdir)):
        return settle(picked, picked.parent, None)
    # One level off: the inputs' own folder picked for a live run, or a run folder for
    # a POD5 or BAM run. A BAM run is given the run folder itself, so nothing moves.
    if live and not live_bam and picked.name == pod5_subdir and _is_run(
            picked.parent, mode, live, pod5_subdir):
        return settle(picked.parent, picked.parent, "up")
    if not live and _is_run(picked, mode, live, pod5_subdir):
        inner = _input_dir_of_run(picked, mode, live, pod5_subdir)
        if _n_inputs(inner, mode)[0]:
            return settle(inner, picked, None if inner == picked else "down")
        if barcoded(picked):
            return offer_barcodes(picked)
    # A barcoded run's bam_pass/ or bam_fail/: it holds no BAM itself, only the barcode
    # folders, so the answer is the run's. It was "No BAM files here".
    if (not live and mode == "bam" and picked.name in (BAM_SUBDIR, BAM_FAIL_SUBDIR)
            and barcoded(picked.parent)):
        return offer_barcodes(picked.parent)
    # Further up: the runs below. A barcoded run has no BAM a run of its folder would
    # read, and is still a run to list: left out, the folder above it read as empty.
    unreadable: list[Path] = []
    runs = _newest_first(_runs_below(picked, mode, live, pod5_subdir, unreadable))
    out["unreadable"] = [str(p) for p in unreadable]
    if not live:
        runs = [r for r in runs
                if _inputs_of_run(r, mode, live, pod5_subdir)[0] or barcoded(r)]
    if len(runs) == 1 and not searched_default:
        run = runs[0]
        if not live and barcoded(run):
            return offer_barcodes(run)
        return settle(_input_dir_of_run(run, mode, live, pod5_subdir), run, "down")
    listed = []
    for run in runs[:MAX_RUNS]:
        facts = _run_facts(run)
        facts["use"] = str(_input_dir_of_run(run, mode, live, pod5_subdir))
        facts["inputs"] = _inputs_of_run(run, mode, live, pod5_subdir)[0]
        if not live and barcoded(run):
            # Picking it offers its barcodes. The count is their files together, so the
            # row does not read as an empty run; no run reads them together.
            folders = barcode_folders(run)
            facts["barcodes"] = len(folders)
            facts["inputs"] = sum(_count(f, want)[0] for f in folders)
        listed.append(facts)
    out["runs"] = listed
    kind = "POD5" if (live or mode == "pod5") else "BAM"
    if listed:
        out["note"] = (f"{len(runs)} MinKNOW runs here"
                       f"{f' (the {MAX_RUNS} newest are listed)' if len(runs) > MAX_RUNS else ''}"
                       f": pick one.") + _same_names(listed)
        if any(r.get("barcodes") for r in listed):
            out["note"] += (" A barcoded run is listed with the files of all its barcodes; "
                            "picking it offers the barcodes, which are not read together.")
    else:
        # Name the folder searched: Find leaves the input field empty, so "this folder"
        # named nothing the operator could see (2026-11-23).
        where = f"MinKNOW's data folder ({picked})" if searched_default else str(picked)
        if live:
            holds = BAM_SUBDIR if live_bam else (pod5_subdir or "POD5")
            out["note"] = (f"No MinKNOW run folder (one that holds {holds}/) in {where} or "
                           f"up to {SEARCH_DEPTH} levels below it.")
        else:
            out["note"] = f"No {kind} files in {where}, or in a MinKNOW run below it."
        if searched_default:
            out["note"] += (" If MinKNOW writes its runs somewhere else, pick that folder "
                            "with Browse.")
    if unreadable:
        n = len(unreadable)
        shown = ", ".join(str(p) for p in unreadable[:2]) + (", ..." if n > 2 else "")
        out["note"] += (f" {n} folder{'' if n == 1 else 's'} there could not be read by "
                        f"this account, so a run inside {'it' if n == 1 else 'them'} was "
                        f"not seen: {shown}.")
    return out
