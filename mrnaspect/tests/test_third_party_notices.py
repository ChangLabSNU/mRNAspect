"""The app carries one notices file for the third parties' software, generated, not typed.

Asked for on 2026-11-12, in full. The app redistributes
about 120 conda packages, the Rust crates of its window and the JavaScript of its page, and
MIT, BSD, Apache and the LGPL all ask that their text travel with every copy.

`scripts/make_notices.py` writes `THIRD-PARTY-NOTICES.txt` into the staged environment
from what that environment itself records, so it cannot describe another solve. A package
whose licence text cannot be found is named in the file under INCOMPLETE; staging goes on
(a developer is not stopped) and `check_bundle.sh`, the gate before a release, fails.

Held here: the generator on a stand-in environment, on the real environment of this host,
and that staging, the bundle check, the shell and About all use it.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "make_notices.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"
_CHECK = _ROOT / "scripts" / "check_bundle.sh"
_GUI = _ROOT / "gui"

pytestmark = pytest.mark.skipif(
    not _STAGE.exists(), reason="scripts/ is not in this checkout")


@functools.lru_cache(maxsize=1)
def _make():
    """The script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("make_notices", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# A stand-in: an environment, the package cache behind it, and a checkout
# ---------------------------------------------------------------------------

_FEEDSTOCK = "https://github.com/conda-forge/{}-feedstock"
_FTL = '''The FreeType Project LICENSE

    """
    Portions of this software are copyright © <year> The FreeType
    Project (https://freetype.org).  All rights reserved.
    """

  Please replace <year> with the value from the FreeType version you
  actually use.
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _package(world: Path, name: str, version: str, licence: str, *, files=("lib/x",),
             texts=None, feedstock=None, sha="a" * 40, source=None, recipe="meta.yaml",
             home=None) -> None:
    """One conda package: its record in the environment and its folder in the cache."""
    env, cache = world / "env", world / "cache" / f"{name}-{version}-0"
    for rel in files:
        if not (env / rel).exists():
            _write(env / rel, "x\n")
    for title, text in (texts or {}).items():
        _write(cache / "info" / "licenses" / title, text)
    about = {"home": home or f"https://example.org/{name}",
             "extra": {"remote_url": _FEEDSTOCK.format(feedstock or name), "sha": sha}}
    _write(cache / "info" / "about.json", json.dumps(about))
    if source:
        block = (f"package:\n  name: {name}\nsource:\n  url: {source}\n  sha256: {'b' * 64}\n"
                 "build:\n  number: 0\n" if recipe == "meta.yaml" else
                 f"recipe:\n  package:\n    name: {name}\n  source:\n  - url: {source}\n"
                 f"    sha256: {'b' * 64}\n  build:\n    number: 0\n")
        _write(cache / "info" / "recipe" / recipe, block)
    _write(env / "conda-meta" / f"{name}-{version}-0.json", json.dumps({
        "name": name, "version": version, "license": licence, "files": list(files),
        "url": f"https://conda.anaconda.org/conda-forge/osx-arm64/{name}-{version}-0.conda",
        "extracted_package_dir": str(cache)}))


def _world(parent: Path) -> Path:
    """An environment with one package of each kind the generator has to tell apart."""
    world = parent / "world"
    _package(world, "numpy", "2.5.3", "BSD-3-Clause", texts={"LICENSE.txt": "numpy's BSD text\n"},
             source="https://github.com/numpy/numpy/releases/download/v2.5.3/numpy-2.5.3.tar.gz",
             recipe="rendered_recipe.yaml")
    _package(world, "pango", "1.58.2", "LGPL-2.1-or-later", texts={"COPYING": "the LGPL text\n"},
             source="https://download.gnome.org/sources/pango/1.58/pango-1.58.2.tar.xz")
    # Two outputs of one recipe: only one of them carries the texts.
    _package(world, "freetype", "2.14.3", "GPL-2.0-only OR FTL", feedstock="freetype", sha="f" * 40,
             files=("include/freetype2/freetype/freetype.h",),
             texts={"docs/FTL.TXT": _FTL, "docs/GPLv2.TXT": "the GPL v2 text\n"})
    _write(world / "env/include/freetype2/freetype/freetype.h",
           "/*\n * Copyright (C) 1996-2026 by\n * David Turner, Robert Wilhelm, and Werner Lemberg.\n */\n")
    _package(world, "libfreetype6", "2.14.3", "GPL-2.0-only OR FTL", feedstock="freetype",
             sha="f" * 40, files=("lib/libfreetype.6.dylib",))
    # Its text is a file it installs, not one its package folder keeps.
    _package(world, "libgcc", "16.2.0", "GPL-3.0-only WITH GCC-exception-3.1",
             files=("lib/libgcc_s.1.dylib", "share/licenses/gcc-libs/RUNTIME.LIBRARY.EXCEPTION"))
    _write(world / "env/share/licenses/gcc-libs/RUNTIME.LIBRARY.EXCEPTION", "the GCC runtime exception\n")
    # A name that installs nothing.
    _package(world, "libgcc-ng", "16.2.0", "GPL-3.0-only WITH GCC-exception-3.1", files=())
    # No text anywhere but the copy this repository keeps.
    _package(world, "pysam", "0.24.1", "MIT", files=("lib/python3.14/site-packages/pysam/__init__.py",))
    _package(world, "pyphen", "0.18.1", "GPL-2.0-or-later AND LGPL-2.1-or-later AND MPL-1.1",
             texts={"COPYING.MPL": "the MPL text\n", "LICENSE": "tri-license\n"})

    root = world / "checkout"
    _write(root / "LICENSE", "MIT License\n\nCopyright (c) 2026 Qbiolab\n\n"
           "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
           "of this software.\n")
    _write(root / "gui/package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {
        "": {"dependencies": {"@tauri-apps/api": "^2"}},
        "node_modules/@tauri-apps/api": {"version": "2.12.4", "license": "Apache-2.0 OR MIT"},
        "node_modules/@tauri-apps/plugin-dialog": {"version": "2.8.4", "license": "MIT OR Apache-2.0"},
        "node_modules/vite": {"version": "8.0.16", "license": "MIT", "dev": True}}}))
    _write(root / "gui/node_modules/@tauri-apps/api/LICENSE-MIT", "tauri api's MIT text\n")
    _write(root / "gui/node_modules/@tauri-apps/api/LICENSE-APACHE-2.0", "tauri api's Apache text\n")
    # As published: a statement of the licences and the copyright, and no licence text.
    _write(root / "gui/node_modules/@tauri-apps/plugin-dialog/LICENSE.spdx",
           "PackageLicenseDeclared: Apache-2.0\nPackageLicenseDeclared: MIT\n"
           "PackageCopyrightText: 2019-2022, The Tauri Programme in the Commons Conservancy\n")
    _write(root / "gui/node_modules/vite/LICENSE", "vite's text\n")
    _write(root / "gui/public/plotly-cartesian-3.7.0.min.js",
           "/**\n* plotly.js (cartesian - minified) v3.7.0\n* Copyright 2012-2026, Plotly, Inc.\n"
           "* All rights reserved.\n* Licensed under the MIT license\n*/\nvar Plotly={};\n"
           "/*! Bundled license information:\n\nobject-assign/index.js:\n  (*\n  object-assign\n"
           "  (c) Sindre Sorhus\n  @license MIT\n  *)\n*/\n")
    _write(root / "gui/src-tauri/Cargo.toml", "[package]\nname = \"mrnaspect-shell\"\n")
    return world


def _crates(world: Path) -> dict:
    """What `cargo metadata` prints: the shell, a crate it links, one it builds with, one
    only its tests use, and one that ships no licence file."""
    reg = world / "registry"
    _write(reg / "tauri-2.9.0/LICENSE_MIT", "tauri's MIT text\n")
    _write(reg / "tauri-2.9.0/Cargo.toml", "")
    _write(reg / "cc-1.2.0/LICENSE-APACHE", "cc's Apache text\n")
    _write(reg / "cc-1.2.0/Cargo.toml", "")
    _write(reg / "tempfile-3.0.0/LICENSE-MIT", "tempfile's text\n")
    _write(reg / "tempfile-3.0.0/Cargo.toml", "")
    _write(reg / "bare-0.1.0/Cargo.toml", "")
    shell = world / "checkout/gui/src-tauri/Cargo.toml"

    def package(name, version, licence, manifest):
        return {"id": f"{name} {version}", "name": name, "version": version, "license": licence,
                "manifest_path": str(manifest), "repository": f"https://github.com/x/{name}",
                "authors": [f"The {name} authors"]}

    def dep(pkg, kind):
        return {"pkg": pkg, "dep_kinds": [{"kind": kind, "target": None}]}

    return {
        "packages": [package("mrnaspect-shell", "0.1.0", "MIT", shell),
                     package("tauri", "2.9.0", "Apache-2.0 OR MIT", reg / "tauri-2.9.0/Cargo.toml"),
                     package("cc", "1.2.0", "MIT OR Apache-2.0", reg / "cc-1.2.0/Cargo.toml"),
                     package("tempfile", "3.0.0", "MIT OR Apache-2.0", reg / "tempfile-3.0.0/Cargo.toml"),
                     package("bare", "0.1.0", "Zlib", reg / "bare-0.1.0/Cargo.toml")],
        "resolve": {"root": "mrnaspect-shell 0.1.0", "nodes": [
            {"id": "mrnaspect-shell 0.1.0", "deps": [dep("tauri 2.9.0", None), dep("cc 1.2.0", "build"),
                                                     dep("tempfile 3.0.0", "dev")]},
            {"id": "tauri 2.9.0", "deps": [dep("bare 0.1.0", None)]},
            {"id": "cc 1.2.0", "deps": []}, {"id": "tempfile 3.0.0", "deps": []},
            {"id": "bare 0.1.0", "deps": []}]},
    }


def _fake_cargo(world: Path, metadata: dict | None) -> Path:
    """A folder to put on PATH: a `cargo` that prints ``metadata``, and a `rustc`."""
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    _write(world / "metadata.json", json.dumps(metadata))
    cargo = tools / "cargo"
    cargo.write_text(f'#!/bin/sh\ncase "$1" in metadata) cat "{world / "metadata.json"}" ;; '
                     '*) exit 9 ;; esac\n')
    rustc = tools / "rustc"
    rustc.write_text('#!/bin/sh\necho "rustc 1.90.0"\necho "host: aarch64-apple-darwin"\n')
    for tool in (cargo, rustc):
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return tools


def _run(*args: object, path: Path | None = None) -> subprocess.CompletedProcess:
    """Run the script. ``path`` replaces PATH's tools; without it there is no cargo."""
    env = dict(os.environ, PATH=f"{path}:/usr/bin:/bin" if path else "/usr/bin:/bin")
    return subprocess.run([sys.executable, "-I", str(_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=180, env=env)


def _generate(world: Path, *, cargo: bool = True) -> tuple[subprocess.CompletedProcess, str]:
    tools = _fake_cargo(world, _crates(world)) if cargo else None
    proc = _run(world / "env", "--root", world / "checkout", path=tools)
    out = world / "env" / _make().NAME
    return proc, out.read_text() if out.exists() else ""


def _entry(text: str, heading: str) -> str:
    """One package's part of the file: from its heading to the next one."""
    rule = "-" * 80
    start = text.index(f"{rule}\n{heading}\n{rule}\n")
    end = text.find(f"\n{rule}\n", start + len(heading) + 2 * len(rule) + 3)
    return text[start:end if end != -1 else len(text)]


# ---------------------------------------------------------------------------
# 1. The environment's packages
# ---------------------------------------------------------------------------

def test_every_package_is_listed_with_its_licence_and_its_text(tmp_path: Path) -> None:
    proc, text = _generate(_world(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    numpy = _entry(text, "numpy 2.5.3")
    assert "Licence: BSD-3-Clause" in numpy
    assert "numpy's BSD text" in numpy
    assert "https://conda.anaconda.org/conda-forge/osx-arm64/numpy-2.5.3-0.conda" in numpy
    for heading in ("pango 1.58.2", "freetype 2.14.3", "libfreetype6 2.14.3", "libgcc 16.2.0",
                    "libgcc-ng 16.2.0", "pysam 0.24.1", "pyphen 0.18.1"):
        assert _entry(text, heading)


def test_the_same_environment_gives_the_same_file(tmp_path: Path) -> None:
    """No date in it, and a fixed order: a file that changes only when what ships does."""
    world = _world(tmp_path)
    _, first = _generate(world)
    _, second = _generate(world)
    assert first and first == second
    # Between the contents' line 2 and section 2's heading: section 1.
    names = re.findall(r"^-{80}\n(\S+) \S+\n-{80}$", first.split("2. The app window")[1], re.M)
    assert names == sorted(names, key=str.lower) and len(names) == 8


def test_a_copyleft_package_says_where_the_source_of_this_build_is(tmp_path: Path) -> None:
    """The LGPL asks for the library's source, or where to get it."""
    _, text = _generate(_world(tmp_path))
    pango = _entry(text, "pango 1.58.2")
    assert "https://download.gnome.org/sources/pango/1.58/pango-1.58.2.tar.xz" in pango
    assert "sha256 " + "b" * 64 in pango
    assert _FEEDSTOCK.format("pango") in pango and "a" * 40 in pango
    # The newer recipe format lists its source too.
    assert "numpy-2.5.3.tar.gz" in _entry(text, "numpy 2.5.3")


def test_a_package_without_its_own_text_takes_its_recipes(tmp_path: Path) -> None:
    """libfreetype6 is the library and freetype holds the licence files: one recipe."""
    _, text = _generate(_world(tmp_path))
    entry = _entry(text, "libfreetype6 2.14.3")
    assert "The FreeType Project LICENSE" in entry
    assert "freetype 2.14.3, built from the same recipe" in entry


def test_a_licence_file_the_package_installs_is_read(tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    entry = _entry(text, "libgcc 16.2.0")
    assert "the GCC runtime exception" in entry
    assert "share/licenses/gcc-libs/RUNTIME.LIBRARY.EXCEPTION" in entry


def test_a_package_that_installs_nothing_needs_no_text(tmp_path: Path) -> None:
    proc, text = _generate(_world(tmp_path))
    assert "installs no file" in _entry(text, "libgcc-ng 16.2.0")
    assert "INCOMPLETE" not in text, proc.stderr


def test_the_copy_this_repository_keeps_is_the_last_resort(tmp_path: Path) -> None:
    """pysam's package carries no licence file; `scripts/notices/` holds its COPYING."""
    _, text = _generate(_world(tmp_path))
    entry = _entry(text, "pysam 0.24.1")
    assert "Copyright (c) 2008-2009 Genome Research Ltd." in entry
    assert "scripts/notices/pysam.txt" in entry


def test_each_kept_copy_says_where_it_came_from() -> None:
    kept = sorted(p.name for p in (_ROOT / "scripts" / "notices").glob("*.txt"))
    assert kept, "scripts/notices/ holds no text"
    readme = (_ROOT / "scripts" / "notices" / "README.md").read_text()
    for name in kept:
        assert f"`{name}`" in readme, f"{name} is not accounted for in scripts/notices/README.md"


def test_where_a_licence_offers_a_choice_the_file_says_which_is_taken(tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    assert "Taken: FTL" in _entry(text, "freetype 2.14.3")
    pyphen = _entry(text, "pyphen 0.18.1")
    assert "Taken: MPL-1.1" in pyphen
    assert "dictionaries" in pyphen and "left out" in pyphen


def test_the_freetype_credit_carries_the_year_of_the_version_shipped(tmp_path: Path) -> None:
    """The FTL asks for this sentence, with the year "from the FreeType version you
    actually use": read from the header the environment holds."""
    _, text = _generate(_world(tmp_path))
    credit = ("Portions of this software are copyright © 2026 The FreeType Project "
              "(https://freetype.org). All rights reserved.")
    assert credit in _entry(text, "freetype 2.14.3")


def test_a_package_with_files_and_no_text_makes_the_file_say_incomplete(tmp_path: Path) -> None:
    """Staging goes on, the file says so at its top, and the release gate fails."""
    world = _world(tmp_path)
    _package(world, "mystery", "1.0", "MIT")
    proc, text = _generate(world)
    assert proc.returncode == 0, "a developer's staging must not stop for this"
    assert "mystery 1.0" in proc.stderr
    head = text.split("1. The Python environment\n")[0]
    assert re.search(r"^INCOMPLETE: .*mystery 1\.0", head, re.M), head
    check = _run("--check", world / "env")
    assert check.returncode == 1 and "mystery 1.0" in check.stderr


# ---------------------------------------------------------------------------
# 2. The window's crates and the page's JavaScript
# ---------------------------------------------------------------------------

def test_the_crates_the_window_is_built_from_are_listed(tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    crates = text.split("2. The app window")[2].split("3. The page")[0]
    assert "tauri's MIT text" in _entry(crates, "tauri 2.9.0")
    assert "Licence: Apache-2.0 OR MIT" in _entry(crates, "tauri 2.9.0")
    assert "cc's Apache text" in crates, "a crate the build runs is part of the build"
    assert "tempfile" not in crates, "a crate only the tests use is not in the app"
    assert "mrnaspect-shell" not in crates, "the shell is mRNAspect, not a third party"


def test_a_crate_without_a_licence_file_is_listed_as_declared(tmp_path: Path) -> None:
    """Many published crates leave the file out. The declaration is what there is."""
    proc, text = _generate(_world(tmp_path))
    bare = _entry(text, "bare 0.1.0")
    assert "Licence: Zlib" in bare and "no licence file" in bare
    assert "INCOMPLETE" not in text, proc.stderr


def test_without_cargo_the_file_says_the_crates_are_missing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    proc, text = _generate(world, cargo=False)
    assert proc.returncode == 0
    assert re.search(r"^INCOMPLETE: .*cargo", text, re.M)
    assert _run("--check", world / "env").returncode == 1


def test_the_page_lists_what_it_loads_and_not_the_build_tools(tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    page = text.split("3. The page")[2]
    api = _entry(page, "@tauri-apps/api 2.12.4")
    assert "tauri api's MIT text" in api and "tauri api's Apache text" in api
    assert "vite" not in page, "a development dependency is not in the page"


def test_a_page_package_with_only_an_spdx_file_takes_its_publishers_texts(
        tmp_path: Path) -> None:
    """@tauri-apps/plugin-dialog publishes LICENSE.spdx alone (seen in 2.8.4). The texts
    are the ones its publisher ships with @tauri-apps/api, under the same two licences."""
    proc, text = _generate(_world(tmp_path))
    entry = _entry(text.split("3. The page")[2], "@tauri-apps/plugin-dialog 2.8.4")
    assert "The Tauri Programme in the Commons Conservancy" in entry
    assert "tauri api's MIT text" in entry and "tauri api's Apache text" in entry
    assert "@tauri-apps/api 2.12.4" in entry
    assert "INCOMPLETE" not in text, proc.stderr


def test_plotly_comes_with_its_header_its_licence_and_what_it_bundles(tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    plotly = _entry(text.split("3. The page")[2], "plotly.js 3.7.0")
    assert "Copyright 2012-2026, Plotly, Inc." in plotly
    assert "Permission is hereby granted, free of charge" in plotly
    assert "Copyright (c) 2026 Qbiolab" not in plotly, "the permission notice, not our copyright"
    assert "(c) Sindre Sorhus" in plotly


def test_a_page_package_that_is_not_installed_is_incomplete(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "checkout/gui/node_modules/@tauri-apps/api/LICENSE-MIT").unlink()
    (world / "checkout/gui/node_modules/@tauri-apps/api/LICENSE-APACHE-2.0").unlink()
    _, text = _generate(world)
    assert re.search(r"^INCOMPLETE: .*@tauri-apps/api", text, re.M)


# ---------------------------------------------------------------------------
# 3. The file's first page, and the check
# ---------------------------------------------------------------------------

def test_the_first_page_says_what_the_file_is_and_what_the_app_does_not_carry(
        tmp_path: Path) -> None:
    _, text = _generate(_world(tmp_path))
    head = text.split("1. The Python environment\n")[0]
    assert "mRNAspect itself is under the MIT licence" in head
    for name in ("dorado_basecall_server", "ont_basecall_client", "MinKNOW",
                 "Oxford Nanopore Technologies"):
        assert name in head
    assert "8 packages" in head and "3 Rust crates" in head


def test_the_check_passes_on_a_whole_file_and_fails_when_a_package_is_not_in_it(
        tmp_path: Path) -> None:
    world = _world(tmp_path)
    _generate(world)
    assert _run("--check", world / "env").returncode == 0
    # A package installed after the file was written: the file no longer describes this
    # environment.
    _package(world, "latecomer", "3.1", "MIT", texts={"LICENSE": "x\n"})
    check = _run("--check", world / "env")
    assert check.returncode == 1 and "latecomer 3.1" in check.stderr


def test_the_check_fails_when_there_is_no_file(tmp_path: Path) -> None:
    world = _world(tmp_path)
    check = _run("--check", world / "env")
    assert check.returncode == 1 and _make().NAME in check.stderr


def test_a_folder_that_is_not_an_environment_is_refused(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 2


# ---------------------------------------------------------------------------
# 4. The real environment of this host, and the real page
# ---------------------------------------------------------------------------

def test_in_the_environment_here_every_package_has_its_text() -> None:
    """linux-64 on this host; the Mac's own answer is `check_bundle.sh` check 8. Skipped
    where there is no environment, or where its package cache has been cleared."""
    env = _ROOT / ".pixi" / "envs" / "default"
    make = _make()
    records = sorted(env.glob("conda-meta/*.json"))
    if not records:
        pytest.skip("no pixi environment here")
    if not all(Path(json.loads(r.read_text()).get("extracted_package_dir", "/nope")).is_dir()
               for r in records[:5]):
        pytest.skip("the package cache behind this environment is gone")
    entries, missing = make.collect_env(env)
    assert len(entries) == len(records)
    assert not missing, missing


def test_the_real_page_gives_plotlys_header_and_bundled_notices() -> None:
    make = _make()
    entries, missing = make.collect_page(_ROOT)
    plotly = [e for e in entries if e.name == "plotly.js"]
    assert len(plotly) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", plotly[0].version)
    joined = "\n".join(text for _, text in plotly[0].texts)
    assert "Plotly, Inc." in joined and "Bundled license information" in joined
    assert "Permission is hereby granted" in joined
    # node_modules is not on every host; when it is, nothing may be missing.
    if (_GUI / "node_modules").is_dir():
        assert not missing, missing


# ---------------------------------------------------------------------------
# 5. Staging writes it, the gate checks it, the app opens it
# ---------------------------------------------------------------------------

def test_staging_writes_the_file_after_it_has_left_things_out() -> None:
    """So what was left out is not listed, and what is listed is what ships."""
    text = _STAGE.read_text()
    assert text.index("strip_bundle_env.py") < text.index("make_notices.py")
    call = text.index("make_notices.py")
    line = text[text.rindex("\n", 0, call):text.index("\n", call)]
    assert '"$DEST/bin/python" -I' in line and '--root "$ROOT"' in line


def test_the_bundle_check_asks_whether_the_file_is_whole() -> None:
    text = _CHECK.read_text()
    call = text.index("make_notices.py")
    assert '--check "$RES"' in text[call:text.index("\n", call)]


def test_the_shell_opens_the_file_the_script_writes() -> None:
    """One name, in the script and in the Rust that looks for it inside the .app."""
    rust = (_GUI / "src-tauri" / "src" / "lib.rs").read_text()
    name = _make().NAME
    assert f"../Resources/env/{name}" in rust
    assert re.search(r"#\[tauri::command\]\s*fn open_notices\(", rust)
    handler = rust[rust.index("generate_handler!["):]
    assert "open_notices" in handler[:handler.index("])")]


def test_about_has_a_button_that_opens_the_notices() -> None:
    html = (_GUI / "index.html").read_text()
    about = html[html.index('id="page-about"'):]
    assert re.search(r'<button[^>]*id="opennotices"', about)
    ts = (_GUI / "src" / "main.ts").read_text()
    assert 'invoke("open_notices")' in ts
    # A build without the file says so beside the button, not in a console.
    assert 'id="noticeserr"' in about and '$("noticeserr")' in ts


def test_about_names_the_basecall_server_it_does_not_include() -> None:
    """Said by name, not as "a basecaller"."""
    html = (_GUI / "index.html").read_text()
    about = html[html.index('id="page-about"'):]
    assert "dorado-server" in about
    assert "Oxford Nanopore Technologies" in about
