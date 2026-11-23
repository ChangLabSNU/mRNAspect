#!/usr/bin/env python3
"""Write, into the staged environment, the commit it is built from.

WHY THIS EXISTS. Core stamps every result with the commit of the code that made it, and
asks git for it. Inside the app there is no repository to ask: Core is a copy in
`site-packages`, so a result made by the app recorded a version number and no commit. The
version number does not tell builds apart: on 2026-11-18 four different builds were made,
all "0.1.0", and when a tester's result came back nothing in it said which of them had run.

So staging writes what git says at that moment into the installed package,

    <env>/lib/python3.x/site-packages/mrnaspect/_build.json
    {"commit": "<40 hex>", "dirty": false, "staged_at": "2026-11-19T01:20:00+00:00"}

and `mrnaspect.provenance` reads it where its own code is in no repository. `dirty` is
git's answer to "do tracked files differ from that commit": when true, the commit does not
describe the code in the app, and the report says so beside it.

    python3 scripts/bundle_build_record.py <env> --source <checkout>           write
    python3 scripts/bundle_build_record.py --check <env> [--source <checkout>]  verify only
    python3 scripts/bundle_build_record.py --check <env> --expect <commit>      a release

Writing replaces whatever record is there. That matters: the staged environment is reused
between stagings and the record is not one of the files pip installs, so pip would leave
an old one in place. A source that is not a git checkout (a downloaded archive) has no
commit to give: the record is removed, and this is said.

`--check` writes nothing; it runs on the built, sealed app. With `--source` naming a git
checkout it also expects a record to be there (staging from a checkout always writes
one), and fails when the checkout is at another commit than the app was built from: the
app is then not this source's. That used to be a note and a pass, and a release was
checked against the wrong commit that way.

`--expect` is for a release: the commit the release is of, as 40 hexadecimal characters,
or a name such as its tag, which is resolved in `--source`. The check fails unless the
record names exactly that commit and was written from an unmodified tree. With the
commit in full it needs no checkout, so it runs wherever the release is unpacked.

git is started only for a source that has a `.git`: on a Mac without Apple's command line
developer tools, `/usr/bin/git` is a stand-in that opens a system dialog when run.

Exit 0: written, or verified. Exit 1: git failed, or the record is missing where one is
expected, or it is not a record, or it names another commit than the checkout or the
release. Exit 2: not given an environment.

Standard library only: it runs on the interpreter of the environment it writes into.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NAME = "_build.json"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def package_folder(env: Path) -> Path | None:
    """Core as installed in `env`, or `None` when it is not there exactly once."""
    found = sorted(p.parent for p in
                   env.glob("lib/python3*/site-packages/mrnaspect/__init__.py"))
    return found[0] if len(found) == 1 else None


def is_checkout(source: Path) -> bool:
    """A `.git` in the folder: a directory in a clone, a file in a worktree."""
    return (source / ".git").exists()


def git(source: Path, *args: str) -> str:
    """What git prints for `args` in `source`.

    Raises:
        RuntimeError: When git cannot be run or exits non-zero, with what it said.
    """
    try:
        done = subprocess.run(["git", "-C", str(source), *args], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"git {' '.join(args)}: {type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} exited {done.returncode}: "
                           f"{done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def problem_with(record: object) -> str | None:
    """Why `record` is not a build record, in words, or `None` when it is one.

    `mrnaspect.provenance` accepts the same shape; a test holds the two to each other.
    """
    if not isinstance(record, dict):
        return "not a JSON object"
    commit = record.get("commit")
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        return f"commit is not 40 hexadecimal characters: {commit!r}"
    if not isinstance(record.get("dirty"), bool):
        return f"dirty is not true or false: {record.get('dirty')!r}"
    staged_at = record.get("staged_at")
    if not isinstance(staged_at, str):
        return f"staged_at is not a time: {staged_at!r}"
    try:
        datetime.fromisoformat(staged_at)
    except ValueError:
        return f"staged_at is not a time: {staged_at!r}"
    return None


def describe(record: dict) -> str:
    tree = ("the working tree was MODIFIED, so the commit does not describe this code"
            if record["dirty"] else "working tree clean")
    return f"built from {record['commit']} ({tree}), staged {record['staged_at']}"


def write(env: Path, source: Path, now: datetime | None = None) -> int:
    """Write the record of `source`'s commit into `env`. Returns the exit code."""
    package = package_folder(env)
    if package is None:
        print(f"Core is not installed in {env} (no lib/python3*/site-packages/mrnaspect)",
              file=sys.stderr)
        return 2
    target = package / NAME
    if not is_checkout(source):
        # An old record would name a commit this build did not come from.
        target.unlink(missing_ok=True)
        print(f"  note  {source} is not a git checkout: the app's results will name no "
              f"commit")
        return 0
    try:
        commit = git(source, "rev-parse", "HEAD")
        dirty = git(source, "status", "--porcelain", "--untracked-files=no") != ""
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    record = {
        "commit": commit,
        "dirty": dirty,
        "staged_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    why = problem_with(record)
    if why is not None:     # git answered with something that is not a commit
        print(f"FAILED: {why}", file=sys.stderr)
        return 1
    # Written beside the target and moved over it: a reader never sees half a record.
    tmp = target.with_name(NAME + ".tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    print(f"  ok    {describe(record)}")
    return 0


def resolve(expect: str, source: Path | None) -> str:
    """The commit `expect` names: itself when it is one, else what git resolves it to.

    Raises:
        RuntimeError: When a name is given with no checkout to resolve it in, or git
            does not know it.
    """
    if _COMMIT.match(expect):
        return expect
    if source is None or not is_checkout(source):
        raise RuntimeError(f"{expect!r} is not a commit in full (40 hexadecimal "
                           f"characters), and no --source checkout was given to resolve "
                           f"it in")
    # ^{commit}: an annotated tag is an object of its own, pointing at the commit.
    return git(source, "rev-parse", "--verify", "--quiet", f"{expect}^{{commit}}")


def check(env: Path, source: Path | None, expect: str | None = None) -> int:
    """Verify the record in `env`; writes nothing. Returns the exit code."""
    package = package_folder(env)
    if package is None:
        print(f"Core is not installed in {env} (no lib/python3*/site-packages/mrnaspect)",
              file=sys.stderr)
        return 2
    target = package / NAME
    from_checkout = source is not None and is_checkout(source)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        if from_checkout or expect is not None:
            print(f"no {NAME} in the app's mrnaspect package: staging from a git checkout "
                  f"always writes one, so this app's results name no commit",
                  file=sys.stderr)
            return 1
        print("  note  no build record: built from a source that is not a git checkout; "
              "the app's results name no commit")
        return 0
    try:
        record = json.loads(text)
    except ValueError as exc:
        print(f"{NAME} is not JSON: {exc}", file=sys.stderr)
        return 1
    why = problem_with(record)
    if why is not None:
        print(f"{NAME}: {why}", file=sys.stderr)
        return 1
    if expect is not None:
        # A release: the record against the commit the release is of.
        try:
            wanted = resolve(expect, source)
        except RuntimeError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        if record["commit"] != wanted:
            print(f"the app was built from {record['commit']}, and the release is "
                  f"{wanted}" + ("" if wanted == expect else f" ({expect})")
                  + ": its results would name a commit that is not the release's",
                  file=sys.stderr)
            return 1
        if record["dirty"]:
            print(f"the app was built from {wanted} with the working tree modified: the "
                  f"commit does not describe the code in the app", file=sys.stderr)
            return 1
    elif from_checkout:
        try:
            head = git(source, "rev-parse", "HEAD")
        except RuntimeError:
            head = None     # the record is what is being checked, not this checkout
        if head is not None and head != record["commit"]:
            print(f"this checkout is at {head}, and the app was built from another "
                  f"commit, {record['commit']}. Build the app again from this checkout, "
                  f"or name the commit it should be of with --expect.", file=sys.stderr)
            return 1
    print(f"  ok    {describe(record)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record in a staged environment the commit it is built from.")
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    parser.add_argument("--source", type=Path, default=None,
                        help="the checkout the environment is built from")
    parser.add_argument("--expect", default=None, metavar="COMMIT",
                        help="with --check: the commit a release is of (in full, or a "
                             "name such as its tag, resolved in --source)")
    parser.add_argument("env", type=Path, help="a staged environment, or the one in an app")
    args = parser.parse_args(argv)
    if not (args.env / "lib").is_dir():
        print(f"not an environment (no lib/): {args.env}", file=sys.stderr)
        return 2
    try:
        if args.check:
            return check(args.env.resolve(), args.source, args.expect)
        if args.expect is not None:
            print("--expect belongs with --check: a record is written from --source",
                  file=sys.stderr)
            return 2
        if args.source is None:
            print("--source is needed to write a record", file=sys.stderr)
            return 2
        return write(args.env.resolve(), args.source.resolve())
    except OSError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
