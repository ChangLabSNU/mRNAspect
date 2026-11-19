"""Basecaller wrapper: POD5 -> a directory of unaligned BAM chunks, via run_basecall.sh.

Basecalling (GPU, dorado-server) and alignment (CPU, minimap2; ``aligner.py``) are
separate stages. The driver caches each POD5's basecall output, so a restart does not
basecall it again, and always re-aligns from it, so an alignment is never stale against
the reference or the flags.

The basecall child runs in its own session: a terminal Ctrl-C signals the foreground
process group, and it is the driver that decides when to stop (after the current input).

A fine-tuned model is applied by passing a different ``model``: an exported dorado model
folder, which dorado-server loads with ``--model``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import os

from ._shell import resolve_tool, run_logged

# The script sits beside this file.
_BASECALL_SCRIPT = Path(__file__).resolve().parent / "run_basecall.sh"

# Any callable (pod5_path, out_dir) -> unaligned BAM (ubam) directory; reference-free.
Basecaller = Callable[[Path, Path], Path]


def shell_basecall(pod5_path: Path, out_dir: Path, *, model: str | None = None,
                   client: str | None = None,
                   server_binary: str | None = None,
                   emit_moves: bool = False) -> Path:
    """Basecall ``pod5_path`` with the dorado-server; return the ``basecall_<prefix>/`` dir.

    ``model`` is the server's ``--model`` value: an absolute path to a stock or exported
    fine-tuned model folder. Without one the script exits with an error. ``emit_moves``
    also writes the move table (``mv`` tag), which nothing here reads. The child runs
    in a new session, so a foreground Ctrl-C does not interrupt an in-flight basecall.

    The script's output is written live to ``basecall_<prefix>.log`` beside the ubam
    dir, not to the console, so a long basecall can be followed with ``tail -f``.

    Raises:
        FileNotFoundError: the basecall script or ``ont_basecall_client`` cannot be
            found, or no basecall output dir was produced.
        subprocess.CalledProcessError: the basecall script exited non-zero.
    """
    if not _BASECALL_SCRIPT.exists():
        raise FileNotFoundError(f"basecall script not found: {_BASECALL_SCRIPT}")
    # Resolved here and passed to the script as $DORADO_CLIENT. ONT ships the client in
    # the server's bin/, so ``server_binary`` alone is enough to find it.
    try:
        client_path = resolve_tool(
            "ont_basecall_client", client, env_var="DORADO_CLIENT",
            sibling_of=server_binary,
            hint="it ships inside the ONT dorado-server bundle, which is proprietary and "
                 "cannot be bundled here. Set basecall.server.binary to the server in "
                 "that bundle (the client is found beside it), set "
                 "basecall.server.client explicitly, or export $DORADO_CLIENT.")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"basecall cannot start: {exc}") from exc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = pod5_path.stem
    cmd = ["bash", str(_BASECALL_SCRIPT), str(pod5_path), prefix, str(out_dir)]
    if model is not None:
        cmd.append(str(model))
    run_logged(
        cmd,
        out_dir / f"basecall_{prefix}.log",
        stage="basecall",
        start_new_session=True,  # isolate from the foreground process group (abort)
        env={**os.environ, "DORADO_CLIENT": client_path,
             "EMIT_MOVES": "1" if emit_moves else "0"},
    )
    basecall_dir = out_dir / f"basecall_{prefix}"
    if not basecall_dir.is_dir():
        raise FileNotFoundError(f"Expected basecall dir not produced: {basecall_dir}")
    return basecall_dir
