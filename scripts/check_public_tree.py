#!/usr/bin/env python3
"""Hold a tree to the rules of what is published: nothing only the developing lab knows.

    python scripts/check_public_tree.py <tree> [--lab-private FILE] [--unpublished NAME ...]

Run on an export by scripts/make_public_tree.sh, and on the checkout by
mrnaspect/tests/test_public_tree.py, which loads this file, so the rules are written once.

1. ``who``: no comment or document says who asked for a thing: no role beside a date, no
   role in brackets, no lab head's title. The date stays: it says when it was decided.
2. ``unpublished``: no file names a file that is not published, so no pointer dangles.
3. ``research``: no file points into the lab's private analysis repository: its log, its
   numbered findings and analyses, its specifications.
4. ``lab``: no file names a machine, a mount, an account or a program of the lab. Those
   names are in no tracked file; they are passed in (``--lab-private``, a JSON file with a
   ``words`` list) and the rule is skipped without them.
5. ``bam``: a fixture BAM's header records its commands with file names, not the
   directories of the machine that made it.
6. ``mail``: no file carries an e-mail address. An address names a person; the project is
   reached through its repository.

This file and the test that loads it spell the patterns of rules 1 and 3, so both are
left out of those two scans. Standard library only, but for rule 5, which needs pysam and
says so when it is missing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

#: Left out of a walk when the tree is not a git checkout (an export has none of them).
BUILT = {"node_modules", "target", "dist", "build", ".pixi", "__pycache__", ".git",
         ".pytest_cache", ".pytest_shards", ".claude"}
#: Other people's text, copied whole (licence texts, a minified library, lock files):
#: their words are not ours to hold to this.
VERBATIM_DIRS = ("scripts/notices/",)
VERBATIM_NAMES = (".min.js", ".lock", "package-lock.json")
#: The two files that spell the patterns below.
SPELL_THE_PATTERNS = ("scripts/check_public_tree.py", "mrnaspect/tests/test_public_tree.py")

_ROLE = "(?:[Uu]ser|PI)"
#: "(role, ...", "(role)", "(role: ...", "role, 2026-11-13", "role 11-13", "role decision".
MARK = re.compile(rf"\((?:the )?{_ROLE}(?:'s)?[,;:)]"
                  rf"|\b{_ROLE},? (?:on )?(?:20\d\d-)?\d\d-\d\d\b"
                  rf"|\b[Uu]ser (?:decision|plan)\b")
TITLE = re.compile(r"\bPI\b")
RESEARCH = re.compile(
    r"\bresearch repo\b|\bresearch's `|\b(?:journal|log|findings|todo|claims)\.md\b"
    r"|\bdocs/specs/|\banalysis \d{1,2}\b"
    # the analysis repository's own directories (".github/workflows/" is not one)
    r"|\bphase[12]/|(?<![\w./-])(?:analyses|finetuning|workflows)/"
    r"|(?<![\w#.\"'-])F\d{1,2}[a-z]?(?:\b(?![\w\"'=(.\[-])|'s\b)")
#: An absolute path inside a recorded command line.
ABSOLUTE = re.compile(r"(?<![\w.])/(?:[\w.@+-]+/)+[\w.@+-]+")
#: A name, an at sign, a dotted host. Not "icon@2x.png", "package@1.2.3", or the
#: "git@host:" of a clone address.
MAIL = re.compile(r"(?<![\w.+-])(?!git@)[\w.+-]+@(?:[\w-]+\.)+"
                  r"(?!(?:png|jpe?g|svg|ico|icns)\b)[A-Za-z]{2,}\b")


def published_files(root: Path, not_published: Iterable[Path] = ()) -> list[Path]:
    """Every file of ``root`` that would be published.

    Where ``root`` is a git checkout, its tracked files and its new files that are not
    ignored; every file where it is not (an export). Less ``not_published`` and other
    people's verbatim text.
    """
    root = root.resolve()
    hidden = {Path(p).resolve() for p in not_published}
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             check=True, capture_output=True, text=True).stdout.strip()
        if Path(top).resolve() != root:
            raise OSError("not the top of a checkout")
        # Tracked, and new but not ignored: what the next commit would hold.
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached",
                              "--others", "--exclude-standard"], check=True,
                             capture_output=True, text=True).stdout
        files = [root / name for name in out.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in root.rglob("*")
                 if p.is_file() and not BUILT & set(p.relative_to(root).parts)]
    return [p for p in files
            if p.exists() and p.resolve() not in hidden
            and not p.relative_to(root).as_posix().startswith(VERBATIM_DIRS)
            and not p.name.endswith(VERBATIM_NAMES)]


def _lines(path: Path) -> Iterator[tuple[int, str]]:
    """``(number, line)`` of a text file; nothing for a binary one."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    yield from enumerate(text.splitlines(), 1)


def _hits(root: Path, files: Sequence[Path], pattern: re.Pattern[str], *,
          skip: Sequence[str] = (), guards: bool = False) -> list[str]:
    found = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in skip:
            continue
        for n, line in _lines(path):
            # A line that spells the two roles as a pattern is a guard, not a credit.
            if guards and ("user|PI" in line or "[Uu]ser|PI" in line):
                continue
            if pattern.search(line):
                found.append(f"{rel}:{n}: {line.strip()[:120]}")
    return found


def unpublished_pattern(names: Sequence[str]) -> Optional[re.Pattern[str]]:
    """A file's name, or its name written as words (``working_record.md`` is also
    "working record"). None when there is nothing unpublished to name."""
    names = sorted({Path(n).name for n in names if not Path(n).name.startswith(".")})
    if not names:
        return None
    spoken = [Path(n).stem.replace("_", " ") for n in names if len(Path(n).stem) >= 10]
    return re.compile("|".join(map(re.escape, names + spoken)), re.I)


def bam_headers(files: Sequence[Path]) -> list[str]:
    """Rule 5. samtools and minimap2 record their command lines in @PG, with whatever
    paths they were given."""
    bams = [p for p in files if p.suffix == ".bam"]
    if not bams:
        return []
    try:
        import pysam
    except ImportError:
        return [f"{len(bams)} BAM file(s) not checked: pysam is not installed"]
    found = []
    for bam in bams:
        with pysam.AlignmentFile(str(bam), check_sq=False) as handle:
            commands = [pg.get("CL", "") for pg in handle.header.to_dict().get("PG", [])]
        found += [f"{bam.name}: {c[:120]}" for c in commands if ABSOLUTE.search(c)]
    return found


def check(root: Path, *, not_published: Iterable[Path] = (),
          unpublished_names: Sequence[str] = (),
          lab_words: Sequence[str] = ()) -> dict[str, Optional[list[str]]]:
    """Every rule's findings for ``root``; None for a rule that could not be asked."""
    root = root.resolve()
    files = published_files(root, not_published)
    if len(files) < 100:
        raise ValueError(f"only {len(files)} files under {root}: not this project's tree")
    names = [*unpublished_names, *(Path(p).name for p in not_published)]
    pointer = unpublished_pattern(names)
    return {
        "who": (_hits(root, files, MARK, skip=SPELL_THE_PATTERNS, guards=True)
                + _hits(root, files, TITLE, skip=SPELL_THE_PATTERNS, guards=True)),
        "unpublished": None if pointer is None else _hits(root, files, pointer),
        "research": _hits(root, files, RESEARCH, skip=SPELL_THE_PATTERNS),
        "lab": (None if not lab_words else
                _hits(root, files, re.compile("|".join(map(re.escape, lab_words))))),
        "bam": bam_headers(files),
        "mail": _hits(root, files, MAIL),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tree", type=Path)
    parser.add_argument("--lab-private", type=Path,
                        help="JSON with a 'words' list: the lab's own names (rule 4)")
    parser.add_argument("--unpublished", nargs="*", default=[],
                        help="names of the files left out of the tree (rule 2)")
    args = parser.parse_args(argv)
    words = []
    if args.lab_private:
        words = json.loads(args.lab_private.read_text(encoding="utf-8")).get("words") or []
    results = check(args.tree, unpublished_names=args.unpublished, lab_words=words)
    bad = 0
    for rule, found in results.items():
        if found is None:
            print(f"{rule}: not asked (nothing to look for was given)")
        elif found:
            bad += len(found)
            print(f"{rule}: {len(found)} finding(s)")
            for line in found[:30]:
                print(f"  {line}")
        else:
            print(f"{rule}: clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
