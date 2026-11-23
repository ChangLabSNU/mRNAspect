"""The shell wrappers must run under bash 3.2, because macOS still ships it.

Apple froze `/bin/bash` at 3.2.57 (2007, the last GPLv2 release) and has never moved.
Core's GUI is developed on macOS — the Linux machines here are headless with no system
webview — so "Linux is the shipping target" does not excuse a wrapper that only runs on
bash 4. Measured 2026-09-21: `mapfile -t UBAMS < <(find ...)` in `run_align.sh` died with
`mapfile: command not found` and took two tests with it.

This is a static check on purpose. Running the wrappers under a real bash 3.2 would be
the stronger test, but there is no bash 3.2 on the machines that run this suite, and a
guard that only works where the bug cannot happen is no guard at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER_DIR = REPO / "mrnaspect" / "wrappers"
WRAPPERS = sorted(WRAPPER_DIR.glob("*.sh"))

#: EVERY shell script in the repository, not only the wrappers.
#:
#: Scoping this to `wrappers/` is how the same bug shipped twice. These checks were written
#: 2026-09-21 after `mapfile` in `run_align.sh` broke on macOS — and on 2026-10-06 a new
#: `scripts/run_tests_sharded.sh` was written with `mapfile` in it, passed every check
#: here, and died on the macOS CI runner at its first line of work. The guard had been
#: scoped to the directory where the bug was found rather than to the class of file it
#: applies to.
#: Derived trees are excluded: `build/lib/` holds a COPY of the wrappers made by the last
#: `pip install`, and it is stale by construction — scanning it reports a defect in a file
#: nobody edits and that no release ships. (It is also where this check first fired after
#: the fix, on the pre-fix copy.) `dist/bundle-env` is the same thing for the shipped
#: environment, and `.claude/worktrees/` is the same thing again: the desktop app creates
#: checkouts of this repo there, they are gitignored, and they lag.
#:
#: That last one was found 2026-10-22 by running the suite from a FRESH CLONE and getting
#: 1128 cases against 1140 here. The extra 12 were four scripts from a worktree snapshot
#: being scanned a second time, so the count of this guard depended on whether the person
#: running it had ever opened a session worktree. Nothing failed — but a guard whose
#: subject set varies by machine cannot be reasoned about, and the comment above records
#: that a stale copy HAS fired this check before.
_SKIP_DIRS = {".pixi", ".git", ".claude", "node_modules", "build", "dist", "target",
              ".pytest_cache"}
ALL_SHELL = sorted(
    p for p in REPO.rglob("*.sh")
    if not (_SKIP_DIRS & set(p.parts)) and not any(x.endswith(".egg-info") for x in p.parts)
)

# Each entry: (regex, what it is, what to write instead). Only constructs that are
# genuinely absent or broken in 3.2 — not merely newer style.
BASH4_ONLY = [
    (r"\bmapfile\b", "mapfile (bash 4.0)",
     "UBAMS=(); while IFS= read -r x; do UBAMS+=(\"$x\"); done < <(...)"),
    (r"\breadarray\b", "readarray (bash 4.0, mapfile's alias)",
     "the same read loop"),
    (r"declare\s+-A", "associative arrays (bash 4.0)",
     "parallel indexed arrays, or a case statement"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*,,\}", "${var,,} lowercasing (bash 4.0)",
     "$(printf '%s' \"$var\" | tr '[:upper:]' '[:lower:]')"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^\}", "${var^^} uppercasing (bash 4.0)",
     "tr, as above"),
    (r"&>>", "&>> append-both redirection (bash 4.0)", ">>file 2>&1"),
    (r"\bcoproc\b", "coproc (bash 4.0)", "a background job with explicit fifos"),
]

# A second class, and the one that bit next. These are not bash features at all — they are
# GNU coreutils flags that the BSD tools on macOS either lack or spell differently. The
# failure mode is worse than a missing builtin, because the command SUCCEEDS and returns
# something unusable: `date +%s%N` on BSD emits the literal "N" and the arithmetic on the
# next line dies far from the cause. Measured on the macOS CI runner 2026-10-07.
GNU_ONLY = [
    (r"\breadlink\s+-f\b", "readlink -f (GNU only)",
     "cd \"$(dirname \"$x\")\" && pwd -P"),
    (r"\bstat\s+-c\b", "stat -c (GNU; BSD uses -f)", "wc -c, or a portable branch"),
    (r"\bsed\s+-i\s+-", "sed -i without a backup suffix (BSD requires one)",
     "sed -i.bak, then rm the .bak; or a temp file and mv"),
    (r"\bgrep\s+-P\b", "grep -P (GNU only)", "grep -E, or awk"),
    (r"\bdate\s+-d\b", "date -d (GNU; BSD uses -v/-j -f)", "compute in Python"),
    # Both found by the first run of the suite on a Mac, 2026-11-23. Neither fails where it
    # is written: zcat prints an error and the pipe after it counts 0 lines, and sed
    # matches nothing and prints nothing.
    (r"\bzcat\b", "zcat (on macOS it reads compress(1) files and looks for <name>.Z)",
     "gzip -dc"),
    (r"\bsed\b(?![^\n]*\s-E\b)[^\n|]*\\[+?]",
     r"\+ or \? in a basic regular expression of sed (GNU extension; BSD sed reads a "
     r"literal character)", "sed -E with + and ?"),
]


def test_there_are_wrappers_to_check() -> None:
    """A rename that emptied this list would make every check below vacuously pass."""
    assert WRAPPERS, f"no *.sh found under {WRAPPER_DIR}"
    assert {p.name for p in WRAPPERS} >= {"run_align.sh", "run_basecall.sh",
                                          "start_server.sh"}, [p.name for p in WRAPPERS]


def _code_only(script: Path) -> str:
    """Script text with comments stripped — the explanations of these very bugs live in
    comments, in this file and in the wrappers, and a check that reads them fails on the
    description of its own fix."""
    return "\n".join(re.sub(r"(?<!\$)#.*$", "", line)
                     for line in script.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_no_bash4_only_constructs_anywhere(script: Path) -> None:
    """The bash-3.2 rules apply to every shell script here, not just the wrappers."""
    code = _code_only(script)
    for pattern, what, instead in BASH4_ONLY:
        assert re.search(pattern, code) is None, (
            f"{script.relative_to(REPO)} uses {what}, which macOS's bash 3.2 does not "
            f"have. Use {instead}.")


#: `date +%s%N` is GNU-only, but it is usable WITH a BSD branch — BSD emits a literal "N",
#: so `case "$t" in *N) ...` detects it at run time. The rule is therefore not "never %N"
#: but "never %N without that branch"; stating it as a flat ban would have forced the
#: correct helper to be written in a way that hides what it does.
_PCT_N = re.compile(r"date\s+\+[^\s|)\"']*%N")
_BSD_FALLBACK = re.compile(r"\*N\)")


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_date_nanoseconds_has_a_bsd_fallback(script: Path) -> None:
    code = _code_only(script)
    if not _PCT_N.search(code):
        return
    assert _BSD_FALLBACK.search(code), (
        f"{script.relative_to(REPO)} uses `date +%N`, which BSD date (macOS) does not "
        f"know — it emits a literal 'N' and the next arithmetic dies with "
        f"'value too great for base'. Branch on a trailing N, as now_ms() does.")


#: `du --apparent-size` is the same shape as `%N`: GNU-only, but legitimate WITH a BSD
#: branch, and the project needs it -- `du -sh` reports blocks on disk, which on this
#: compressed checkout understates the bundle by ~30%, and a .app carries bytes.
#:
#: This entry exists because the guard above enumerated the GNU flags it had SEEN
#: (readlink -f, stat -c, grep -P, date -d) rather than the class they belong to, so
#: `du --apparent-size` walked into scripts/stage_bundle_env.sh unchallenged and failed
#: silently on the first real macOS run (2026-10-23): du errored, 2>/dev/null ate it, and
#: the line printed an empty size. check_relocation.sh had the correct branch all along.
_DU_APPARENT = re.compile(r"du\s+[^\n|]*--apparent-size")
_DU_BSD_FALLBACK = re.compile(r"stat\s+-f")


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_du_apparent_size_has_a_bsd_fallback(script: Path) -> None:
    code = _code_only(script)
    if not _DU_APPARENT.search(code):
        return
    assert _DU_BSD_FALLBACK.search(code), (
        f"{script.relative_to(REPO)} uses `du --apparent-size`, which BSD du (macOS) does "
        f"not know. It fails rather than falling back, and a `2>/dev/null` around it turns "
        f"that into a blank where a number should be. Branch on it, as check_relocation.sh "
        f"and stage_bundle_env.sh do.")


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_no_gnu_only_utility_flags(script: Path) -> None:
    """GNU coreutils flags the BSD tools on macOS lack or spell differently."""
    code = _code_only(script)
    for pattern, what, instead in GNU_ONLY:
        assert re.search(pattern, code) is None, (
            f"{script.relative_to(REPO)} uses {what}. Use {instead}.")


def test_the_shell_scan_reaches_outside_the_wrappers() -> None:
    """Guards the widening: if this only ever saw `wrappers/`, the bug it was widened for
    would pass again."""
    outside = [p for p in ALL_SHELL if WRAPPER_DIR not in p.parents]
    assert outside, "no shell script found outside mrnaspect/wrappers — did the glob break?"


@pytest.mark.parametrize("script", WRAPPERS, ids=lambda p: p.name)
def test_shebang_is_bash_not_sh(script: Path) -> None:
    """The wrappers DO use bash-3 features (arrays, [[ ]]); they must not claim POSIX sh."""
    first = script.read_text(encoding="utf-8").splitlines()[0]
    assert re.match(r"^#!(/usr/bin/env bash|/bin/bash)\s*$", first), first


# ---------------------------------------------------------------------------
# No machine-specific absolute path may be a shipped DEFAULT
# ---------------------------------------------------------------------------
# `LOG_PATH=${DORADO_LOG:-<a scratch directory>}` shipped in start_server.sh until
# 2026-09-23 — a directory that exists on one machine. The pipeline never tripped over it
# because `dorado_server.py` passes the run's own output dir as `--log_path`; only the
# manual entry point used the default, so it failed for exactly the person who was not us.
# Found while preparing pod5 mode on macOS, where it is simply a path that is not there.
#
# The rule is general, not a blocklist of one site's mounts: a `${VAR:-/abs/path}` default
# is only portable if that path exists on every machine, which in practice means a temp or
# system directory. Anything else must come from the environment or the config.

_DEFAULT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")
# Where a default absolute path may legitimately point.
PORTABLE_ROOTS = ("/tmp", "/var/tmp", "/usr", "/bin", "/sbin", "/etc", "/dev/null")


@pytest.mark.parametrize("script", WRAPPERS, ids=lambda p: p.name)
def test_no_machine_specific_default_paths(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"(?<!\$)#.*$", "", line) for line in text.splitlines())
    bad = []
    for var, default in _DEFAULT_RE.findall(code):
        d = default.strip()
        # Nested expansions like ${TMPDIR:-/tmp}/x are resolved by the shell at run time;
        # judge the literal prefix, which is what a machine would have to provide.
        if not d.startswith("/"):
            continue
        if any(d == r or d.startswith(r + "/") for r in PORTABLE_ROOTS):
            continue
        bad.append(f"${{{var}:-{d}}}")
    assert not bad, (
        f"{script.name} defaults to absolute path(s) that exist on one machine: "
        f"{bad}. A shipped default must be portable — use a temp dir, or require the "
        f"value from the environment/config and fail loudly without it.")
