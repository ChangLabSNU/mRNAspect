#!/usr/bin/env python3
"""Put Core into a staged environment: the previous copy out whole, a built wheel in.

WHY THIS EXISTS. Staging reuses `dist/bundle-env` when the conda package set is unchanged,
and installed Core over the copy already there. Two things were wrong with how:

* It deleted the installed Core's `.dist-info`, `RECORD` with it, before installing, so
  the installer had no list of the old files and removed none. A module deleted or
  renamed in the source stayed in the app and still imported.
* It installed from the checkout by path, and the installer writes where it installed
  from into the package's metadata (`direct_url.json`): the build account's home, inside
  every app.

So this removes the previous Core while its record is still there, then whatever of the
package folder is left (a file no record ever listed), and installs the new one from a
wheel built for this call from a clean copy of the source, by name and version:

    <env>/bin/python -I scripts/bundle_install_core.py <env> --source <checkout>
    <env>/bin/python -I scripts/bundle_install_core.py <env> --source <checkout> --remove

`--remove` stops after the removal: staging runs it early, before the steps that read the
environment, and the install late. Afterwards the files of the installed package are
compared with the wheel's own list: one more or one fewer is a failure.

The package's command line launchers (`bin/mrnaspect`, `bin/mrnaspect-realtime`) are
taken out again: the installer writes each with the full path of this environment's
interpreter on its first line, which is the build machine's, and nothing in the app
starts them (the shell runs `python -m mrnaspect.main`). Last, every file the install
left is read: none may name the folder the environment is in.

Run by the environment's own interpreter, which is where pip and setuptools are; nothing
is downloaded (`--no-index`), and no dependency is touched (`--no-deps`): conda provides
those. Standard library only.

Exit 0: removed, or installed and verified. Exit 1: a step failed, and which is said.
Exit 2: not given an environment, or a source that is no Core checkout.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

#: What the wheel is built from, and nothing else of the checkout.
_SOURCE_FILES = ("pyproject.toml", "README.md", "LICENSE")
_PACKAGE = "mrnaspect"
#: Left out of the copy the wheel is built from: the tests (pyproject excludes them too),
#: bytecode, and a build record, which belongs to an installed copy and never to a source.
_NOT_COPIED = ("tests", "__pycache__", "_build.json")


def site_packages(env: Path) -> Path | None:
    """The environment's site-packages, or `None` when there is not exactly one."""
    found = sorted(p for p in env.glob("lib/python3*/site-packages") if p.is_dir())
    return found[0] if len(found) == 1 else None


def distribution(source: Path) -> str:
    """The distribution's name, from the source's pyproject.toml."""
    text = (source / "pyproject.toml").read_text(encoding="utf-8")
    name = re.search(r'^name = "([^"]+)"', text, re.M)
    if not name:
        raise ValueError(f"no distribution name in {source / 'pyproject.toml'}")
    return name.group(1)


def _pip(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-m", "pip", *args, "--quiet",
                           "--disable-pip-version-check"], capture_output=True, text=True)


def remove(env: Path, name: str) -> list[str]:
    """Take the installed Core out of `env`. Returns what could not be done, in words.

    pip first, while the record of the installed files is there: it removes what it
    installed, an editable install's finder included. Then what no record lists: the
    metadata folders by name, and the package folder with whatever is left in it.
    """
    site = site_packages(env)
    if site is None:
        return [f"{env} has no single lib/python3*/site-packages"]
    said: list[str] = []
    escaped = re.escape(name.replace("-", "_"))
    records = [p for p in site.iterdir()
               if re.fullmatch(rf"{escaped}-[^-]+\.dist-info", p.name, re.I)]
    if any((p / "RECORD").is_file() for p in records):
        done = _pip("uninstall", "--yes", name)
        if done.returncode != 0:
            said.append(f"pip uninstall {name} exited {done.returncode}: "
                        f"{(done.stderr or done.stdout).strip()[-400:]}")
        # pip removes a folder it has emptied, and Core can be all there was in one.
        site.mkdir(exist_ok=True)
    for leftover in [*site.glob("__editable__*"), *site.glob(f"{name.replace('-', '_')}-*.dist-info"),
                     site / _PACKAGE]:
        if leftover.is_dir() and not leftover.is_symlink():
            shutil.rmtree(leftover)
        elif leftover.exists() or leftover.is_symlink():
            leftover.unlink()
    still = [p.name for p in site.iterdir() if p.name == _PACKAGE
             or p.name.startswith("__editable__")
             or re.fullmatch(rf"{escaped}-[^-]+\.dist-info", p.name, re.I)]
    if still:
        said.append(f"still in {site}: {', '.join(sorted(still))}")
    return said


def build_wheel(source: Path, work: Path) -> Path:
    """A wheel of `source`, built from a clean copy under `work`.

    A copy, so that nothing is written into the checkout and nothing left there by an
    earlier build (setuptools reuses `build/lib`) can reach the wheel.

    Raises:
        RuntimeError: When the build fails, with what the builder said.
    """
    copy, out = work / "source", work / "wheel"
    copy.mkdir()
    out.mkdir()
    for name in _SOURCE_FILES:
        shutil.copy2(source / name, copy / name)
    shutil.copytree(source / _PACKAGE, copy / _PACKAGE,
                    ignore=shutil.ignore_patterns(*_NOT_COPIED))
    done = _pip("wheel", str(copy), "--no-deps", "--no-build-isolation", "--no-index",
                "--no-cache-dir", "--wheel-dir", str(out))
    wheels = sorted(out.glob("*.whl"))
    if done.returncode != 0 or len(wheels) != 1:
        raise RuntimeError(f"building the wheel failed (exit {done.returncode}, "
                           f"{len(wheels)} wheel(s)): {(done.stderr or done.stdout).strip()[-800:]}")
    return wheels[0]


def _launchers(info: Path) -> list[str]:
    """The names of the launchers the installer wrote into bin/ for this package."""
    try:
        text = (info / "entry_points.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    names, inside = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line in ("[console_scripts]", "[gui_scripts]")
        elif inside and "=" in line:
            names.append(line.split("=", 1)[0].strip())
    return names


def install(env: Path, wheel: Path, name: str) -> list[str]:
    """Install `wheel` into `env` by name and version, and compare what landed with it.

    By name from a folder of wheels, not by the wheel's path: installed by path, the
    package's metadata records that path.
    """
    site = site_packages(env)
    if site is None:
        return [f"{env} has no single lib/python3*/site-packages"]
    version = wheel.name.split("-")[1]
    # --no-compile: bytecode is written once, at the end of staging, in the form that
    # ships (scripts/bundle_bytecode.py); pip's would record this folder's full path.
    done = _pip("install", f"{name}=={version}", "--no-index", "--find-links",
                str(wheel.parent), "--no-deps", "--force-reinstall", "--no-cache-dir",
                "--no-compile")
    if done.returncode != 0:
        return [f"pip install {name}=={version} exited {done.returncode}: "
                f"{(done.stderr or done.stdout).strip()[-800:]}"]
    said: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        wanted = {n for n in archive.namelist()
                  if n.startswith(f"{_PACKAGE}/") and not n.endswith("/")}
    package = site / _PACKAGE
    landed = {p.relative_to(site).as_posix() for p in package.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    extra, missing = sorted(landed - wanted), sorted(wanted - landed)
    if extra:
        said.append(f"in the environment and not in the wheel: {', '.join(extra[:8])}")
    if missing:
        said.append(f"in the wheel and not in the environment: {', '.join(missing[:8])}")
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    if not (info / "RECORD").is_file():
        said.append(f"no RECORD in {info.name}: the next staging could not remove this install")
    if (info / "direct_url.json").exists():
        said.append(f"{info.name}/direct_url.json records where the install came from")
    launchers = _launchers(info)
    for launcher in launchers:
        (env / "bin" / launcher).unlink(missing_ok=True)
    here = str(env).encode()
    naming = sorted(p.relative_to(env).as_posix()
                    for p in [*info.rglob("*"), *package.rglob("*"),
                              *(env / "bin" / n for n in launchers)]
                    if p.is_file() and here in p.read_bytes())
    if naming:
        said.append(f"naming the folder this environment is in: {', '.join(naming[:8])}")
    if not said:
        print(f"  ok    {name} {version}: {len(wanted)} files, as the wheel lists them"
              + (f"; {len(launchers)} launcher(s) left out of bin/" if launchers else ""))
    return said


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace Core in a staged environment with a freshly built wheel.")
    parser.add_argument("env", type=Path, help="the staged environment")
    parser.add_argument("--source", type=Path, required=True, help="the Core checkout")
    parser.add_argument("--remove", action="store_true",
                        help="only take the installed Core out")
    args = parser.parse_args(argv)
    env, source = args.env.resolve(), args.source.resolve()
    if site_packages(env) is None:
        print(f"not an environment (no single lib/python3*/site-packages): {env}",
              file=sys.stderr)
        return 2
    if not (source / "pyproject.toml").is_file() or not (source / _PACKAGE).is_dir():
        print(f"not a Core checkout (no pyproject.toml, or no {_PACKAGE}/): {source}",
              file=sys.stderr)
        return 2
    try:
        name = distribution(source)
    except (OSError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2
    said = remove(env, name)
    if not said and not args.remove:
        with tempfile.TemporaryDirectory(prefix="mrnaspect-wheel.") as work:
            try:
                wheel = build_wheel(source, Path(work))
            except (OSError, RuntimeError) as exc:
                said = [str(exc)]
            else:
                said = install(env, wheel, name)
    elif not said:
        print(f"  ok    no {name} is installed in {env.name} now")
    for line in said:
        print(f"FAILED: {line}", file=sys.stderr)
    return 1 if said else 0


if __name__ == "__main__":
    sys.exit(main())
