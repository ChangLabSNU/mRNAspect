"""The bundled interpreter's path is agreed in two languages and one of them is JSON.

`resolve_python` (Rust) looks for `../Resources/env/bin/python3` relative to the
executable. `tauri.conf.json` (JSON) is what puts an environment there. Nothing links
them: change the destination in one and the other goes on looking where it always did,
and the symptom is an `.app` that starts, finds no interpreter and says so -- on the Mac,
at the end of a build nobody runs on this host.

So the agreement is pinned here, where it costs nothing to check. This does NOT test that
the bundler honours the declaration: that needs a Mac and a build, and
`scripts/check_bundle.sh` is the thing that settles it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_GUI = Path(__file__).resolve().parents[2] / "gui"
_CONF = _GUI / "src-tauri" / "tauri.conf.json"
_LIB = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not _CONF.exists() or not _LIB.exists(),
    reason="GUI sources are not in this checkout")


def _resource_dest() -> str:
    """Where `tauri.conf.json` says the environment lands, inside the resource dir."""
    resources = json.loads(_CONF.read_text())["bundle"]["resources"]
    assert isinstance(resources, dict), (
        "the MAP form is required: it names the destination, and the destination is the "
        "one thing resolve_python is specific about. A glob would place the tree under "
        "its own source path instead.")
    assert len(resources) == 1, resources
    return next(iter(resources.values())).strip("/")


def test_the_bundle_puts_the_env_where_the_shell_looks_for_it():
    dest = _resource_dest()
    rust = _LIB.read_text()
    # The macOS candidate, as the Rust writes it.
    assert f"../Resources/{dest}/bin/python3" in rust, (
        f"tauri.conf.json lands the environment at Resources/{dest}, and resolve_python "
        f"does not look there")
    # And the flat-layout candidate beside the binary, which has to agree too.
    assert f'"{dest}/bin/python3"' in rust, (
        f"the flat-layout fallback does not use {dest}/ either")


def test_the_declared_source_is_the_STAGED_env_and_not_the_editable_one():
    """The development environment must never be what ships.

    `pixi run dev-install` registers Core as an EDITABLE install: a finder in
    site-packages holding an absolute path back to this checkout. Bundling
    `.pixi/envs/default` therefore produces an `.app` whose interpreter resolves
    `import mrnaspect` to a directory that exists on one computer -- and it fails at run
    time, on the user's machine, not at build time on ours.

    `scripts/stage_bundle_env.sh` builds `dist/bundle-env` from that same environment
    with Core installed for real. This pins which of the two `tauri.conf.json` names.
    """
    source = next(iter(json.loads(_CONF.read_text())["bundle"]["resources"]))
    resolved = (_CONF.parent / source).resolve()
    root = _GUI.parent

    assert resolved != (root / ".pixi" / "envs" / "default").resolve(), (
        "tauri.conf.json ships the DEVELOPMENT env, whose mrnaspect is an editable pointer "
        "at this checkout. Ship dist/bundle-env (scripts/stage_bundle_env.sh) instead.")
    assert resolved == (root / "dist" / "bundle-env").resolve(), (
        f"the resource source resolves to {resolved}, not the staged bundle env")


def test_the_staging_script_builds_the_path_the_bundle_declares():
    """Two files naming one directory, so neither can be moved alone."""
    script = (_GUI.parent / "scripts" / "stage_bundle_env.sh")
    assert script.exists(), "scripts/stage_bundle_env.sh is gone; nothing builds the bundle env"
    source = next(iter(json.loads(_CONF.read_text())["bundle"]["resources"]))
    declared = (_CONF.parent / source).resolve().name
    assert f'DEST="$ROOT/dist/{declared}"' in script.read_text(), (
        f"the script does not build dist/{declared}, which is what the bundle declares")


def test_a_staged_env_carries_no_editable_pointer():
    """The property the whole arrangement exists for, checked on the artifact itself.

    Skipped until something has been staged -- the assertion needs the directory, and
    the build is where it comes from. `scripts/stage_bundle_env.sh` makes the same check
    at staging time, which is where it fires first.
    """
    staged = _GUI.parent / "dist" / "bundle-env"
    if not staged.is_dir():
        pytest.skip("no staged env here; run scripts/stage_bundle_env.sh")

    # No distribution name in the glob. It used to say `__editable__*mrnaspect*`, and renaming
    # the distribution to mrnaspect made that match nothing — setuptools names these after
    # the DISTRIBUTION, so the guard disarmed itself on a rename it was not watching.
    pointers = sorted(staged.glob("lib/python*/site-packages/__editable__*"))
    assert not pointers, f"the staged env still links back to a checkout: {pointers}"

    installed = sorted(staged.glob("lib/python*/site-packages/mrnaspect/__init__.py"))
    assert installed, "the staged env has no installed mrnaspect at all"


def test_the_interpreter_name_is_the_one_conda_actually_makes():
    """`bin/python3`, not `bin/python`.

    Both exist in a conda env and both are symlinks, but the Rust names `python3` and a
    test that accepted either would not notice the day one of them stopped being made.
    """
    rust = _LIB.read_text()
    # Within resolve_python: the shell names other files of the bundled env too (the
    # third parties' notices, since 2026-11-14), and this is about the interpreter.
    start = rust.index("fn resolve_python")
    body = rust[start:rust.index("\n}\n", start)]
    candidates = re.findall(r'"(\.\./Resources/[^"]+|env/[^"]+)"', body)
    assert candidates, "resolve_python no longer names any bundled candidate"
    for cand in candidates:
        assert cand.endswith("/bin/python3"), (
            f"{cand!r}: the bundled candidate must be bin/python3")


def test_an_explicit_override_is_still_an_error_when_it_does_not_resolve():
    """The bundle path is a FALLBACK. $QCRNA_PYTHON must not silently fall through to it:
    running a different Core than the operator named, and never saying so, is the shape
    this project keeps meeting."""
    rust = _LIB.read_text()
    i = rust.index("fn resolve_python")
    j = rust.index("../Resources/", i)
    override = rust[i:j]
    assert "return Err(" in override, (
        "an unresolvable $QCRNA_PYTHON must raise before the bundle candidates are tried")


def test_building_the_gui_stages_the_env_without_being_asked():
    """The staging step must be in npm's lifecycle, not in somebody's memory.

    `dist/bundle-env` is gitignored, so a fresh clone does not have it, and the failure is
    at the Rust build's resource check: `resource path '../../dist/bundle-env' doesn't
    exist` — an error that names the missing path and not the command that makes it. That
    happened on the first Mac build after the environment became a built artifact
    (2026-10-22).

    npm runs `pre<script>` synchronously before `<script>`, and `dev`/`build` are exactly
    what `tauri.conf.json`'s beforeDevCommand/beforeBuildCommand call — so hooking there
    covers both paths without the Tauri config knowing about it.
    """
    import json as _json

    pkg = _GUI / "package.json"
    if not pkg.exists():
        pytest.skip("no package.json in this checkout")
    scripts = _json.loads(pkg.read_text()).get("scripts", {})

    for hook, main in (("predev", "dev"), ("prebuild", "build")):
        assert hook in scripts, (
            f"npm has no {hook!r}, so `npm run {main}` does not stage the environment and "
            f"the Rust build fails on a missing resource path")
        assert "stage_bundle_env.sh" in scripts[hook], (
            f"{hook!r} does not run the staging script: {scripts[hook]!r}")


_STAGE = _GUI.parent / "scripts" / "stage_bundle_env.sh"


def test_a_folder_that_is_not_an_environment_is_refused_in_words(tmp_path):
    """2026-11-18, on the Mac: `.pixi/envs/default` existed and held only a font cache, and
    staging printed its first step and stopped with exit 1 and no message. `ls` of the
    missing conda-meta failed inside a pipe whose errors went to /dev/null, and `set -e`
    ended the script. A script that fails has to say what it found."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / ".pixi" / "envs" / "default" / "var" / "cache" / "fontconfig").mkdir(parents=True)
    shutil.copy(_STAGE, root / "scripts" / _STAGE.name)
    done = subprocess.run(["bash", str(root / "scripts" / _STAGE.name)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not a whole environment" in done.stderr, done.stdout + done.stderr
    assert "Clearing stale build artifacts" not in done.stdout, "it got past the check"
    text = _STAGE.read_text()
    assert 'ls "$SRC/conda-meta" 2>/dev/null' not in text, "the error is hidden again"


def test_the_font_cache_is_not_kept_in_the_build_folder():
    """fonts.conf names, first, a cache folder inside the environment as it was installed,
    and fontconfig creates that folder when it is missing. Run on the build machine with
    `.pixi` moved away, the app made a new `.pixi/envs/default` holding a font cache;
    moving the real one back then put it inside that folder. Staging takes the line out,
    so every Mac keeps the cache in the user's own folder."""
    text = _STAGE.read_text()
    step = text.index('step "Keeping the font cache out of the folder')
    assert 'sed -i.bak "\\\\|<cachedir>$SRC/|d" "$CONF"' in text
    assert text.index('grep -q "<cachedir>$SRC/" "$CONF"') > step
    assert step < text.index('step "Installing Core for real"'), (
        "before the checks that render a PDF, so they run on the configuration that ships")

