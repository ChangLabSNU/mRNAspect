"""What the lock file installs, where a release is held to it.

The first release carried WeasyPrint 69.0, Pillow 12.3.0 and pypdf 6.21.2, each a version
with a published advisory. conda-forge had no newer WeasyPrint, so 70.0 comes from PyPI:
the one package that does, as its pure-Python wheel, with everything it needs still from
conda. pypdf is for the tests alone and the app does not carry it.

Read from `pixi.lock`, for both platforms, so that a re-solve which undoes any of this
fails here and not at a release.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LOCK = _ROOT / "pixi.lock"

pytestmark = pytest.mark.skipif(not _LOCK.exists(), reason="no pixi.lock in this tree")

PLATFORMS = ("linux-64", "osx-arm64")
#: At least these, on every platform: the versions that answer the advisories.
FLOORS = {"weasyprint": (70, 0), "pillow": (13, 0, 0), "pypdf": (6, 21, 4)}


def _environment(platform: str) -> list[str]:
    """The package URLs the default environment lists for one platform."""
    text = _LOCK.read_text()
    block = re.search(rf"^      {re.escape(platform)}:\n((?:      - .+\n)+)", text, re.M)
    assert block, f"{platform} is not in pixi.lock"
    return re.findall(r"^      - (?:conda|pypi): (\S+)$", block.group(1), re.M)


def _versions(platform: str) -> dict[str, tuple[int, ...]]:
    found = {}
    for url in _environment(platform):
        file = url.rsplit("/", 1)[-1]
        match = (re.fullmatch(r"(.+)-(\d[^-]*)-[^-]+\.(?:conda|tar\.bz2)", file)
                 or re.fullmatch(r"(.+?)-(\d[^-]*)-.+\.whl", file))
        if match:
            found[match.group(1).lower().replace("_", "-")] = tuple(
                int(n) for n in re.findall(r"\d+", match.group(2))[:3])
    return found


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_versions_with_advisories_are_behind_us(platform: str) -> None:
    have = _versions(platform)
    for name, floor in FLOORS.items():
        assert name in have, f"{platform}: {name} is not in the lock"
        assert have[name] >= floor, (platform, name, have[name])


@pytest.mark.parametrize("platform", PLATFORMS)
def test_weasyprint_is_the_only_package_from_pypi_and_is_pure_python(platform: str) -> None:
    """conda is the sole provider of compiled code here (pysam is built against its
    htslib). A wheel with compiled code would be a second, unmatched provider."""
    wheels = [u for u in _environment(platform) if "files.pythonhosted.org" in u
              or u.endswith(".whl")]
    assert len(wheels) == 1, wheels
    assert wheels[0].endswith("/weasyprint-70.0-py3-none-any.whl"), wheels


def test_the_wheel_is_locked_by_its_hash() -> None:
    text = _LOCK.read_text()
    entry = re.search(r"^- pypi: \S+/weasyprint-70\.0-py3-none-any\.whl\n((?:  .+\n)+)",
                      text, re.M)
    assert entry and re.search(r"^  sha256: [0-9a-f]{64}$", entry.group(1), re.M)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_what_weasyprint_needs_comes_from_conda(platform: str) -> None:
    """Its own requirements, and the two its font handling asks for."""
    text = _LOCK.read_text()
    entry = re.search(r"^- pypi: \S+/weasyprint-70\.0-py3-none-any\.whl\n((?:  .+\n)+)",
                      text, re.M).group(1)
    needs = {re.split(r"[\[<>=!~; ]", line.strip()[2:], maxsplit=1)[0].lower()
             for line in entry.split("requires_dist:\n", 1)[1].splitlines()
             if line.startswith("  - ") and "extra ==" not in line}
    assert needs >= {"pydyf", "cffi", "tinyhtml5", "tinycss2", "cssselect2", "pyphen",
                     "pillow", "fonttools"}, needs
    have = _versions(platform)
    for name in sorted(needs | {"brotli-python", "zopfli", "pango"}):
        assert name in have, f"{platform}: {name} is not provided by conda"


def test_the_app_does_not_carry_the_pdf_reader() -> None:
    spec = importlib.util.spec_from_file_location(
        "strip_bundle_env", _ROOT / "scripts" / "strip_bundle_env.py")
    strip = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = strip
    spec.loader.exec_module(strip)
    assert "pypdf" in strip.LEFT_OUT and strip.LEFT_OUT["pypdf"].modules == ("pypdf",)


def test_a_test_that_reads_a_pdf_cannot_skip_for_want_of_the_reader() -> None:
    """With `importorskip` a missing reader turned every PDF check into a skip, and a
    run with skips still ends green."""
    here = Path(__file__)
    using = [p.name for p in sorted(here.parent.glob("test_*.py")) if p != here
             and re.search(r"importorskip\(\s*[\"']pypdf", p.read_text())]
    assert using == []
    import pypdf  # noqa: F401  (the environment the tests run in has it)


def test_the_window_s_lock_has_the_fixed_tokio() -> None:
    """1.53.1 was in the first release; the advisory is fixed from 1.53.3."""
    lock = (_ROOT / "gui" / "src-tauri" / "Cargo.lock").read_text()
    versions = re.findall(r'^name = "tokio"\nversion = "([^"]+)"', lock, re.M)
    assert len(versions) == 1, versions
    assert tuple(int(n) for n in versions[0].split(".")[:3]) >= (1, 53, 3), versions

