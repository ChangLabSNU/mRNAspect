"""The three languages of one invocation have to agree, and no compiler checks that.

A Tauri command is called from TypeScript with a camelCase object and received in Rust as
snake_case parameters. Nothing type-checks across that boundary: rename a field on one
side and the build still succeeds, the app still starts, and the argument silently
arrives as nothing. The same hole sits between `index.html`'s element ids and the
`val("…")` calls that read them — a typo returns undefined, not an error.

It is checked HERE, in Python, for a practical reason: the machines that run this suite
have no cargo, rustc, node or tsc (measured 2026-10-22), so the GUI's own toolchain
cannot run until the Mac builds it. A static cross-language check is the only guard that
runs at the time the mistake is made.

This does not replace building the app. It catches the mismatches a build would not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists() and _RS.exists()),
    reason="GUI sources are not in this checkout")


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _invoke_keys(command: str) -> set[str]:
    """The object keys TypeScript sends to one Tauri command."""
    ts = _TS.read_text()
    start = ts.index(f'"{command}"')
    body = ts[ts.index("{", start): ts.index("});", start)]
    # Comments out first: they contain prose with colons. Then any key preceded by the
    # opening brace or a comma — NOT just line-initial ones, because this object puts two
    # keys on a line and a line-initial regex silently missed inputDir and outputDir,
    # which is a test reporting a defect that is its own.
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body))


def _rust_params(command: str) -> set[str]:
    rs = _RS.read_text()
    start = rs.index(f"fn {command}(")
    sig = rs[start: rs.index("->", start)]
    return set(re.findall(r"([a-z_][a-z0-9_]*)\s*:\s*(?:String|bool|i64|u64|f64)", sig))


def test_every_argument_typescript_sends_is_one_rust_receives():
    ts = {_camel_to_snake(k) for k in _invoke_keys("start_qc")}
    rs = _rust_params("start_qc")
    assert ts == rs, (
        f"start_qc argument mismatch.\n"
        f"  TypeScript sends, Rust does not take: {sorted(ts - rs)}\n"
        f"  Rust takes, TypeScript does not send: {sorted(rs - ts)}")


def test_the_run_name_reaches_rust():
    """Named on its own because it is the newest, and the one a rename would break."""
    assert "runName" in _invoke_keys("start_qc")
    assert "run_name" in _rust_params("start_qc")


def test_every_field_the_script_reads_exists_in_the_page():
    ts_ids = set(re.findall(r'val\("([^"]+)"\)', _TS.read_text()))
    html_ids = set(re.findall(r'id="([^"]+)"', _HTML.read_text()))
    missing = sorted(ts_ids - html_ids)
    assert not missing, f"main.ts reads ids that index.html does not define: {missing}"


def test_the_shell_does_not_re_implement_core_s_run_name_rule():
    """One rule, in Core, where both the CLI and the GUI meet it.

    `resolve_run_dir` refuses a name containing a separator, `..`, `.` or an absolute
    path, because `output_dir / name` would otherwise write outside the directory the
    operator chose. A second copy of that rule in Rust is how the two come to disagree —
    this project's most repeated defect shape.
    """
    rs = _RS.read_text()
    start = rs.index("fn start_qc(")
    body = rs[start: rs.index("\n}\n", start)]
    for banned in ('run_name.contains("/")', "run_name.contains('/')",
                   'run_name.contains("..")'):
        assert banned not in body, (
            f"the shell validates the run name itself ({banned}); Core already does, and "
            f"two copies of one rule drift apart")


def test_the_shell_leaves_min_reads_to_the_config():
    """`min_reads` raises the report's "not enough reads to diagnose" banner, and the
    config sets it (shipped 1000). The shell forced 1 while the GUI was being tested,
    which kept that banner off every GUI run that mapped a single read and made the CLI
    and the GUI mean different things by one setting. Removed 2026-11-02; nothing
    in the shell may set it again.
    """
    rs = _RS.read_text()
    start = rs.index("fn start_qc(")
    body = rs[start: rs.index("\n}\n", start)]
    assert "--min-reads" not in body, (
        "the shell passes --min-reads, which overrides the operator's config and the "
        "shipped default; leave min_reads to the config")
