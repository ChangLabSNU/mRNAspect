"""The relocation check's component results follow the commands' own exit status.

``scripts/check_relocation.sh`` marked minimap2 and samtools PASS on any non-empty first
line of ``--version``, with stderr merged in, so "No such file or directory" passed; and
it took WeasyPrint's status from a pipeline ending in ``tail``. Against an empty
environment with a failing interpreter, all three passed.

The two blocks are run here as they stand in the script, cut out between their
headings, with the script's own helpers, against stand-in environments.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_relocation.sh"


def _blocks() -> str:
    text = _SCRIPT.read_text()
    start = text.index('say "3. the external binaries in the copy execute"')
    end = text.index('say "5. the whole pipeline, from the copy"')
    return text[start:end]


def _helpers() -> str:
    """say / ok / bad / check and run, as the script defines them."""
    wanted = ("say()", "ok()", "bad()", "check()", "run()")
    lines = [l for l in _SCRIPT.read_text().splitlines() if l.startswith(wanted)]
    assert len(lines) == len(wanted), lines
    return "\n".join(lines)


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run(env: Path) -> tuple[int, int, str]:
    """(passes, failures, output) of the two blocks against ``env``."""
    script = "\n".join(["set -u", "PASS=0; FAIL=0", _helpers(), f'DEST="{env}"',
                        'PY="$DEST/bin/python"', 'mkdir -p "$DEST/fakehome"', _blocks(),
                        'echo "RESULT $PASS $FAIL"'])
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    result = next(l for l in done.stdout.splitlines() if l.startswith("RESULT "))
    _, passed, failed = result.split()
    return int(passed), int(failed), done.stdout


def test_a_working_environment_passes_all_three(tmp_path: Path) -> None:
    env = tmp_path / "env"
    _exe(env / "bin" / "minimap2", 'echo "2.30-r1287"\n')
    _exe(env / "bin" / "samtools", 'echo "samtools 1.23"; echo "Using htslib 1.23"\n')
    _exe(env / "bin" / "python", 'echo "  pango/harfbuzz loaded: True"\n')
    passed, failed, out = _run(env)
    assert (passed, failed) == (3, 0), out
    assert "minimap2: 2.30-r1287" in out and "samtools: samtools 1.23" in out


def test_missing_binaries_and_a_failed_import_all_fail(tmp_path: Path) -> None:
    """The issue's case: an empty environment and an interpreter that exits 1."""
    env = tmp_path / "env"
    _exe(env / "bin" / "python", 'echo "ModuleNotFoundError: No module named weasyprint" >&2\nexit 1\n')
    passed, failed, out = _run(env)
    assert (passed, failed) == (0, 3), out
    # The diagnostic is kept, not replaced by a bare FAIL.
    assert out.count("No such file or directory") == 2
    assert "ModuleNotFoundError" in out


def test_a_version_command_that_exits_non_zero_fails(tmp_path: Path) -> None:
    env = tmp_path / "env"
    _exe(env / "bin" / "minimap2", 'echo "dyld: Library not loaded: libz.1.dylib"\nexit 134\n')
    _exe(env / "bin" / "samtools", 'echo "samtools 1.23"\n')
    _exe(env / "bin" / "python", 'echo "  pango/harfbuzz loaded: True"\n')
    passed, failed, out = _run(env)
    assert (passed, failed) == (2, 1), out
    assert "minimap2 did not run (exit 134): dyld: Library not loaded" in out
