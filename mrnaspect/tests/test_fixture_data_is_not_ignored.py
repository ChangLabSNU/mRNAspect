"""The sequencing fixtures stay excepted from the blanket ignore rule.

`.gitignore` ignores every BAM, BAI, POD5 and FASTQ, so a user's own run is never
committed by accident, and excepts the package's fixtures by their directory. The
2026-10-22 rename moved that directory from `qcrna/tests/data/` to
`mrnaspect/tests/data/`, and the exceptions kept naming the old one. Nothing failed: the
three fixtures were already tracked, and ignore rules do not apply to tracked files. But
a fixture added there next would have been left out of the commit without a word, and
every clone but the one that made it would have lacked it -- the failure conftest.py
describes. Found 2026-10-29, before the first push to a second remote.

Patterns are matched with fnmatch, which is looser than git (its `*` crosses `/`). A
looser match cannot hide an exception naming a directory that is not there, which is the
failure this file is for.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IGNORE = _ROOT / ".gitignore"
_DATA = Path(__file__).resolve().parent / "data"

#: What the "Sequencing data" block of .gitignore ignores everywhere.
_SEQUENCING = ("*.bam", "*.bai", "*.pod5", "*.fastq", "*.fastq.gz")

pytestmark = pytest.mark.skipif(
    not _IGNORE.exists(), reason=".gitignore is not in this checkout")


def _exceptions() -> list[str]:
    lines = (line.strip() for line in _IGNORE.read_text().splitlines())
    return [line[1:] for line in lines if line.startswith("!")]


def test_every_sequencing_fixture_is_excepted() -> None:
    excepted = _exceptions()
    fixtures = [f.relative_to(_ROOT).as_posix() for f in sorted(_DATA.iterdir())
                if any(fnmatch(f.name, s) for s in _SEQUENCING)]
    assert fixtures, "found no sequencing fixture; the data path is wrong"
    missed = [f for f in fixtures if not any(fnmatch(f, e) for e in excepted)]
    assert not missed, f"ignored, and no '!' line in .gitignore excepts them: {missed}"


def test_every_exception_still_names_a_file() -> None:
    assert _exceptions(), "found no '!' line in .gitignore"
    dead = [e for e in _exceptions() if not any(_ROOT.glob(e.lstrip("/")))]
    assert not dead, f"these exceptions match no file, so they guard nothing: {dead}"
