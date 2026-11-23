"""A release is built from a new clone of its tag, in a folder that names no account.

`scripts/build_release.sh` runs on the Mac that builds the app, and most of it cannot run
anywhere else. What can be held here: where it agrees to build, which is what decides
whether the app's files name the build account's home folder; and the order of what it
does, which is what decides that the archive is checked as it will be downloaded.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "build_release.sh"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")


def _run(tmp_path: Path, build: Path, *, home: Path | None = None) -> tuple[int, str]:
    """The script with stand-ins for the four tools, so that only its own guards speak."""
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for name in ("git", "pixi", "npm", "cargo"):
        (tools / name).write_text("#!/bin/sh\necho \"stand-in $0 was started: $*\" >&2\nexit 97\n")
        (tools / name).chmod(0o755)
    env = {"PATH": f"{tools}{os.pathsep}/usr/bin:/bin", "HOME": str(home or tmp_path / "home"),
           "MRNASPECT_BUILD_ROOT": str(build)}
    (tmp_path / "home").mkdir(exist_ok=True)
    done = subprocess.run(["bash", str(_SCRIPT), "v0.1.0", str(tmp_path / "nowhere")],
                          capture_output=True, text=True, env=env, timeout=60)
    return done.returncode, done.stdout + done.stderr


def test_a_build_folder_that_does_not_exist_is_said_with_how_to_make_it(tmp_path: Path) -> None:
    code, out = _run(tmp_path, tmp_path / "absent")
    assert code == 1 and "no build folder" in out and "sudo mkdir -p" in out
    assert "stand-in" not in out, "nothing was started"


def test_a_build_folder_under_the_home_folder_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "build").mkdir(parents=True)
    code, out = _run(tmp_path, home / "build", home=home)
    assert code == 1 and "under this account's home folder" in out
    assert "stand-in" not in out


def test_a_build_folder_any_user_could_create_is_refused(tmp_path: Path) -> None:
    """Its path ends up in the app as a place libraries look in: on the computer that
    runs the app it must not be a folder an ordinary user can make."""
    shared = tmp_path / "shared"
    (shared / "build").mkdir(parents=True)
    shared.chmod(0o1777)
    try:
        code, out = _run(tmp_path, shared / "build")
    finally:
        shared.chmod(0o755)
    assert code == 1 and "can be written by any user" in out
    assert "stand-in" not in out


def test_a_build_folder_with_something_in_it_is_refused(tmp_path: Path) -> None:
    build = tmp_path / "opt" / "build"
    (build / "src").mkdir(parents=True)
    code, out = _run(tmp_path, build)
    assert code == 1 and "is not empty" in out and "starts from nothing" in out
    assert "stand-in" not in out
    assert (build / "src").is_dir(), "the script removes nothing itself"


def test_an_empty_build_folder_in_a_safe_place_gets_as_far_as_the_clone(tmp_path: Path) -> None:
    build = tmp_path / "opt" / "build"
    build.mkdir(parents=True)
    code, out = _run(tmp_path, build)
    assert code == 1
    assert "Cloning v0.1.0" in out and "stand-in" in out and "git was started: clone" in out
    assert "could not clone v0.1.0" in out


def test_the_steps_are_in_the_order_that_makes_the_check_mean_something() -> None:
    text = _SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    order = ["git clone --quiet --depth 1 --branch \"$TAG\"",
             "pixi config set --local detached-environments \"$BUILD/env\"",
             "pixi install --frozen",
             "npm ci",
             # The three lock files are what is built: cargo must not write a new one.
             "cargo metadata --locked",
             # And what they resolve to is asked of the advisories again, before building.
             "npm audit --include=dev",
             "npm run tauri build -- --bundles app",
             "the build changed a tracked file of the clone",
             "check_bundle.sh\" \"$APP\" \"$COMMIT\"",
             "ditto -c -k --keepParent \"$APP\" \"$ZIP\"",
             "scripts/check_release.py \"$ZIP\""]
    at = [code.index(step) for step in order]
    assert at == sorted(at), "a step moved"
    # The archive is what is checked, against the tag's commit, the home folder and the
    # checkout; and the script neither uploads nor moves a tag.
    assert '--commit "$COMMIT" --source "$SRC" --forbid "$(cd "$HOME" && pwd -P)" --forbid "$SRC"' in code
    for never in ("gh release", "git push", "git tag ", "rm -rf \"$"):
        assert never not in code, never
    # Every cache and Rust's registry are in the build folder, not in the home folder.
    for name in ("PIXI_CACHE_DIR", "npm_config_cache", "CARGO_HOME"):
        assert re.search(rf'^export {name}="\$BUILD/', code, re.M), name
    assert "--remap-path-prefix=$SRC=" in code and "--remap-path-prefix=$HOME=" in code


def test_the_versions_the_archive_is_held_to_are_the_ones_the_lock_gives() -> None:
    """What the script asks of the archive must be what the lock file installs, or every
    release would fail its own check."""
    text = _SCRIPT.read_text()
    floors = dict(pair.split("=") for pair in
                  re.search(r"^MIN_VERSIONS=\(([^)]*)\)", text, re.M).group(1).split())
    lock = (_ROOT / "pixi.lock").read_text()
    for name, floor in floors.items():
        locked = set(re.findall(rf"/{name}-(\d[\w.]*?)-(?:py|h)", lock)
                     + re.findall(rf"/{name}-(\d[\w.]*?)-py3-none-any\.whl", lock))
        assert locked, f"{name} is not in pixi.lock"
        assert all(tuple(map(int, re.findall(r"\d+", v)[:3])) >=
                   tuple(map(int, re.findall(r"\d+", floor)[:3])) for v in locked), (name, locked)
