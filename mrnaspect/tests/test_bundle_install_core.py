"""Core goes into the staged environment whole: the old copy out, a built wheel in.

Staging reuses `dist/bundle-env` while the conda package set is unchanged, and used to
delete the installed Core's `.dist-info` (and its `RECORD`) before installing over the
copy already there. The installer then had no list of the old files: a module deleted or
renamed in the source stayed in the app and still imported. It also installed from the
checkout by path, which the installer writes into the package's metadata.

`scripts/bundle_install_core.py` is what staging runs now. It is run here for real, with
the real pip and setuptools, on a small environment that stands in for the staged one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "bundle_install_core.py"
_STAGE = _ROOT / "scripts" / "stage_bundle_env.sh"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(), reason="scripts/ is not in this checkout")


def _environment(parent: Path) -> Path:
    """A small environment with this interpreter's pip and setuptools in reach."""
    env = parent / "env"
    done = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages",
                           "--without-pip", str(env)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    probe = subprocess.run([str(env / "bin" / "python"), "-I", "-c",
                            "import pip, setuptools"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("pip and setuptools are not importable from a new environment here")
    return env


def _source(parent: Path) -> Path:
    """A copy of what Core's wheel is built from, to edit freely."""
    source = parent / "checkout"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(_ROOT / name, source / name)
    shutil.copytree(_ROOT / "mrnaspect", source / "mrnaspect",
                    ignore=shutil.ignore_patterns("tests", "__pycache__", "_build.json"))
    return source


def _install(env: Path, source: Path, *more: str,
             python: Path | None = None) -> subprocess.CompletedProcess:
    """Run the script as staging does: by the environment's own interpreter."""
    return subprocess.run([str(python or env / "bin" / "python"), "-I", str(_SCRIPT),
                           str(env), "--source", str(source), *more],
                          capture_output=True, text=True)


def _site(env: Path) -> Path:
    return next(env.glob("lib/python3*/site-packages"))


def _imports(env: Path, module: str) -> subprocess.CompletedProcess:
    """Import `module` in `env`, from outside any checkout, and print where it is.

    `-B`: as the app starts Python, and so that looking writes no bytecode of its own."""
    return subprocess.run([str(env / "bin" / "python"), "-I", "-B", "-c",
                           f"import {module} as m; print(m.__file__)"],
                          capture_output=True, text=True, cwd="/")


def test_a_module_removed_or_renamed_in_the_source_is_gone_after_the_next_staging(
        tmp_path: Path) -> None:
    """The reported case, and its neighbours: two ordinary stagings of one environment."""
    env, source = _environment(tmp_path), _source(tmp_path)
    (source / "mrnaspect" / "staging_probe.py").write_text("")
    (source / "mrnaspect" / "old_name.py").write_text("VALUE = 1\n")
    first = _install(env, source)
    assert first.returncode == 0, first.stdout + first.stderr
    package = _site(env) / "mrnaspect"
    assert _imports(env, "mrnaspect.staging_probe").returncode == 0
    # Two files no record lists: one dropped in by hand, and the build record staging
    # writes after the install.
    (package / "orphan.py").write_text("")
    (package / "_build.json").write_text("{}")

    (source / "mrnaspect" / "staging_probe.py").unlink()
    (source / "mrnaspect" / "old_name.py").rename(source / "mrnaspect" / "new_name.py")
    second = _install(env, source)
    assert second.returncode == 0, second.stdout + second.stderr

    for gone in ("staging_probe", "old_name", "orphan"):
        assert not (package / f"{gone}.py").exists(), gone
        tried = _imports(env, f"mrnaspect.{gone}")
        assert tried.returncode != 0 and "ModuleNotFoundError" in tried.stderr, gone
    assert not (package / "_build.json").exists()
    here = _imports(env, "mrnaspect.new_name")
    assert here.returncode == 0 and here.stdout.strip() == str(package / "new_name.py")


def test_the_install_says_nothing_of_where_it_came_from(tmp_path: Path) -> None:
    """An environment copied from the development one carries an editable install: a
    finder pointing back at the checkout. After this, Core is in the environment, the
    finder is gone, and no installed file names the folder it was built from."""
    env, source = _environment(tmp_path), _source(tmp_path)
    site = _site(env)
    editable = subprocess.run(
        [str(env / "bin" / "python"), "-I", "-m", "pip", "install", "-e", str(source),
         "--no-deps", "--no-build-isolation", "--no-index", "--quiet"],
        capture_output=True, text=True)
    assert editable.returncode == 0, editable.stderr
    assert list(site.glob("__editable__*")), "the stand-in for the inherited install"
    before = sorted(p.relative_to(source).as_posix() for p in source.rglob("*")
                    if "egg-info" not in p.as_posix())

    done = _install(env, source)
    assert done.returncode == 0, done.stdout + done.stderr

    assert not list(site.glob("__editable__*"))
    where = _imports(env, "mrnaspect")
    assert where.stdout.strip() == str(site / "mrnaspect" / "__init__.py")
    info = [p for p in site.glob("mrnaspect-*.dist-info")]
    assert len(info) == 1 and (info[0] / "RECORD").is_file()
    assert not (info[0] / "direct_url.json").exists()
    for path in [*info[0].rglob("*"), *(site / "mrnaspect").rglob("*")]:
        if path.is_file():
            assert str(tmp_path).encode() not in path.read_bytes(), path
    # The launchers pip writes start with this environment's full path; the app starts
    # Core as `python -m mrnaspect.main`, so they are left out.
    assert "mrnaspect = " in (info[0] / "entry_points.txt").read_text()
    assert not (env / "bin" / "mrnaspect").exists()
    assert not (env / "bin" / "mrnaspect-realtime").exists()
    assert "2 launcher(s) left out of bin/" in done.stdout
    runs = subprocess.run([str(env / "bin" / "python"), "-I", "-B", "-m", "mrnaspect.main",
                           "--help"], capture_output=True, text=True, cwd="/")
    assert runs.returncode == 0 and "--input-dir" in runs.stdout
    # Built from a copy: nothing was written into the checkout.
    after = sorted(p.relative_to(source).as_posix() for p in source.rglob("*")
                   if "egg-info" not in p.as_posix())
    assert after == before
    assert not (source / "build").exists()
    # Compiled once, at the end of staging, in the form that ships: not here.
    assert not list((site / "mrnaspect").rglob("*.pyc"))


def test_remove_only_takes_core_out_and_installs_nothing(tmp_path: Path) -> None:
    env, source = _environment(tmp_path), _source(tmp_path)
    assert _install(env, source).returncode == 0
    done = _install(env, source, "--remove")
    assert done.returncode == 0, done.stdout + done.stderr
    site = _site(env)
    assert not (site / "mrnaspect").exists()
    assert not list(site.glob("mrnaspect-*.dist-info"))


def test_what_is_not_an_environment_or_not_a_checkout_is_refused(tmp_path: Path) -> None:
    env, source = _environment(tmp_path), _source(tmp_path)
    (tmp_path / "empty").mkdir()
    python = env / "bin" / "python"
    assert _install(tmp_path / "empty", source, python=python).returncode == 2
    assert _install(env, tmp_path / "empty").returncode == 2


def test_staging_removes_core_by_its_record_and_installs_a_wheel() -> None:
    """The two calls, in order, and neither of the two things that went wrong."""
    stage = _STAGE.read_text()
    remove = stage.index('bundle_install_core.py" "$DEST" --source "$ROOT" --remove')
    install = stage.index('bundle_install_core.py" "$DEST" --source "$ROOT" \\\n')
    assert remove < stage.index("strip_bundle_env.py") < install
    assert install < stage.index("bundle_build_record.py")
    code = "\n".join(line for line in stage.splitlines() if not line.lstrip().startswith("#"))
    assert 'pip install "$ROOT"' not in code, "installing by path records the path"
    assert ".dist-info" not in code, "the record is pip's to remove, with the files it lists"


def test_two_ordinary_stagings_drop_a_module_removed_between_them(tmp_path: Path) -> None:
    """The staging script itself, run twice on a copy of this checkout, as it is run
    before a build: the second run reuses the staged environment, which is where the
    stale module used to survive.

    Slow (it copies the environment once), and skipped where there is no pixi
    environment to stage from.
    """
    env = _ROOT / ".pixi" / "envs" / "default"
    if not (env / "conda-meta").is_dir() or not (env / "bin" / "python").exists():
        pytest.skip("no pixi environment here to stage from")
    copy = tmp_path / "checkout"
    shutil.copytree(_ROOT, copy, symlinks=True, ignore=shutil.ignore_patterns(
        ".git", ".pixi", "dist", "build", "node_modules", "target", "__pycache__",
        ".pytest_shards", ".pytest_cache", "*.egg-info"))
    (copy / ".pixi").symlink_to(_ROOT / ".pixi")
    staged = copy / "dist" / "bundle-env"

    def stage() -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(copy / "scripts" / "stage_bundle_env.sh")],
                              capture_output=True, text=True, timeout=1500, cwd=str(copy))

    def probe(module: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(staged / "bin" / "python"), "-I", "-B", "-c",
                               f"import mrnaspect.{module}"],
                              capture_output=True, text=True, cwd="/")

    try:
        (copy / "mrnaspect" / "staging_probe.py").write_text("")
        (copy / "mrnaspect" / "old_name.py").write_text("VALUE = 1\n")
        first = stage()
        assert first.returncode == 0, first.stdout[-3000:] + first.stderr[-2000:]
        assert probe("staging_probe").returncode == 0 and probe("old_name").returncode == 0

        (copy / "mrnaspect" / "staging_probe.py").unlink()
        (copy / "mrnaspect" / "old_name.py").rename(copy / "mrnaspect" / "new_name.py")
        second = stage()
        assert second.returncode == 0, second.stdout[-3000:] + second.stderr[-2000:]
        assert "Reusing" in second.stdout, "the second staging did not reuse the environment"

        package = next(staged.glob("lib/python3*/site-packages/mrnaspect"))
        for gone in ("staging_probe", "old_name"):
            assert not (package / f"{gone}.py").exists(), gone
            tried = probe(gone)
            assert tried.returncode != 0 and "ModuleNotFoundError" in tried.stderr, gone
        assert probe("new_name").returncode == 0
        # And nothing of the staged Core names the folder it was staged in.
        for path in package.rglob("*"):
            if path.is_file():
                assert str(copy).encode() not in path.read_bytes(), path
    finally:
        shutil.rmtree(staged, ignore_errors=True)

