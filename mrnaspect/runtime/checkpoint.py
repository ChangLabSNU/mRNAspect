"""Basecall cache: a POD5 name -> its basecall output (a directory of unaligned BAMs).

In pod5 mode basecalling is the expensive GPU step. Once a POD5's basecall output is
complete on disk the driver records ``pod5_name<TAB>ubam_dir``, and a restart reuses it
instead of basecalling again. Every input is still re-aligned and re-ingested, so a
restarted run's report is complete; the cache only saves the basecall. bam mode does not
use it.

An entry is written only after the basecall output is complete, so a ``kill -9``
mid-basecall leaves no entry. Entries are appended one line at a time. A malformed line
(one torn by a ``kill -9``, say) or an entry whose directory has vanished is a cache
miss, and that POD5 is basecalled again; it is never an error.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_NAME = "basecall_cache.tsv"


class Checkpoint:
    """POD5 name -> cached ubam directory, persisted one ``name<TAB>path`` per line."""

    def __init__(self, state_file: Path | str) -> None:
        self.state_file = Path(state_file)
        self._map: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        """Read the map (empty if the file is absent); malformed lines are skipped."""
        mapping: dict[str, str] = {}
        if not self.state_file.exists():
            return mapping
        for line in self.state_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                # E.g. a line torn by a kill -9 mid-append: basecalled again.
                logger.warning("Ignoring malformed checkpoint line: %r", line)
                continue
            mapping[parts[0]] = parts[1]  # last write wins
        if mapping:
            logger.info("Restart: %d cached basecall(s) on record", len(mapping))
        return mapping

    def get(self, name: str) -> Path | None:
        """The cached ubam dir for ``name`` if recorded and still on disk, else None."""
        cached = self._map.get(name)
        if cached is None:
            return None
        p = Path(cached)
        if not p.exists():
            logger.warning("Cached BAM for %s vanished (%s); will re-basecall", name, p)
            return None
        return p

    def record(self, name: str, bam_path: Path | str) -> None:
        """Append one POD5 name -> ubam dir entry (durable; the last write wins)."""
        bam_path = str(bam_path)
        self._map[name] = bam_path
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{name}\t{bam_path}\n")
