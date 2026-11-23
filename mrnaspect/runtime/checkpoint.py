"""Basecall cache: a POD5 name -> its basecall output (a directory of unaligned BAMs).

In pod5 mode basecalling is the expensive GPU step. Once a POD5's basecall output is
complete on disk the driver records it, and a restart reuses it instead of basecalling
again. Every input is still re-aligned and re-ingested, so a restarted run's report is
complete; the cache only saves the basecall. bam mode does not use it.

An entry is one line of five tab-separated fields::

    pod5_name  ubam_dir  sha256 of the POD5  model name  sha256 of the model folder

The last three say what the basecall was made from. A file name alone does not: a POD5
replaced under the same name, or a run continued with another model, would otherwise be
served the earlier basecall. ``verified`` serves an entry only when the file and the
model are the ones recorded, and raises :class:`StaleBasecallError` when either differs,
so the run stops instead of mixing two inputs or two models in one report.

An entry is written only after the basecall output is complete, so a ``kill -9``
mid-basecall leaves no entry. Entries are appended one line at a time. A malformed line
(one torn by a ``kill -9``, say), an entry whose directory has vanished, or an entry with
only the first two fields (written before the others existed) is a cache miss, and that
POD5 is basecalled again; none of these is an error.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_NAME = "basecall_cache.tsv"

#: The digest field of a model that is not a folder here (a name the server resolves).
NO_DIGEST = "-"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FRESH_RUN = (
    "Start a fresh run for it: a new output folder (on the command line, a new "
    "--output-name, or neither --output-name nor --continue-latest).")

#: (name, digest) of a basecall model, from :func:`model_fingerprint`.
ModelFingerprint = tuple[str, str]


class StaleBasecallError(ValueError):
    """A cached basecall was made from another file, or with another model."""


@dataclass(frozen=True)
class _Entry:
    ubam: str
    input_sha256: Optional[str] = None
    model_name: Optional[str] = None
    model_sha256: Optional[str] = None

    @property
    def checkable(self) -> bool:
        return self.input_sha256 is not None


def file_sha256(path: Path | str, *, chunk_bytes: int = 1 << 20) -> str:
    """The sha256 of a file's contents, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def _one_field(text: str) -> str:
    """``text`` with nothing that would split a line or a field of the cache file."""
    return text.replace("\t", " ").replace("\n", " ").replace("\r", " ")


#: Files a file manager writes into a folder it has shown, by name. With the hidden
#: files (a name starting with "."), they are not the model's: no basecaller reads them.
_FILE_MANAGER_NAMES = frozenset({"Thumbs.db", "desktop.ini", "__MACOSX"})


def _is_model_file(rel: Path) -> bool:
    """Whether a path inside a model folder is one of the model's own files.

    Not a hidden file or anything in a hidden folder, and not a file manager's own
    record of the folder: Finder writes ``.DS_Store`` into a folder it has opened and
    ``._name`` beside a file copied from another file system, Explorer ``Thumbs.db`` and
    ``desktop.ini``. Counted, one of these made an unchanged model read as changed, and
    the run it had basecalled could not be continued.
    """
    return not any(part.startswith(".") or part in _FILE_MANAGER_NAMES
                   for part in rel.parts)


def model_fingerprint(model: Path | str) -> ModelFingerprint:
    """What identifies a basecall model: its name, and a digest of its files.

    For a model folder, the folder's own name (links resolved) and a sha256 over every
    file's path inside it and that file's contents. The name is part of it because it
    is what the basecaller writes into each read's read group; the digest, because a
    folder can be re-exported under the same name. A folder that only moved keeps both.
    Hidden files and a file manager's own files are left out (:func:`_is_model_file`).
    A value that is not a folder here is a name the server resolves itself, and is
    compared as given, with ``NO_DIGEST``.

    Raises:
        ValueError: the folder cannot be read.
    """
    path = Path(model)
    if not path.is_dir():
        return _one_field(str(model)), NO_DIGEST
    digest = hashlib.sha256()
    try:
        files = sorted((p.relative_to(path).as_posix(), p)
                       for p in path.rglob("*")
                       if p.is_file() and _is_model_file(p.relative_to(path)))
        for rel, p in files:
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(file_sha256(p)))
    except OSError as exc:
        raise ValueError(f"The model folder {path} could not be read: {exc}") from exc
    return _one_field(path.resolve().name or str(path)), digest.hexdigest()


class Checkpoint:
    """POD5 name -> cached ubam directory and what it was basecalled from."""

    def __init__(self, state_file: Path | str) -> None:
        self.state_file = Path(state_file)
        self._map: dict[str, _Entry] = self._load()

    @staticmethod
    def _parse(line: str) -> Optional[tuple[str, _Entry]]:
        """One line as (name, entry), or None for a line that is not a whole entry."""
        parts = line.split("\t")
        if len(parts) == 2 and all(parts):
            return parts[0], _Entry(parts[1])
        if len(parts) == 5 and all(parts) and _SHA256.fullmatch(parts[2]) and (
                parts[4] == NO_DIGEST or _SHA256.fullmatch(parts[4])):
            return parts[0], _Entry(parts[1], parts[2], parts[3], parts[4])
        return None

    def _load(self) -> dict[str, _Entry]:
        """Read the map (empty if the file is absent); malformed lines are skipped."""
        mapping: dict[str, _Entry] = {}
        if not self.state_file.exists():
            return mapping
        for line in self.state_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parsed = self._parse(line)
            if parsed is None:
                # E.g. a line torn by a kill -9 mid-append: basecalled again.
                logger.warning("Ignoring malformed checkpoint line: %r", line)
                continue
            mapping[parsed[0]] = parsed[1]  # last write wins
        if mapping:
            logger.info("Restart: %d cached basecall(s) on record", len(mapping))
        return mapping

    def __contains__(self, name: str) -> bool:
        return name in self._map

    def __len__(self) -> int:
        """Basecalls on record (``run_dir_state`` and ``run_status.json``'s ``continued``)."""
        return len(self._map)

    def get(self, name: str) -> Path | None:
        """The cached ubam dir for ``name`` if recorded and still on disk, else None.

        Checks nothing about the file or the model; the drivers use ``verified``.
        """
        entry = self._map.get(name)
        if entry is None:
            return None
        p = Path(entry.ubam)
        if not p.exists():
            logger.warning("Cached BAM for %s vanished (%s); will re-basecall", name, p)
            return None
        return p

    def check_model(self, name: str, model: ModelFingerprint) -> None:
        """Raise if ``name``'s cached basecall was made with another model.

        An input with no entry, or with an entry that names no model, passes.

        Raises:
            StaleBasecallError: the entry names another model, or the same folder name
                with other files.
        """
        entry = self._map.get(name)
        if entry is None or not entry.checkable:
            return
        was = (entry.model_name, entry.model_sha256)
        if was == tuple(model):
            return
        where = self.state_file.parent
        if entry.model_name != model[0]:
            raise StaleBasecallError(
                f"The basecalls kept in {where} were made with the model "
                f"{entry.model_name!r}, and this run asks for {model[0]!r} "
                f"(first seen at {name}). One run is basecalled with one model. "
                f"{_FRESH_RUN} To continue this run instead, give it the model it was "
                f"started with.")
        raise StaleBasecallError(
            f"The basecalls kept in {where} were made with the model folder "
            f"{entry.model_name!r}, whose files have changed since (first seen at "
            f"{name}; digest {str(entry.model_sha256)[:12]} then, {model[1][:12]} now). "
            f"{_FRESH_RUN} To continue this run instead, give it the model folder as it "
            f"was.")

    def verified(self, name: str, *, input_path: Path | str,
                 model: ModelFingerprint) -> Path | None:
        """The cached ubam dir for ``name``, if it was made from this file and model.

        Args:
            name: The POD5's file name, the cache key.
            input_path: The POD5 as it is now; its contents are hashed.
            model: The model this run basecalls with (``model_fingerprint``).

        Returns:
            The directory, or None on a cache miss: no entry, a vanished directory, or
            an entry that records neither file nor model. The caller basecalls again.

        Raises:
            StaleBasecallError: the entry was made with another model, or from a file
                with other contents.
        """
        ubam = self.get(name)
        if ubam is None:
            return None
        entry = self._map[name]
        if not entry.checkable:
            logger.warning(
                "The cached basecall of %s cannot be checked against the file and the "
                "model: its entry was written before those were recorded. Basecalling "
                "it again.", name)
            return None
        self.check_model(name, model)
        now = file_sha256(input_path)
        if now != entry.input_sha256:
            raise StaleBasecallError(
                f"{name} has changed since this run basecalled it: the file at "
                f"{input_path} is not the one the kept basecall was made from (sha256 "
                f"{str(entry.input_sha256)[:12]} then, {now[:12]} now). {_FRESH_RUN} To "
                f"continue this run instead, put the earlier file back.")
        return ubam

    def record(self, name: str, bam_path: Path | str, *,
               input_sha256: Optional[str] = None,
               model: Optional[ModelFingerprint] = None) -> None:
        """Append one entry (durable; the last write wins).

        With ``input_sha256`` and ``model`` the entry can be checked on a restart;
        without them it is a two-field entry, which ``verified`` treats as a miss.
        """
        bam_path = str(bam_path)
        if input_sha256 is not None and model is not None:
            entry = _Entry(bam_path, input_sha256, _one_field(model[0]), model[1])
            fields = [name, bam_path, input_sha256, entry.model_name, entry.model_sha256]
        else:
            entry = _Entry(bam_path)
            fields = [name, bam_path]
        self._map[name] = entry
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("a", encoding="utf-8") as fh:
            fh.write("\t".join(str(f) for f in fields) + "\n")
