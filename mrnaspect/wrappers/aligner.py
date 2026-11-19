"""Aligner wrapper: basecalled reads in BAM -> a sorted, indexed BAM, via run_align.sh.

Aligns every BAM in the ``basecall_<prefix>/`` directory to the reference, with the
configured minimap2 flags: the chunks the basecaller wrote (pod5 mode), or a link to the
input BAM (bam mode), whose own alignment, if any, is discarded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import os

from ._shell import resolve_tool, run_logged

# The script sits beside this file.
_ALIGN_SCRIPT = Path(__file__).resolve().parent / "run_align.sh"

# Any callable (prefix, reference, out_dir) -> aligned BAM path. It reads the
# basecall_<prefix>/ dir under out_dir.
Aligner = Callable[[str, Path, Path], Path]

# -a: write SAM. Without it minimap2 writes PAF, which samtools cannot read.
# -y: copy the FASTQ comment, which holds the basecaller tags, into the SAM. Without it
#     every read aligns and pt, RG and the rest are lost.
REQUIRED_MINIMAP2_FLAGS = ("-a", "-y")
# minimap2's short options that take no value (`minimap2 --help`).
_MINIMAP2_NO_VALUE = frozenset("HXaLcYy")


def _short_options(flags: Optional[str]) -> set[str]:
    """The short options a minimap2 flag string sets, as letters.

    Split on whitespace, as run_align.sh's unquoted expansion splits it. In a cluster
    such as ``-ax`` the letters count up to the first option that takes a value, which
    takes the rest of the token, or the next token if nothing is left.
    """
    seen: set[str] = set()
    tokens = (flags or "").split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token.startswith("--") or not token.startswith("-"):
            continue
        for j, letter in enumerate(token[1:], start=1):
            seen.add(letter)
            if letter in _MINIMAP2_NO_VALUE:
                continue
            if j == len(token) - 1:
                i += 1  # its value is the next token
            break
    return seen


def missing_minimap2_flags(flags: str) -> list[str]:
    """The flags of ``REQUIRED_MINIMAP2_FLAGS`` that ``flags`` does not set."""
    seen = _short_options(flags)
    return [flag for flag in REQUIRED_MINIMAP2_FLAGS if flag[1] not in seen]


def minimap2_sets_threads(flags: Optional[str]) -> bool:
    """Whether the flag string sets minimap2's thread count (``-t``)."""
    return "t" in _short_options(flags)


def available_cpus() -> int:
    """The CPUs this process may run on: its affinity where the OS reports one."""
    process_count = getattr(os, "process_cpu_count", None)   # Python 3.13+
    count = process_count() if process_count else None
    if not count and hasattr(os, "sched_getaffinity"):
        count = len(os.sched_getaffinity(0))
    return max(1, count or os.cpu_count() or 1)


def resolve_align_threads(threads: Any, flags: Optional[str]) -> Optional[int]:
    """minimap2's ``-t`` from ``align.threads``; None when the flags set it themselves.

    Null means every CPU this process may use: minimap2's own default is 3, and in bam
    mode alignment is most of the run. A ``-t`` in ``align.minimap2_flags`` decides
    instead, and both at once are refused rather than one silently winning.

    Raises:
        ValueError: ``threads`` is not a whole number of 1 or more, or both are set.
    """
    in_flags = minimap2_sets_threads(flags)
    if threads is None:
        return None if in_flags else available_cpus()
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError(
            f"align.threads must be a whole number of 1 or more, or null for every CPU, "
            f"got {threads!r}")
    if in_flags:
        raise ValueError(
            f"align.threads ({threads}) and the -t in align.minimap2_flags ({flags!r}) "
            f"both set minimap2's threads; keep one")
    return threads


def shell_align(
    prefix: str,
    reference: Path,
    out_dir: Path,
    *,
    align_flags: str | None = None,
    emit_moves: bool = False,
    threads: Optional[int] = None,
    minimap2: str | None = None,
    samtools: str | None = None,
) -> Path:
    """Align the BAMs in ``basecall_<prefix>/`` under ``out_dir`` to ``reference``.

    ``align_flags`` is the minimap2 flag string (``align.minimap2_flags``); when omitted
    the script uses its own default, ``-ax map-ont -k7 -w5 --secondary=no -y``.
    ``emit_moves`` (``basecall.emit_moves``) carries the move table: a BAM basecalled
    elsewhere with ``--emit-moves`` has it on every read, and it about doubles the align
    time and the BAM's size. ``threads`` is minimap2's ``-t`` (``resolve_align_threads``);
    None leaves minimap2 its own default. ``out_dir`` must hold ``basecall_<prefix>/``.

    The output of the script and its tools goes to ``align_<prefix>.log`` beside the
    BAM, not to the console.

    Raises:
        FileNotFoundError: the align script, minimap2 or samtools cannot be found, or
            the aligned BAM was not produced.
        subprocess.CalledProcessError: the align script exited non-zero.
    """
    if not _ALIGN_SCRIPT.exists():
        raise FileNotFoundError(f"align script not found: {_ALIGN_SCRIPT}")
    out_dir = Path(out_dir)
    # Resolved here and passed as $MINIMAP2 / $SAMTOOLS: through sys.prefix, Python
    # finds the environment's own tools even when PATH does not include them.
    tools = {"MINIMAP2": resolve_tool("minimap2", minimap2),
             "SAMTOOLS": resolve_tool("samtools", samtools),
             "EMIT_MOVES": "1" if emit_moves else "0",
             "ALIGN_THREADS": "" if threads is None else str(int(threads))}
    cmd = ["bash", str(_ALIGN_SCRIPT), prefix, str(reference), str(out_dir)]
    if align_flags is not None:
        cmd.append(str(align_flags))
    # A new session, as for basecall: a terminal Ctrl-C would otherwise kill the align
    # mid-run, and under `on_error: skip` a good input would be dropped from the report.
    run_logged(cmd, out_dir / f"align_{prefix}.log", stage="align",
               start_new_session=True, env={**os.environ, **tools})
    bam = out_dir / f"aligned_{prefix}.bam"
    if not bam.exists():
        raise FileNotFoundError(f"Expected aligned BAM not produced: {bam}")
    return bam
