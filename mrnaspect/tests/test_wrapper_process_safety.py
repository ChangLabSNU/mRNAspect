"""A wrapper must not be able to signal a process it did not start.

Measured 2026-10-05, and it bit this session twice. `start_server.sh` found and stopped
the dorado server with `pgrep -f dorado_basecall_server`. `-f` matches the whole COMMAND
LINE, so it matches every process that merely *mentions* the binary — including a shell
that exported `DORADO_SERVER=/…/dorado_basecall_server`, and including the script itself.
The "already running" guard refused to start because it had found its own caller, and
`kill $(pgrep -f …)` then terminated that caller's shell (exit 144).

The guard is the cheap half; the kill is the dangerous half, because it is a wrapper that
can terminate unrelated processes belonging to the same user. The same code also had no
notion of WHICH server: `DORADO_PORT=5556 … stop` would kill a server on 5555.

Static, like the portability checks next door, and for the same reason: reproducing it
needs a real server, a real GPU and a shell holding the variable, none of which the suite
has. A guard that only works where the bug cannot happen is no guard at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WRAPPER_DIR = Path(__file__).resolve().parents[1] / "wrappers"
WRAPPERS = sorted(WRAPPER_DIR.glob("*.sh"))

#: (regex, what it is, what to do instead). Matched against code only — comments in these
#: scripts explain the banned idioms on purpose and must stay readable.
BANNED = [
    (re.compile(r"\bp(?:grep|kill)\s+(?:-\w+\s+)*-\w*f"),
     "pgrep/pkill -f matches the whole command line, so it matches any process that "
     "merely mentions the binary — including this script and its caller",
     "record the pid at start and verify it with `ps -p <pid> -o comm=`"),
    (re.compile(r"\bps\s+aux\b[^\n]*\|[^\n]*grep"),
     "`ps aux | grep <name>` is the same over-match wearing a different hat",
     "`ps -p <pid> -o …` — scoped to one known pid"),
    (re.compile(r"\bkill\b[^\n]*\$\(\s*(?:pgrep|ps|pidof)\b"),
     "killing the output of a process scan signals whatever the scan happened to match",
     "kill one recorded, re-verified pid"),
]


def _code_lines(script: Path) -> list[tuple[int, str]]:
    """Lines with comments and blanks dropped — the banned idioms are *documented* here."""
    out = []
    for n, raw in enumerate(script.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if line.strip():
            out.append((n, line))
    return out


def test_there_are_wrappers_to_check() -> None:
    assert WRAPPERS, f"no *.sh under {WRAPPER_DIR} — this suite would pass vacuously"


@pytest.mark.parametrize("script", WRAPPERS, ids=lambda p: p.name)
def test_no_process_scanning_to_find_or_kill(script: Path) -> None:
    for pattern, what, instead in BANNED:
        hits = [(n, l) for n, l in _code_lines(script) if pattern.search(l)]
        assert not hits, (
            f"{script.name}:{hits[0][0]} uses a process scan to identify a process.\n"
            f"  {hits[0][1].strip()}\n"
            f"  {what}.\n"
            f"  Instead: {instead}.")


def test_the_server_wrapper_kills_only_a_pid_it_recorded() -> None:
    """The positive half: banning the idiom is not the same as having a safe one."""
    script = WRAPPER_DIR / "start_server.sh"
    text = script.read_text()
    assert "PID_FILE=" in text, "the server wrapper must record the pid it started"
    assert re.search(r"ps -p \"?\$\{?\w+\}?\"? -o comm=", text), (
        "before signalling, the recorded pid must be re-verified by EXECUTABLE name — a "
        "stale pid file after a crash or a pid reuse otherwise names an innocent process")
    # Every kill must take a variable, never a command substitution.
    for n, line in _code_lines(script):
        if re.search(r"\bkill\b", line):
            assert re.search(r'\bkill\b(\s+-\w+)*\s+"?\$', line), (
                f"start_server.sh:{n}: kill target is not a plain variable — {line.strip()}")


def test_the_pid_file_is_scoped_to_the_port() -> None:
    """One host can legitimately run two servers; `stop` must not reach across ports.

    Before this was scoped, `DORADO_PORT=5556 start_server.sh stop` killed a server on
    5555 — there was only ever one notion of "the" server.
    """
    text = (WRAPPER_DIR / "start_server.sh").read_text()
    assert re.search(r'PID_FILE="[^"]*\$\{?PORT\}?[^"]*"', text), (
        "the pid file path must contain $PORT, or stop/status cannot tell two servers apart")
