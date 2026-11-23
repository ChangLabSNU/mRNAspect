#!/usr/bin/env python3
"""Check a release archive as the person who downloads it gets it.

WHY THIS EXISTS. Every other check runs where the app was built, on the tree it was built
in. Three things about the first release were only found by unpacking its archive
somewhere else and reading it: its files named the build account's home folder; its
results named a commit of a repository no reader can see; and nothing compared what was in
it with the release it was said to be.

    python3 scripts/check_release.py <archive.zip | .app | environment> \\
        --commit <the release's commit, in full> --source <a checkout of that commit> \\
        --forbid <a folder the app must not name> [--forbid ...] \\
        [--min weasyprint=70.0] [--absent pypdf] [--no-run]

A .zip is unpacked into a new temporary folder first, so what is read is what was packed.
The folder's name has a space in it ("Lab Tools"): the first release could not align a
read from such a place, and everything below that runs the app runs it from there.
Then, each a PASS or a FAIL:

1. no file names a forbidden folder: the build account's home and the checkout the app
   was built in, by the builder's own account of them (`--forbid`). Read from every
   file's bytes, and from the file names.
2. the build record names the release's commit, written from an unmodified tree, so a
   result made by the app names a commit a reader can look up
   (`bundle_build_record.py --check --expect`).
3. Core in the app is that commit's Core: every file of the installed package is in the
   commit, byte for byte, and nothing else is there but the build record. The licence
   the app carries for it is the commit's LICENSE, and its copyright line is printed.
4. the packages asked about are at least the versions given (`--min`), once each, and
   those named with `--absent` are not there in any form.
5. the third parties' notices list what the environment records (`make_notices.py`), and
   hold the whole licence texts of the plotly bundle the app carries.
6. where the app's own Python can run, which is a Mac for the released app: that Python's
   own answer for the versions of step 4; the existing bundle checks (`check_bundle.sh`,
   libraries and signature included); and a run of the fixture BAM by the app's Python,
   with its bundled minimap2 and samtools: 166 mapped reads on four constructs, the
   commit in full in `qc_result.json`, and its short form in the HTML and the PDF.

Nothing is run from the archive but its own interpreter, in step 6, and `--no-run` leaves
that out. Run it with the development environment's Python: reading the PDF needs pypdf,
which the app does not carry.

Exit 0: every check passed (a check that could not be asked here is said, and not counted
as passed). Exit 1: one failed. Exit 2: the arguments do not name a release.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
#: In the installed package and in no commit: the build record, and compiled Python.
_NOT_SOURCE = ("_build.json", "__pycache__")
#: The fixture run's expected result (mrnaspect/tests/golden/EXPLAINED_DIFF.md).
FIXTURE = {"bam": "mrnaspect/tests/data/small_aligned.bam",
           "reference": "mrnaspect/tests/data/curlcake_ref.fasta",
           "mapped": 166, "constructs": 4}


class Report:
    """PASS / FAIL lines, counted."""

    def __init__(self) -> None:
        self.passed = self.failed = 0

    def ok(self, what: str) -> None:
        self.passed += 1
        print(f"  PASS  {what}")

    def bad(self, what: str, details: list[str] | None = None) -> None:
        self.failed += 1
        print(f"  FAIL  {what}")
        for line in (details or [])[:12]:
            print(f"        {line}")
        if details and len(details) > 12:
            print(f"        and {len(details) - 12} more")

    def note(self, what: str) -> None:
        print(f"  NOTE  {what}")


def _load(name: str):
    """A sibling script as a module: `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def unpack(archive: Path, into: Path) -> None:
    """Unpack a .zip as macOS would: links and permissions kept.

    Raises:
        RuntimeError: When no tool here unpacks it, or the tool fails.
    """
    for tool in (["ditto", "-x", "-k", str(archive), str(into)],
                 ["unzip", "-q", str(archive), "-d", str(into)]):
        if shutil.which(tool[0]) is None:
            continue
        done = subprocess.run(tool, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"{tool[0]} exited {done.returncode}: {done.stderr.strip()[-400:]}")
        return
    raise RuntimeError("neither ditto nor unzip is here to unpack the archive")


def locate(given: Path, work: Path) -> tuple[Path | None, Path]:
    """`(app, env)` for a .zip, an .app or an environment. `app` is None for an environment.

    Raises:
        ValueError: When `given` holds no single app, or no environment.
    """
    if given.is_file():
        unpack(given, work)
        apps = sorted(p for p in work.rglob("*.app") if (p / "Contents").is_dir())
        if len(apps) != 1:
            raise ValueError(f"{given.name} holds {len(apps)} apps, not one")
        given = apps[0]
    if (given / "Contents").is_dir():
        env = given / "Contents" / "Resources" / "env"
        if not (env / "lib").is_dir():
            raise ValueError(f"{given.name} holds no environment at Contents/Resources/env")
        return given, env
    if (given / "lib").is_dir():
        return None, given
    raise ValueError(f"{given} is not a .zip, an .app or an environment")


def naming(root: Path, folders: list[str]) -> dict[str, list[str]]:
    """Folder -> the files under `root` that name it, in their bytes or their own path."""
    needles = {folder: folder.rstrip("/").encode() for folder in folders}
    found: dict[str, list[str]] = {folder: [] for folder in folders}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = str(path.readlink()).encode()
            for folder, needle in needles.items():
                if needle in target:
                    found[folder].append(f"{rel} (a link to it)")
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        for folder, needle in needles.items():
            if needle in data or needle in rel.encode():
                found[folder].append(rel)
    return found


def package_folder(env: Path) -> Path | None:
    found = sorted(p.parent for p in env.glob("lib/python3*/site-packages/mrnaspect/__init__.py"))
    return found[0] if len(found) == 1 else None


def _git(source: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-C", str(source), *args], capture_output=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} exited {done.returncode}: "
                           f"{done.stderr.decode(errors='replace').strip()[-300:]}")
    return done.stdout


def not_the_commits(env: Path, source: Path, commit: str) -> list[str]:
    """What of the installed Core is not the commit's, in words; empty when all of it is.

    Raises:
        RuntimeError: When git cannot answer for `commit` in `source`.
    """
    package = package_folder(env)
    if package is None:
        return ["Core is not installed in the environment exactly once"]
    listed = _git(source, "ls-tree", "-r", "--name-only", "-z", commit, "--", "mrnaspect/")
    in_commit = {name for name in listed.decode().split("\0") if name}
    said: list[str] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file() or any(part in _NOT_SOURCE for part in path.parts):
            continue
        name = "mrnaspect/" + path.relative_to(package).as_posix()
        if name not in in_commit:
            said.append(f"{name}: in the app and not in the commit")
        elif _git(source, "cat-file", "blob", f"{commit}:{name}") != path.read_bytes():
            said.append(f"{name}: differs from the commit's")
    return said


def licence_of_core(env: Path, source: Path, commit: str) -> tuple[str | None, str]:
    """`(copyright line, what is wrong)` for the licence the app carries for Core.

    The installed package's own licence file must be the commit's LICENSE, byte for byte.
    The copyright line is returned to be printed: who is named is for a person to read.

    Raises:
        RuntimeError: When git cannot give the commit's LICENSE.
    """
    found = sorted(env.glob("lib/python3*/site-packages/mrnaspect-*.dist-info/licenses/LICENSE"))
    if len(found) != 1:
        return None, f"{len(found)} licence files for Core in the app, not one"
    text = found[0].read_bytes()
    line = next((l.strip() for l in text.decode("utf-8", "replace").splitlines()
                 if l.startswith("Copyright")), None)
    if text != _git(source, "cat-file", "blob", f"{commit}:LICENSE"):
        return line, "the licence in the app differs from the commit's LICENSE"
    if line is None:
        return None, "the licence in the app has no copyright line"
    return line, ""


def bundle_notices_missing(env: Path) -> list[str]:
    """What of the plotly bundle's licence texts the app's notices do not hold.

    The app carries the bundle twice (the report's copy in Core, the window's own), and
    its notices file is where a reader of the app finds the terms. Each texts file
    beside a bundle in the installed Core must be in the notices whole.
    """
    notices = env / "THIRD-PARTY-NOTICES.txt"
    said: list[str] = []
    assets = sorted(env.glob("lib/python3*/site-packages/mrnaspect/reporting/assets"))
    bundles = [b for folder in assets for b in sorted(folder.glob("plotly-*.min.js"))]
    if not bundles:
        return ["no plotly bundle in the installed Core"]
    held = notices.read_text(encoding="utf-8", errors="replace") if notices.is_file() else ""
    for bundle in bundles:
        texts = bundle.with_name(bundle.name.removesuffix(".min.js") + ".LICENSES.txt")
        if not texts.is_file():
            said.append(f"{texts.name} is not beside {bundle.name} in the installed Core")
        elif texts.read_text(encoding="utf-8").strip() not in held:
            said.append(f"the notices do not hold {texts.name} whole")
    return said


def bundled_answers(python: Path, floors: dict[str, str], absent: list[str]) -> list[str]:
    """What the app's own Python says against the versions asked for; empty when it agrees.

    The records read in step 4 say what was installed. This asks the interpreter that
    will run: the version it resolves for each package, and that an absent one does not
    import.
    """
    code = ("import importlib.metadata as m, importlib.util as u, json, sys\n"
            "names, gone = json.loads(sys.argv[1]), json.loads(sys.argv[2])\n"
            "out = {'versions': {}, 'importable': []}\n"
            "for n in names:\n"
            "    try: out['versions'][n] = m.version(n)\n"
            "    except m.PackageNotFoundError: out['versions'][n] = None\n"
            "out['importable'] = [n for n in gone if u.find_spec(n) is not None]\n"
            "print(json.dumps(out))\n")
    done = subprocess.run([str(python), "-I", "-B", "-c", code, json.dumps(sorted(floors)),
                           json.dumps(sorted(absent))], capture_output=True, text=True,
                          timeout=300)
    if done.returncode != 0:
        return [f"the app's Python could not be asked (exit {done.returncode}): "
                f"{done.stderr.strip()[-300:]}"]
    said = json.loads(done.stdout)
    wrong = [f"{name}: the app's Python has {have or 'none'}, wanted at least {floors[name]}"
             for name, have in said["versions"].items()
             if have is None or _version(have) < _version(floors[name])]
    wrong += [f"{name} imports in the app's Python" for name in said["importable"]]
    return wrong


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text.split("+")[0])[:4])


def installed(env: Path) -> dict[str, list[str]]:
    """Normalised package name -> every version of it the environment records.

    From conda's records and from each `.dist-info` in site-packages: a package can be
    in either, and a superseded copy left beside a new one shows as two versions.
    """
    found: dict[str, set[str]] = {}

    def add(name: str, version: str) -> None:
        found.setdefault(re.sub(r"[-_.]+", "-", name).lower(), set()).add(version)

    for record in (env / "conda-meta").glob("*.json"):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("name") and data.get("version"):
            add(str(data["name"]), str(data["version"]))
    for info in env.glob("lib/python3*/site-packages/*.dist-info"):
        match = re.fullmatch(r"(.+)-([^-]+)\.dist-info", info.name)
        if match:
            add(match.group(1), match.group(2))
    return {name: sorted(versions) for name, versions in found.items()}


def traces_of(env: Path, name: str) -> list[str]:
    """Where a package that should be absent still is: a record, its metadata, its code."""
    plain = re.sub(r"[-_.]+", "-", name).lower()
    found = [f"recorded as {v}" for v in installed(env).get(plain, [])]
    for site in env.glob("lib/python3*/site-packages"):
        for candidate in (site / plain.replace("-", "_"), site / f"{plain.replace('-', '_')}.py"):
            if candidate.exists():
                found.append(candidate.relative_to(env).as_posix())
    return found


def runs_here(python: Path) -> bool:
    """Whether the environment's interpreter starts on this computer."""
    try:
        done = subprocess.run([str(python), "-I", "-B", "-c", "print('ok')"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "ok"


def fixture_run(python: Path, source: Path, commit: str, work: Path, report: Report) -> None:
    """Run the fixture BAM through the app's Python and read what it wrote."""
    bam, reference = source / FIXTURE["bam"], source / FIXTURE["reference"]
    if not bam.is_file() or not reference.is_file():
        report.bad(f"the fixture is not in {source} ({FIXTURE['bam']})")
        return
    inputs, out = work / "fixture-in", work / "fixture-out"
    inputs.mkdir()
    shutil.copy(bam, inputs / bam.name)
    done = subprocess.run(
        [str(python), "-I", "-B", "-m", "mrnaspect.main", "--mode", "bam",
         "--input-dir", str(inputs), "--reference", str(reference),
         "--output-dir", str(out), "--output-name", "release-check", "--min-reads", "1"],
        capture_output=True, text=True, timeout=1800, cwd=str(work))
    run = out / "release-check"
    result_file = run / "qc_result.json"
    if done.returncode != 0 or not result_file.is_file():
        report.bad(f"the app's Python did not finish the fixture run (exit {done.returncode})",
                   (done.stderr or done.stdout).strip().splitlines()[-6:])
        return
    result = json.loads(result_file.read_text(encoding="utf-8"))
    mapped = result["snapshots"]["run_summary"]["mapping"]["mapped"]
    constructs = len(result["qc_metrics"]["constructs"])
    if (mapped, constructs) == (FIXTURE["mapped"], FIXTURE["constructs"]):
        report.ok(f"the fixture run: {mapped} mapped reads on {constructs} constructs")
    else:
        report.bad(f"the fixture run gave {mapped} mapped reads on {constructs} constructs, "
                   f"not {FIXTURE['mapped']} on {FIXTURE['constructs']}")
    stamped = result.get("provenance", {})
    if stamped.get("git_commit") == commit and stamped.get("git_dirty") is False:
        report.ok(f"qc_result.json names commit {commit}")
    else:
        report.bad(f"qc_result.json names commit {stamped.get('git_commit')!r} "
                   f"(modified tree: {stamped.get('git_dirty')!r}), not {commit}")
    short = commit[:7]
    html = (run / "report_latest.html").read_text(encoding="utf-8")
    row = re.search(r"Source commit</th>\s*<td[^>]*>([^<]*)<", html)
    if row and row.group(1).strip() == short:
        report.ok(f"the HTML report's Source commit row says {short}")
    else:
        report.bad(f"the HTML report's Source commit row says "
                   f"{row.group(1).strip() if row else 'nothing'!r}, not {short}")
    pdfs = sorted(run.glob("*.pdf"))
    if not pdfs:
        report.bad("the fixture run wrote no PDF")
        return
    try:
        import pypdf
    except ImportError:
        report.bad("the PDF was not read: this Python has no pypdf (run the check with the "
                   "development environment's Python)")
        return
    text = " ".join(page.extract_text() or "" for page in pypdf.PdfReader(str(pdfs[-1])).pages)
    if re.search(rf"Source commit\s+{short}\b", text):
        report.ok(f"the PDF report says Source commit {short}")
    else:
        report.bad(f"the PDF report does not say Source commit {short}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a release archive as the person who downloads it gets it.")
    parser.add_argument("release", type=Path, help="the .zip, an .app, or an environment")
    parser.add_argument("--commit", required=True, help="the release's commit, 40 hex")
    parser.add_argument("--source", type=Path, required=True,
                        help="a checkout that holds that commit")
    parser.add_argument("--forbid", action="append", default=[], metavar="FOLDER",
                        help="a folder no file of the app may name (repeat)")
    parser.add_argument("--min", action="append", default=[], metavar="NAME=VERSION",
                        help="a package that must be at least this version (repeat)")
    parser.add_argument("--absent", action="append", default=[], metavar="NAME",
                        help="a package that must not be in the app (repeat)")
    parser.add_argument("--no-run", action="store_true",
                        help="read only: start nothing from the release")
    args = parser.parse_args(argv)
    if not _COMMIT.match(args.commit):
        print(f"--commit is not a commit in full (40 hexadecimal characters): {args.commit}",
              file=sys.stderr)
        return 2
    if not args.release.exists() or not (args.source / ".git").exists():
        print("the release does not exist, or --source is not a git checkout", file=sys.stderr)
        return 2
    if not args.forbid:
        print("no --forbid folder: name at least the build account's home and the checkout "
              "the app was built in", file=sys.stderr)
        return 2
    floors = dict(pair.split("=", 1) for pair in args.min if "=" in pair)
    if len(floors) != len(args.min):
        print("--min takes NAME=VERSION", file=sys.stderr)
        return 2
    report = Report()
    with tempfile.TemporaryDirectory(prefix="mrnaspect-release.") as tmp:
        work = Path(tmp)
        # A space in the folder's name, on purpose: see the module docstring.
        (work / "Lab Tools").mkdir()
        try:
            app, env = locate(args.release.resolve(), work / "Lab Tools")
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"not a release: {exc}", file=sys.stderr)
            return 2
        root = app if app is not None else env
        n_files = sum(1 for p in root.rglob("*") if p.is_file() and not p.is_symlink())
        print(f"\n== {args.release.name}: {root.name}, {n_files} files")
        print(f"   read at {root}\n")

        print("== 1. no file names a folder of the build machine it must not")
        for folder, files in naming(root, args.forbid).items():
            if files:
                report.bad(f"{len(files)} files name {folder}", files)
            else:
                report.ok(f"no file names {folder}")

        print("\n== 2. the build record names the release's commit")
        record = _load("bundle_build_record")
        if record.check(env, None, args.commit) == 0:
            report.ok(f"built from {args.commit}, from an unmodified tree")
        else:
            report.bad("the build record is missing, or is of another commit or a modified tree")

        print("\n== 3. Core in the app is that commit's Core")
        try:
            differences = not_the_commits(env, args.source.resolve(), args.commit)
        except RuntimeError as exc:
            differences = [str(exc)]
        if differences:
            report.bad("the installed Core is not the commit's", differences)
        else:
            report.ok("every file of the installed Core is in the commit, byte for byte")
        try:
            holder, wrong = licence_of_core(env, args.source.resolve(), args.commit)
        except RuntimeError as exc:
            holder, wrong = None, str(exc)
        if wrong:
            report.bad(wrong, [holder] if holder else None)
        else:
            report.ok(f"the app carries the commit's LICENSE for Core: {holder}")

        print("\n== 4. the packages asked about")
        have = installed(env)
        for name, floor in sorted(floors.items()):
            versions = have.get(re.sub(r"[-_.]+", "-", name).lower(), [])
            if len(versions) == 1 and _version(versions[0]) >= _version(floor):
                report.ok(f"{name} {versions[0]} (at least {floor})")
            else:
                report.bad(f"{name}: {', '.join(versions) or 'not there'}; wanted one copy, "
                           f"at least {floor}")
        for name in sorted(args.absent):
            traces = traces_of(env, name)
            if traces:
                report.bad(f"{name} is in the app", traces)
            else:
                report.ok(f"{name} is not in the app")
        if not floors and not args.absent:
            report.note("none asked about (--min, --absent)")

        print("\n== 5. the third parties' notices list what the environment records")
        problems = _load("make_notices").check(env)
        if problems:
            report.bad("the notices are missing or incomplete", problems)
        else:
            report.ok("the notices list every package the environment records")
        lacking = bundle_notices_missing(env)
        if lacking:
            report.bad("the notices do not carry the plotly bundle's licence texts", lacking)
        else:
            report.ok("the notices hold the plotly bundle's licence texts whole")

        print("\n== 6. the app, run")
        python = env / "bin" / "python3"
        if args.no_run:
            report.note("not asked (--no-run)")
        elif not runs_here(python):
            report.note(f"the app's Python does not start on this computer "
                        f"({platform.system()} {platform.machine()}): run this on a Mac")
        else:
            if " " in str(python):
                report.note("run from a folder with a space in its name")
            if floors or args.absent:
                wrong = bundled_answers(python, floors, args.absent)
                if wrong:
                    report.bad("the app's Python does not agree on the packages", wrong)
                else:
                    report.ok("the app's Python reports those versions, and imports none "
                              "of the absent ones")
            if app is not None and platform.system() == "Darwin":
                done = subprocess.run(["bash", str(HERE / "check_bundle.sh"), str(app),
                                       args.commit], capture_output=True, text=True)
                tail = [line for line in done.stdout.splitlines() if line.strip()][-1:]
                if done.returncode == 0:
                    report.ok(f"check_bundle.sh: {tail[0].strip() if tail else 'passed'}")
                else:
                    report.bad("check_bundle.sh failed",
                               [line for line in done.stdout.splitlines() if "FAIL" in line])
            else:
                report.note("check_bundle.sh is for an .app on macOS: not asked")
            fixture_run(python, args.source.resolve(), args.commit, work, report)

    print(f"\n{report.passed} passed, {report.failed} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
