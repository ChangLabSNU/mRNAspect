"""The staged environment carries no pixi note naming the checkout (2026-11-23).

`conda-meta/pixi` is pixi's record of the environment it manages, holding the full path
of the checkout's pixi.toml. Nothing in the app reads it, and `cp -a` of the environment
copied it into the app, where the first release build's read-back failed on it:

    == 1. no file names a folder of the build machine it must not
      FAIL  1 files name /opt/mrnaspect-build/src
            Contents/Resources/env/conda-meta/pixi

`stage_bundle_env.sh` now removes it after the copy, on a reused tree too. Held here: the
step's own lines, run on a directory shaped like the environment, and where it sits.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"
_HEAD = 'step "Leaving out pixi\'s note of the project this environment belongs to"'

pytestmark = pytest.mark.skipif(
    not _STAGE.exists(), reason="scripts/ is not in this checkout")


def _step() -> str:
    text = _STAGE.read_text()
    start = text.index(_HEAD)
    end = text.index("\n\n", text.index("conda-meta/pixi left out", start))
    return text[start:end]


def test_the_step_removes_the_note_and_keeps_the_package_records(tmp_path: Path) -> None:
    dest, root = tmp_path / "bundle-env", tmp_path / "checkout"
    (dest / "conda-meta").mkdir(parents=True)
    (dest / "conda-meta" / "pixi").write_text(json.dumps(
        {"manifest_path": f"{root}/pixi.toml", "environment_name": "default"}))
    record = dest / "conda-meta" / "python-3.14.0-h0.json"
    record.write_text("{}")
    script = ("set -euo pipefail\n"
              "fail() { printf 'FAIL %s\\n' \"$*\" >&2; exit 1; }\n"
              "step() { printf '==> %s\\n' \"$*\"; }\n"
              f"DEST={dest}\nROOT={root}\n" + _step() + "\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not (dest / "conda-meta" / "pixi").exists()
    assert record.exists(), "the package records are what the notices are written from"
    assert "conda-meta/pixi left out" in r.stdout
    # Run again, as on a reused tree with nothing left to remove: still fine.
    assert subprocess.run(["bash", "-c", script], capture_output=True).returncode == 0


def test_the_step_runs_on_a_reused_tree_and_before_anything_is_checked() -> None:
    text = _STAGE.read_text()
    at = text.index(_HEAD)
    # After the copy-or-reuse block closes, so a tree staged earlier loses it too.
    assert text.index('cp -a "$SRC" "$DEST"') < text.index("\nfi\n") < at
    for later in ('step "Installing Core for real"', 'step "Verifying"',
                  'step "Writing the third parties\' notices"'):
        assert at < text.index(later), later
    assert 'rm -f "$DEST/conda-meta/pixi"' in _step()
    assert '[ ! -e "$DEST/conda-meta/pixi" ] || fail' in _step()
