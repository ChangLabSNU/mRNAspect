"""A release archive is checked as the person who downloads it gets it.

`scripts/check_release.py` unpacks the archive somewhere new and reads it: no file names
the build account's home or checkout, the build record names the release's commit, Core
in the app is that commit's Core byte for byte, the packages asked about are the versions
wanted, and the third parties' notices list what is there. Each of those was found wrong,
or left unasked, on the first release.

The app here is a stand-in tree with a real git repository as its source, so every check
but the run of the app's own Python is exercised on any platform.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "check_release.py"

pytestmark = [
    pytest.mark.skipif(not _SCRIPT.exists(), reason="scripts/ is not in this checkout"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not here"),
]

RULE = "-" * 80
#: The licence texts kept beside the plotly bundle, as the app's notices must hold them.
_BUNDLE_TEXTS = ("Licence notices for plotly-cartesian-9.9.9.min.js\n\nplotly.js 9.9.9\n"
                 "MIT License\n\nieee754 1.2.1\nCopyright 2008 Fair Oaks Labs, Inc.\n")


@functools.lru_cache(maxsize=1)
def _tool():
    spec = importlib.util.spec_from_file_location("check_release", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), "-c", "user.name=test", "-c", "user.email=nobody",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True, timeout=60)
    return done.stdout.strip()


def _source(parent: Path) -> tuple[Path, str]:
    """A repository holding a small Core, and its commit."""
    source = parent / "source"
    (source / "mrnaspect" / "reporting").mkdir(parents=True)
    (source / "mrnaspect" / "tests").mkdir()
    (source / "mrnaspect" / "__init__.py").write_text("__version__ = '0.1.0'\n")
    (source / "mrnaspect" / "main.py").write_text("def main():\n    return 0\n")
    (source / "mrnaspect" / "reporting" / "template.html").write_text("<p>report</p>\n")
    (source / "mrnaspect" / "reporting" / "assets").mkdir()
    (source / "mrnaspect" / "reporting" / "assets" / "plotly-cartesian-9.9.9.min.js").write_text(
        "/** plotly.js v9.9.9 */\n")
    (source / "mrnaspect" / "reporting" / "assets" / "plotly-cartesian-9.9.9.LICENSES.txt").write_text(
        _BUNDLE_TEXTS)
    (source / "mrnaspect" / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    (source / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2026 Seoul National University\n\nPermission is ...\n")
    _git(source, "init", "--quiet")
    _git(source, "add", ".")
    _git(source, "commit", "--quiet", "-m", "release")
    return source, _git(source, "rev-parse", "HEAD")


def _app(parent: Path, source: Path, commit: str, *, packages: dict[str, str] | None = None,
         dirty: bool = False) -> Path:
    """An .app whose environment holds that commit's Core, as staging leaves it."""
    app = parent / "packed" / "mRNAspect.app"
    env = app / "Contents" / "Resources" / "env"
    site = env / "lib" / "python3.14" / "site-packages"
    shutil.copytree(source / "mrnaspect", site / "mrnaspect",
                    ignore=shutil.ignore_patterns("tests"))
    (site / "mrnaspect" / "_build.json").write_text(json.dumps(
        {"commit": commit, "dirty": dirty, "staged_at": "2026-11-21T00:00:00+00:00"}))
    (site / "mrnaspect" / "__pycache__").mkdir()
    (site / "mrnaspect" / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"\x00pyc")
    (env / "conda-meta").mkdir()
    notices = ["mRNAspect: third-party notices\n"]
    for name, version in (packages or {"pillow": "13.0.0", "numpy": "2.5.3"}).items():
        (env / "conda-meta" / f"{name}-{version}-0.json").write_text(
            json.dumps({"name": name, "version": version}))
        notices.append(f"\n{RULE}\n{name} {version}\n{RULE}\nLicence: a licence\n")
    notices.append(f"\n{RULE}\nplotly.js 9.9.9\n{RULE}\nLicence: MIT\n--- the texts ---\n"
                   + _BUNDLE_TEXTS)
    (env / "THIRD-PARTY-NOTICES.txt").write_text("".join(notices))
    # WeasyPrint as conda would have installed it: its record and its metadata folder.
    (env / "conda-meta" / "weasyprint-70.0-0.json").write_text(
        json.dumps({"name": "weasyprint", "version": "70.0"}))
    (env / "THIRD-PARTY-NOTICES.txt").write_text(
        (env / "THIRD-PARTY-NOTICES.txt").read_text()
        + f"\n{RULE}\nweasyprint 70.0\n{RULE}\nLicence: BSD\n")
    (site / "weasyprint-70.0.dist-info").mkdir()
    (site / "weasyprint-70.0.dist-info" / "INSTALLER").write_text("conda\n")
    # Core's own metadata, with the licence file the wheel carries.
    (site / "mrnaspect-0.1.0.dist-info" / "licenses").mkdir(parents=True)
    (site / "mrnaspect-0.1.0.dist-info" / "INSTALLER").write_text("pip\n")
    (site / "mrnaspect-0.1.0.dist-info" / "METADATA").write_text("Name: mrnaspect\nVersion: 0.1.0\n")
    shutil.copy(source / "LICENSE", site / "mrnaspect-0.1.0.dist-info" / "licenses" / "LICENSE")
    (env / "bin").mkdir()
    return app


def _run(*args: object) -> tuple[int, str]:
    done = subprocess.run([sys.executable, str(_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout + done.stderr


def test_a_release_built_as_it_should_be_passes(tmp_path: Path) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--forbid", source,
                     "--min", "weasyprint=70.0", "--min", "pillow=13.0.0",
                     "--absent", "pypdf", "--no-run")
    assert code == 0, out
    # Two folders, the record, Core, its licence, two versions, one absence, the notices,
    # the plotly bundle's texts in them.
    assert "10 passed, 0 failed" in out, out
    assert "the app carries the commit's LICENSE for Core: Copyright (c) 2026 Seoul National University" in out
    assert "NOTE  not asked (--no-run)" in out


def test_the_archive_is_unpacked_and_read_as_packed(tmp_path: Path) -> None:
    if shutil.which("ditto") is None and shutil.which("unzip") is None:
        pytest.skip("nothing here unpacks a .zip the way the check does")
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    archive = tmp_path / "mRNAspect-0.1.0-macos-arm64.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        for path in sorted(app.parent.rglob("*")):
            if path.is_file():
                packed.write(path, path.relative_to(app.parent).as_posix())
    code, out = _run(archive, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 0, out
    assert "mRNAspect.app" in out
    # Unpacked under a folder with a space in its name: an app in "Lab Tools" could not
    # align a read in the first release, and what runs the app runs it from there.
    assert re.search(r"read at .*/Lab Tools/mRNAspect\.app", out), out


@pytest.mark.parametrize("where", ["a compiled file", "a text file", "a file's own name",
                                   "a link"])
def test_a_file_that_names_the_build_account_s_home_fails(tmp_path: Path, where: str) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    env = app / "Contents" / "Resources" / "env"
    if where == "a compiled file":
        (env / "lib" / "python3.14" / "os.cpython-314.pyc").write_bytes(
            b"\x00\x01/Users/builder/mrnaspect/dist/bundle-env/lib/python3.14/os.py\x00")
    elif where == "a text file":
        (env / "lib" / "pkgconfig").mkdir()
        (env / "lib" / "pkgconfig" / "zlib.pc").write_text("prefix=/Users/builder/env\n")
    elif where == "a file's own name":
        (env / "Users" / "builder").mkdir(parents=True)
        (env / "Users" / "builder" / "notes.txt").write_text("x")
    else:
        (env / "bin" / "python3").symlink_to("/Users/builder/env/bin/python3.14")
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1, out
    assert "FAIL  1 files name /Users/builder" in out, out


def test_a_record_of_another_commit_or_of_a_modified_tree_fails(tmp_path: Path) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    code, out = _run(app, "--commit", "f" * 40, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1 and "of another commit or a modified tree" in out, out
    shutil.rmtree(app.parent)
    app = _app(tmp_path, source, commit, dirty=True)
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1 and "modified" in out, out


def test_core_that_is_not_the_commit_s_fails(tmp_path: Path) -> None:
    """A module the commit does not have, and one that differs from the commit's."""
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    package = app / "Contents/Resources/env/lib/python3.14/site-packages/mrnaspect"
    (package / "staging_probe.py").write_text("")
    (package / "main.py").write_text("def main():\n    return 1\n")
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1, out
    assert "mrnaspect/staging_probe.py: in the app and not in the commit" in out
    assert "mrnaspect/main.py: differs from the commit's" in out
    # The build record and compiled Python belong to an installed copy, not to a commit.
    assert "_build.json" not in out and "__pycache__" not in out


def test_an_old_version_a_second_copy_or_a_package_that_should_be_gone_fails(
        tmp_path: Path) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit, packages={"pillow": "12.3.0", "pypdf": "6.21.2"})
    site = app / "Contents/Resources/env/lib/python3.14/site-packages"
    (site / "weasyprint-69.0.dist-info").mkdir()       # a superseded copy left beside
    (site / "pypdf").mkdir()
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--min", "weasyprint=70.0",
                     "--min", "pillow=13.0.0", "--absent", "pypdf", "--no-run")
    assert code == 1, out
    assert "FAIL  pillow: 12.3.0; wanted one copy, at least 13.0.0" in out
    assert "FAIL  weasyprint: 69.0, 70.0; wanted one copy, at least 70.0" in out
    assert "FAIL  pypdf is in the app" in out and "recorded as 6.21.2" in out


def test_notices_written_for_other_packages_fail(tmp_path: Path) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    env = app / "Contents" / "Resources" / "env"
    (env / "conda-meta" / "pango-1.58.2-0.json").write_text(
        json.dumps({"name": "pango", "version": "1.58.2"}))
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1 and "pango 1.58.2 is in the environment and not in" in out, out


def test_a_licence_that_is_not_the_commit_s_fails(tmp_path: Path) -> None:
    """The copyright line a reader of the app sees must be the one the release has."""
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    licence = (app / "Contents/Resources/env/lib/python3.14/site-packages"
               / "mrnaspect-0.1.0.dist-info/licenses/LICENSE")
    licence.write_text(licence.read_text().replace("Seoul National University", "Qbiolab"))
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1, out
    assert "the licence in the app differs from the commit's LICENSE" in out
    assert "Copyright (c) 2026 Qbiolab" in out, "the line found is shown"


def test_notices_without_the_plotly_bundle_s_texts_fail(tmp_path: Path) -> None:
    """The app's notices are where a reader of the app finds the terms of what the
    bundle carries: the one-line mentions inside the bundle are not those terms."""
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    notices = app / "Contents/Resources/env/THIRD-PARTY-NOTICES.txt"
    notices.write_text(notices.read_text().replace("Copyright 2008 Fair Oaks Labs, Inc.\n", ""))
    code, out = _run(app, "--commit", commit, "--source", source,
                     "--forbid", "/Users/builder", "--no-run")
    assert code == 1, out
    assert "the notices do not hold plotly-cartesian-9.9.9.LICENSES.txt whole" in out


def test_versions_are_compared_as_numbers() -> None:
    tool = _tool()
    assert tool._version("70.0") > tool._version("69.10")
    assert tool._version("13.0.0") >= tool._version("13.0.0")
    assert tool._version("6.21.4") > tool._version("6.21.2")


@pytest.mark.parametrize("args, said", [
    (["--commit", "abc123"], "not a commit in full"),
    (["--commit", "f" * 40], "no --forbid folder"),
])
def test_what_does_not_name_a_release_is_refused(tmp_path: Path, args: list[str],
                                                 said: str) -> None:
    source, commit = _source(tmp_path)
    app = _app(tmp_path, source, commit)
    code, out = _run(app, "--source", source, *args)
    assert code == 2 and said in out, out


def test_the_fixture_the_check_runs_is_the_suite_s() -> None:
    """The run's expected result is the fixture's, as the golden files record it."""
    tool = _tool()
    assert (_ROOT / tool.FIXTURE["bam"]).is_file()
    assert (_ROOT / tool.FIXTURE["reference"]).is_file()
    assert (tool.FIXTURE["mapped"], tool.FIXTURE["constructs"]) == (166, 4)
