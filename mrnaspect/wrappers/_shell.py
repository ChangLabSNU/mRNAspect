"""Run a wrapper's shell script with its output in a per-stage log, and find tools.

A stage's output goes to a file beside its artifacts rather than to the console, where
tool noise would bury the driver's own log. It is written live, not captured through a
pipe, which would hold a multi-hour stage's output in memory until the stage ended. It
is never discarded: when a stage fails, the tail of its log is logged at ERROR.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Lines of the stage log logged at ERROR when a stage fails.
_FAILURE_TAIL_LINES = 40


def _tail(path: Path, n: int) -> str:
    """The last ``n`` lines of ``path``, or a note saying why they could not be read.

    Never raises: it runs on the failure path and must not mask the original error.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:                      # unreadable/vanished log
        return f"<could not read {path}: {exc}>"
    if not lines:
        return "<stage log is empty>"
    return "\n".join(lines[-n:])


def run_logged(
    cmd: Sequence[str],
    log_path: Path,
    *,
    stage: str,
    **popen_kwargs: Any,
) -> Path:
    """Run ``cmd``, writing its combined stdout+stderr to ``log_path``.

    Args:
        cmd: argv for the child process.
        log_path: file to receive the child's combined output. Truncated per call; its
            parent directory must already exist.
        stage: short human name for this stage ("align", "basecall"), used in messages.
        **popen_kwargs: forwarded to :func:`subprocess.run` (e.g. ``start_new_session``).

    Returns:
        ``log_path``, so a caller can name it in its own messages.

    Raises:
        subprocess.CalledProcessError: the child exited non-zero. The tail of the stage
            log is logged at ERROR first.
    """
    log_path = Path(log_path)
    with open(log_path, "w", encoding="utf-8") as fh:
        try:
            subprocess.run(
                list(cmd), check=True, stdout=fh, stderr=subprocess.STDOUT, **popen_kwargs
            )
        except subprocess.CalledProcessError as exc:
            fh.flush()
            logger.error(
                "%s stage failed (exit %s). Last %d line(s) of %s:\n%s",
                stage, exc.returncode, _FAILURE_TAIL_LINES, log_path,
                _tail(log_path, _FAILURE_TAIL_LINES),
            )
            raise
    logger.debug("%s stage output -> %s", stage, log_path)
    return log_path


def _usable(path: Path) -> Optional[str]:
    """Why ``path`` cannot be run as a command, or ``None`` if it can.

    A directory is named as such: the ONT bundle unpacks to one, and pointing a setting
    at it is the usual mistake.
    """
    if path.is_dir():
        return ("that is a directory, not a program. The ONT bundle unpacks to a "
                "directory; the binary is usually under its bin/ subdirectory.")
    if not path.exists():
        return "that path does not exist."
    if not os.access(path, os.X_OK):
        return "that file is not executable (chmod +x, or point at the right file)."
    return None


# The ONT bundle ships these two in one bin/, and their names differ by one word. One
# set where the other belongs passes every check in `_usable`, and would fail only
# later, as the started program rejecting the other one's flags.
_SWAPPABLE = {
    "dorado_basecall_server": "ont_basecall_client",
    "ont_basecall_client": "dorado_basecall_server",
}


def _wrong_tool(name: str, path: Path) -> Optional[str]:
    """Why ``path`` is the OTHER bundle binary, or ``None``.

    Only the companion's exact filename matches, so a wrapper script, a symlink or a
    version-suffixed binary still passes.
    """
    other = _SWAPPABLE.get(name)
    if other is not None and path.name == other:
        return (f"that is {other}, not {name}. Both live in the ONT bundle's bin/ and "
                f"the names are one word apart; point this setting at {name} instead.")
    return None


def resolve_tool(name: str, override: Optional[str] = None, *,
                 env_var: Optional[str] = None,
                 sibling_of: Optional[str] = None,
                 hint: str = "") -> str:
    """Path to an external command.

    Tried in order, most specific first:

    1. ``override``: an explicit path from config or a caller.
    2. ``$<env_var>``, when ``env_var`` is given.
    3. ``<sys.prefix>/bin/<name>``: the running interpreter's environment, found even
       when it is not on PATH (as under an installed ``mrnaspect`` console script).
    4. ``PATH``.
    5. Beside ``sibling_of``, when given: in a companion binary's directory, or in a
       bundle directory and its ``bin/``.

    An explicit setting (1 or 2) that is unusable raises instead of falling back.

    Raises:
        FileNotFoundError: an explicit setting is unusable, or nothing was found. The
            message lists every place tried.
    """
    if override:
        path = Path(override)
        why = _usable(path) or _wrong_tool(name, path)
        if why:
            raise FileNotFoundError(
                f"{name} was explicitly configured as {path}, and {why} An explicit "
                f"setting is not overridden by a fallback — fix the path or remove the "
                f"setting to let it be discovered.")
        return str(path)

    tried = []
    if env_var:
        from os import environ
        value = environ.get(env_var)
        if value:
            path = Path(value)
            why = _usable(path) or _wrong_tool(name, path)
            if not why:
                return str(path)
            raise FileNotFoundError(
                f"{env_var}={value} but {why} An environment variable is an explicit "
                f"setting, so it is not silently fallen back from.")
        tried.append(f"${env_var} (unset)")

    in_env = Path(sys.prefix) / "bin" / name
    if in_env.exists():
        return str(in_env)
    tried.append(str(in_env))

    on_path = shutil.which(name)
    if on_path:
        return on_path
    tried.append("PATH")

    # Last, and only when the caller names a companion: a vendor bundle ships its
    # binaries in one directory (ont_basecall_client beside dorado_basecall_server).
    if sibling_of:
        base = Path(sibling_of)
        # The companion may be a binary or the bundle directory. For a directory, look
        # inside it and its bin/; its .parent is wherever the bundle was unpacked.
        roots = [base, base / "bin"] if base.is_dir() else [base.parent]
        for root in roots:
            candidate = root / name
            if candidate.exists():
                logger.info("%s resolved at %s (companion of %s)",
                            name, candidate, sibling_of)
                return str(candidate)
            # The path itself, which a reader can check.
            tried.append(str(candidate))

    raise FileNotFoundError(
        f"could not find {name!r}. Looked at: {', '.join(tried)}."
        + (f" {hint}" if hint else
           " Install it, or set an explicit path in the config."))
