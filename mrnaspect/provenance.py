"""Provenance stamps for result files, so a result records what produced it.

The stamp holds the time, the command line, the git commit and dirty flag, a blob hash
of every loaded project module, the software version, the seed, and optionally the
inputs, parameters and external tool versions. Standard library only. Git capture is
best effort. In a built product (the desktop app) the code is a copy and git has no
answer for it: ``git_commit`` and ``git_dirty`` are then what was recorded when the
product was built (:func:`build_record`), and ``None`` when nothing was recorded.

Usage::

    from mrnaspect.provenance import write_result_json

    write_result_json(
        out_path,
        payload={"pooled_error_rate": rate},
        seed=seed,
        inputs=[bam_path, ref_path],
        params={"min_depth": 50},
    )

writes ``{**payload, "provenance": {...}}``.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import sysconfig
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

# Git queries for Core's own commit run from this package's directory, not the cwd.
_REPO_ANCHOR = Path(__file__).resolve().parent

# The file in which an installed Core finds the commit it was built from. Written into
# the installed package when the desktop app's environment is staged
# (scripts/bundle_build_record.py). The source tree never holds one (.gitignore, and a
# test), so in a checkout git is asked.
_BUILD_RECORD_NAME = "_build.json"
_FULL_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _find_repo_root(start: Path) -> Path:
    """Walk up to the directory holding ``.git``; fall back to the package's parent.

    The fallback (an installed Core) makes recorded paths read ``mrnaspect/...``.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parent


_REPO_ROOT = _find_repo_root(_REPO_ANCHOR)


def _interpreter_roots() -> tuple[Path, ...]:
    """Directories holding the interpreter, stdlib and third-party code, resolved.

    Excluded from the code stamp even when inside a repository (an environment kept in
    the project directory); their versions belong in the environment lockfile.
    """
    roots = {Path(sys.prefix), Path(sys.base_prefix)}
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = sysconfig.get_paths().get(key)
        if p:
            roots.add(Path(p))
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:  # unresolvable prefix; nothing to exclude by it
            continue
    return tuple(out)


_INTERPRETER_ROOTS = _interpreter_roots()

try:  # package context; None when this file is imported on its own
    from . import __version__ as _SOFTWARE_VERSION
except Exception:  # pragma: no cover - only when imported outside the package
    _SOFTWARE_VERSION = None


def _git_at(cwd: Path, *args: str) -> str | None:
    """Run a git command in ``cwd``; ``None`` on any failure.

    Best effort: no git, no repository or a non-zero exit gives ``None``, so a missing
    commit is a recorded gap rather than a lost result. ``cwd`` is a parameter because
    the loaded code can come from more than one repository.

    git is not started at all unless ``cwd`` is inside a repository (a ``.git`` in it or
    above it). Code installed in the desktop app is in none, and on a Mac without Apple's
    command line developer tools ``/usr/bin/git`` is a stand-in that, when run, opens a
    system dialog offering to install them: the report asks for the commit at every
    render, so the app would have raised that dialog again and again.
    """
    if _root_for(Path(cwd)) is None:
        return None
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _run_git(*args: str) -> str | None:
    """``_git_at`` anchored to Core's own repo — what ``git_commit``/``git_is_dirty`` use."""
    return _git_at(_REPO_ANCHOR, *args)


@functools.lru_cache(maxsize=None)
def _read_build_record(path: Path) -> dict[str, Any] | None:
    """The build record at ``path``, or ``None`` when there is none or it is not one.

    A file that is there and is not a record is a defect of the build, which the bundle
    check reports; here it is logged and treated as absent, so a result is still written,
    with no commit, rather than lost at its last step.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("provenance: cannot read %s: %s", path, exc)
        return None
    try:
        record = json.loads(text)
    except ValueError as exc:
        logger.warning("provenance: %s is not JSON (%s); no commit is recorded", path, exc)
        return None
    commit = record.get("commit") if isinstance(record, dict) else None
    if (not isinstance(commit, str) or not _FULL_COMMIT.match(commit)
            or not isinstance(record.get("dirty"), bool)):
        logger.warning("provenance: %s is not a build record; no commit is recorded", path)
        return None
    staged_at = record.get("staged_at")
    return {"commit": commit, "dirty": record["dirty"],
            "staged_at": staged_at if isinstance(staged_at, str) else None}


def build_record() -> dict[str, Any] | None:
    """What was recorded about this Core when it was built into a product, or ``None``.

    ``{"commit", "dirty", "staged_at"}``: the commit of the checkout the desktop app's
    environment was staged from, whether that checkout's tracked files differed from the
    commit, and when. It answers "which build made this result" for code that is in no
    repository; the version number does not, since many builds share one.

    The record lies inside the installed package, so it describes that copy and nothing
    else, and where there is one it is believed before git. That order matters: a staged
    environment and a built app lie inside the checkout that built them, where git would
    answer for the checkout as it is now, which is not the code that was copied.

    Returns:
        The record, or ``None`` when there is none: a checkout (no record is ever
        written into the source tree, and git is asked), or a product built from a
        source with no commit to give.
    """
    return _read_build_record(_REPO_ANCHOR / _BUILD_RECORD_NAME)


def git_commit(short: bool = False) -> str | None:
    """Core's commit hash, or ``None`` if unavailable.

    In a checkout: what git says now. In a built product: the commit it was built from
    (:func:`build_record`); ``short`` is then its first seven characters.
    """
    built = build_record()
    if built is not None:
        return built["commit"][:7] if short else built["commit"]
    return _run_git("rev-parse", "--short", "HEAD") if short else _run_git(
        "rev-parse", "HEAD"
    )


def git_is_dirty() -> bool | None:
    """Whether Core's tracked files differ from its commit; ``None`` if unknown.

    Untracked files (data, results) are ignored. In an installed product: whether they
    differed when it was built (:func:`build_record`).
    """
    built = build_record()
    if built is not None:
        return built["dirty"]
    status = _run_git("status", "--porcelain", "--untracked-files=no")
    if status is None:
        return None
    return status != ""


def git_blob_hash(path: str | os.PathLike[str]) -> str | None:
    """Git-compatible blob SHA-1 of a file's exact bytes; ``None`` if unreadable.

    Computed as git does, ``sha1(b"blob <size>\\0" + content)``, so it is the object id
    git stores that content under: once committed anywhere, ``git cat-file blob <hash>``
    retrieves it. Unlike ``git_commit``, it identifies the code that ran even if the
    file was committed only after the run. Pure ``hashlib``, so it works without git.

    Note:
        SHA-1 is used because that is git's object id, not as a security
        primitive; this is an identity/reproducibility check, not a defence
        against a crafted collision.
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        logger.warning("provenance: cannot hash %s: %s", p, exc)
        return None
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


@functools.lru_cache(maxsize=None)
def _root_for(directory: Path) -> Path | None:
    """The git repository ``directory`` belongs to, or ``None`` if it is in none.

    Looked up per module: loaded code can come from more than one repository.
    """
    for candidate in (directory, *directory.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _scan_modules() -> tuple[dict[str, str | None], dict[Path, list[str]]]:
    """Walk ``sys.modules`` once; return ``(code, {root: [keys]})``.

    Shared by :func:`capture_code_versions` and :func:`capture_code_roots` so the two
    halves of the record cannot disagree about which files were seen.
    """
    seen: list[tuple[Path, Path, Path]] = []  # (root, relpath, resolved)
    # list(): a lazy import during iteration would otherwise mutate sys.modules.
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f or not f.endswith(".py"):
            continue
        try:
            resolved = Path(f).resolve()
        except OSError:
            continue
        if any(resolved.is_relative_to(r) for r in _INTERPRETER_ROOTS):
            continue  # stdlib / site-packages, even when installed inside a repo
        # Outside any repository (an installed Core), fall back to _REPO_ROOT.
        root = _root_for(resolved.parent) or (
            _REPO_ROOT if resolved.is_relative_to(_REPO_ROOT) else None)
        if root is None:
            continue  # not project code
        try:
            rel = resolved.relative_to(root)
        except ValueError:  # pragma: no cover - root always contains resolved
            continue
        seen.append((root, rel, resolved))

    # A relative path present in two repositories (e.g. `conftest.py`) is qualified
    # with its repository, so neither entry overwrites the other.
    counts: dict[str, set[Path]] = {}
    for root, rel, _ in seen:
        counts.setdefault(str(rel), set()).add(root)

    code: dict[str, str | None] = {}
    by_root: dict[Path, list[str]] = {}
    for root, rel, resolved in seen:
        key = str(rel)
        if len(counts[key]) > 1:
            key = f"{root.name}::{key}"
        code[key] = git_blob_hash(resolved)
        by_root.setdefault(root, []).append(key)
    return dict(sorted(code.items())), {r: sorted(k) for r, k in by_root.items()}


def capture_code_versions() -> dict[str, str | None]:
    """Blob-hash every project module currently loaded, keyed by repo-relative path.

    Every loaded module inside a git repository (or Core's own root), not just the
    entry script: a helper it imports can change the numbers too. Third-party and
    stdlib modules are excluded; their versions belong in ``tools`` or the environment
    lockfile.

    Returns:
        ``{repo_relative_path: blob_hash}``, sorted by path. A path present in two
        repositories is keyed ``<repo-dir>::<path>``. A value is ``None`` when the
        file could not be read. Each repository's commit is in
        :func:`capture_code_roots`.
    """
    return _scan_modules()[0]


def capture_code_roots() -> dict[str, dict[str, Any]]:
    """The repositories the hashed modules came from, each with its commit and dirty flag.

    A script in one repository that imports Core from another is pinned by both;
    the stamp's ``git_commit``/``git_dirty`` are Core's alone (``git_repo``).

    Returns ``{root_path: {"commit", "dirty", "n_files"}}``, sorted by path.
    """
    _, by_root = _scan_modules()
    out: dict[str, dict[str, Any]] = {}
    for root in sorted(by_root, key=str):
        status = _git_at(root, "status", "--porcelain", "--untracked-files=no")
        out[str(root)] = {
            "commit": _git_at(root, "rev-parse", "HEAD"),
            "dirty": None if status is None else status != "",
            "n_files": len(by_root[root]),
        }
    return out


def _describe_input(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Identity of one input file: path, existence, size, mtime.

    Not hashed, as sequencing inputs are large; size and mtime catch a swapped file.
    Hash at the call site if a claim needs it.
    """
    p = Path(path)
    info: dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if info["exists"]:
        st = p.stat()
        info["bytes"] = st.st_size
        info["mtime_utc"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
    return info


def capture_provenance(
    *,
    seed: int | None = None,
    inputs: Iterable[str | os.PathLike[str]] | None = None,
    params: Mapping[str, Any] | None = None,
    tools: Mapping[str, str] | None = None,
    command: str | None = None,
    now: datetime | None = None,
    code: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build the provenance stamp for a result artifact.

    Args:
        seed: Random seed used by the producing run, if any.
        inputs: Input file paths to fingerprint (path/size/mtime; no hashing).
        params: Parameters that define the result (e.g. ``min_depth``,
            ``window_span``). Recorded verbatim.
        tools: External tool versions (e.g. ``{"minimap2": "2.28"}``).
        command: Override for the captured command line. Defaults to the actual
            ``sys.argv`` of the current process.
        now: Override for the timestamp (for deterministic tests). Defaults to
            the current UTC time.
        code: Override for the loaded-module blob hashes (for deterministic
            tests); ``code_roots`` is then None. Defaults to
            :func:`capture_code_versions`.

    Returns:
        A JSON-serializable dict. ``generated_at`` is ISO-8601 UTC; ``command``
        is a copy-pasteable shell string; ``git_commit``/``git_dirty`` describe
        Core's checkout (``git_repo``); in a built product they are the commit it was
        built from, repeated with its time under ``build``, or ``None`` when none was
        recorded; ``code`` maps
        each loaded project module to its blob hash, which is what identifies the
        logic that ran; ``code_roots`` gives each repository's commit.
    """
    stamp_time = now or datetime.now(timezone.utc)
    stamp: dict[str, Any] = {
        "generated_at": stamp_time.isoformat(),
        "command": command if command is not None else shlex.join(sys.argv),
        "argv": list(sys.argv),
        "git_commit": git_commit(),
        "git_dirty": git_is_dirty(),
        # The repository the two fields above describe: Core's.
        "git_repo": str(_REPO_ROOT),
        # Set in a built product, where the two fields above are not git's answer now
        # but what was recorded at the build; None in a checkout.
        "build": build_record(),
        # What identifies the code that ran (see git_blob_hash); the commit is context.
        "code": capture_code_versions() if code is None else dict(code),
        # Every repository the hashed modules came from, each with its own commit.
        "code_roots": capture_code_roots() if code is None else None,
        "software_version": _SOFTWARE_VERSION,
        "seed": seed,
    }
    if inputs is not None:
        stamp["inputs"] = [_describe_input(p) for p in inputs]
    if params is not None:
        stamp["params"] = dict(params)
    if tools is not None:
        stamp["tools"] = dict(tools)
    return stamp


def write_result_json(
    path: str | os.PathLike[str],
    payload: Mapping[str, Any],
    *,
    provenance: Mapping[str, Any] | None = None,
    key: str = "provenance",
    **capture_kwargs: Any,
) -> dict[str, Any]:
    """Write ``payload`` plus a provenance block to ``path``, atomically.

    The result is ``{**payload, key: provenance}``. The file is written to a
    sibling ``*.tmp`` and ``os.replace``'d into place so a crashed/interrupted
    job never leaves a half-written result that could be mistaken for valid.

    Args:
        path: Destination ``.json`` path. Parent dirs are created.
        payload: The result dict, kept flat.
        provenance: Pre-built stamp; if ``None`` one is captured from
            ``capture_kwargs`` (``seed``, ``inputs``, ``params``, ``tools``, …).
        key: Top-level key the stamp is nested under (default ``"provenance"``).
        **capture_kwargs: Forwarded to :func:`capture_provenance` when
            ``provenance`` is not supplied.

    Returns:
        The full record that was written (payload + provenance).

    Raises:
        ValueError: if ``key`` collides with a key already in ``payload``.
    """
    if key in payload:
        raise ValueError(
            f"payload already contains reserved key {key!r}; pass a different "
            "`key=` or rename the payload field"
        )
    if provenance is None:
        provenance = capture_provenance(**capture_kwargs)
    elif capture_kwargs:
        raise ValueError(
            "pass either `provenance=` or capture kwargs, not both"
        )

    record = {**dict(payload), key: dict(provenance)}

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp_path, out_path)  # atomic on POSIX same-filesystem rename
    logger.info("wrote result with provenance to %s", out_path)
    return record


def sidecar_meta_path(result_path: str | os.PathLike[str]) -> Path:
    """Return the ``*.meta.json`` sidecar path for a non-JSON result (e.g. TSV).

    TSV/plot outputs cannot embed metadata, so the convention is a sibling
    ``<name>.<ext>.meta.json`` carrying the same provenance block.
    """
    p = Path(result_path)
    return p.with_name(p.name + ".meta.json")


def write_sidecar_meta(
    result_path: str | os.PathLike[str],
    *,
    provenance: Mapping[str, Any] | None = None,
    **capture_kwargs: Any,
) -> Path:
    """Write a ``*.meta.json`` provenance sidecar next to a non-JSON result.

    Returns the sidecar path. The sidecar records the result's path as
    ``result_file``; the result itself is not read or modified.
    """
    meta_path = sidecar_meta_path(result_path)
    stamp = provenance if provenance is not None else capture_provenance(
        **capture_kwargs
    )
    write_result_json(meta_path, {"result_file": str(Path(result_path))},
                      provenance=stamp)
    return meta_path
