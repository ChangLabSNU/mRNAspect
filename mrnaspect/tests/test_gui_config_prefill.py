"""The Config field, and the two silent ways it could be wired to nothing.

The shell now takes an operator's ``run_config.yaml``, passes it to Core, and shows what
Core resolves as the grey hint under each field. Two halves of that can fail without any
build, test or runtime error:

1. **The real-time route can swallow it.** ``mrnaspect-realtime`` refuses to start unless
   the resolved config says ``mode: pod5``, so the shell writes a one-key config saying
   so. Both are passed as ``--config``, which merges in order, so the ORDER decides which
   one survives a collision. Reversed, the operator's ``mode: bam`` would bury the
   shell's forced key and the live driver would refuse -- or, if the shell's file ever
   grew a second key, it would silently override the operator's.
2. **A renamed config key stops pre-filling and says nothing.** The field keeps its
   authored placeholder, which still reads correctly, so nothing looks wrong.

Checked in Python because no compiler on this host sees the Rust or the TypeScript
(no cargo, node or tsc, measured 2026-10-22) -- the same reason test_gui_core_contract.py
lives here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.main import _parse_args, load_config

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists() and _RS.exists()),
    reason="GUI sources are not in this checkout")


def _fn_body(name: str) -> str:
    rs = _RS.read_text()
    start = rs.index(f"fn {name}(")
    return rs[start: rs.index("\n}\n", start)]


# --- 1. the layering order --------------------------------------------------

def test_the_operator_s_config_is_passed_before_the_shell_s() -> None:
    """Last --config wins on a collision, so the shell's forced key must come last."""
    body = _fn_body("start_qc")
    user = body.index("args.push(config.trim())")
    forced = body.index("args.push(&cfg_str)")
    assert user < forced, (
        "the GUI's `mode: pod5` layer is passed BEFORE the operator's config, so an "
        "operator config naming `mode` would bury it and the live driver would refuse")


def test_all_three_config_layers_reach_the_command_line_in_order() -> None:
    """A layer read into a variable that is never pushed is a knob wired to nothing, and
    one pushed in the wrong place is overridden by something less specific.

    Least specific first: this computer's facts, then the operator's file, then the
    shell's forced `mode: pod5`.
    """
    body = _fn_body("start_qc")
    assert body.count('args.push("--config")') == 3, (
        "start_qc must pass three --config layers: This Mac, the operator's, the shell's")
    machine = body.index("args.push(m)")
    user = body.index("args.push(config.trim())")
    forced = body.index("args.push(&cfg_str)")
    assert machine < user < forced, "the --config layers are out of order"


def test_the_shell_s_own_config_layer_forces_mode_AND_NOTHING_ELSE() -> None:
    """Every key in it overrides the operator silently; there may be exactly one.

    `mode` is the one the shell is entitled to set: a live run must be told whether it
    follows POD5 files or MinKNOW's BAM files, and the card says which. A second key
    added here would quietly outrank whatever the operator wrote, with nothing on screen
    to say so. There is one layer per kind of file, and the one written is the card's.
    """
    rs = _RS.read_text()
    layers = dict(re.findall(r'const LIVE_LAYER_([A-Z0-9]+): &str = "(.*?)";', rs, re.S))
    assert sorted(layers) == ["BAM", "POD5"], sorted(layers)
    for name, text in layers.items():
        keys = [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        assert keys == [f"mode: {name.lower()}"], (
            f"the shell's config layer for {name} sets {keys}, not just its mode")
    body = _fn_body("start_qc")
    assert 'let layer = if mode == "bam" { LIVE_LAYER_BAM } else { LIVE_LAYER_POD5 };' in body
    assert "std::fs::write(&cfg_path, layer)" in body


# --- 2. the pre-filled hints ------------------------------------------------

def _prefill() -> dict[str, str]:
    ts = _TS.read_text()
    table = ts[ts.index("const PREFILL"): ts.index("const AUTHORED")]
    return {m.group(1): m.group(2)
            for m in re.finditer(r'\["([a-z]+)",\s*"([a-z0-9_.]+)"\]', table)}


def test_the_table_is_the_per_run_fields() -> None:
    """Guards every test below: an unparsed table would make them all vacuously pass.

    Exactly the per-run fields. Server and device left it with I6: they are This Mac's
    saved values now, not hints about what a blank field will do.
    """
    assert set(_prefill()) == {"input", "ref", "output", "runname"}, _prefill()


@pytest.mark.parametrize("field,path", sorted(_prefill().items()) or [("", "")])
def test_every_prefilled_hint_names_a_key_the_config_really_has(field, path) -> None:
    """A renamed key does not fail anything -- the hint just silently stops appearing."""
    node = load_config(None, {}, require_reference=False)
    for key in path.split("."):
        assert isinstance(node, dict) and key in node, (
            f"the GUI pre-fills {field!r} from {path!r}, which the shipped config does "
            f"not have. Rename it in main.ts or the hint is dead and says nothing.")
        node = node[key]


@pytest.mark.parametrize("field", sorted(_prefill()))
def test_every_prefilled_field_exists_in_the_page(field) -> None:
    assert f'id="{field}"' in _HTML.read_text(), (
        f"main.ts pre-fills #{field}, which index.html does not define")


def test_the_resolved_config_is_shown_as_a_placeholder_never_a_value() -> None:
    """A6, and more than cosmetic.

    Writing Core's resolved values into the fields makes the GUI a second source for
    them: the form sends back what Core just told it, so a blank "use the default"
    becomes an explicit setting and a later change to the shipped default is overridden
    by a stale copy.
    """
    ts = _TS.read_text()
    start = ts.index("async function refreshConfig")
    body = ts[start: ts.index("\n}\n", start)]
    assert ".placeholder =" in body
    assert ".value =" not in body, (
        "refreshConfig writes Core's resolved config into a field's value; it must only "
        "ever set the placeholder. (loadMachine writing This Mac's fields is different: "
        "those are the operator's own saved values, a first source, not Core's answer.)")


# --- 3. the flag the shell calls is a flag Core has -------------------------

def test_the_shell_asks_core_with_a_flag_core_actually_parses() -> None:
    """A Rust string literal and an argparse flag cannot be checked against each other
    by either toolchain."""
    assert "print_config(" in _fn_body("read_config")
    body = _fn_body("print_config")
    flags = re.findall(r'"(--[a-z-]+|-m|mrnaspect\.main)"', body)
    assert "-m" in flags and "mrnaspect.main" in flags and "--print-config" in flags
    assert _parse_args(["--print-config"]).print_config is True


def test_every_command_the_front_end_invokes_exists_in_rust() -> None:
    """Registered AND defined. A command missing from generate_handler! compiles fine and
    rejects the call at runtime."""
    rs = _RS.read_text()
    handler = rs[rs.index("generate_handler!"): rs.index("]", rs.index("generate_handler!"))]
    for cmd in sorted(set(re.findall(r'invoke<[^>]*>\("([a-z_]+)"|invoke\("([a-z_]+)"',
                                     _TS.read_text()))):
        name = cmd[0] or cmd[1]
        assert f"fn {name}(" in rs, f"main.ts invokes {name!r}, which lib.rs does not define"
        assert name in handler, (
            f"{name!r} is defined but missing from generate_handler!; the call compiles "
            f"and fails only when a person clicks")


def test_read_config_s_arguments_match_the_rust_signature() -> None:
    sent = re.search(r'invoke<[^>]*>\("read_config",\s*(\{[^}]*\})', _TS.read_text())
    assert sent, "could not find the read_config call in main.ts"
    ts = {re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()
          for k in re.findall(r'[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', sent.group(1))}
    sig = _RS.read_text()
    sig = sig[sig.index("fn read_config("): sig.index("->", sig.index("fn read_config("))]
    rust = set(re.findall(r"([a-z_][a-z0-9_]*)\s*:\s*(?:String|bool)", sig))
    assert ts == rust, f"read_config mismatch: TS sends {sorted(ts)}, Rust takes {sorted(rust)}"


# --- 4. the shell speaks each driver's own command line ---------------------

def _branch_flags() -> dict[str, set[str]]:
    """The flags start_qc passes, split by which driver gets them.

    The two argument lists are built apart in lib.rs because the drivers differ in more
    than a flag; that is also why a flag can be added to one and be wrong for the other.
    The conditional pushes at the end apply to whichever branch was chosen, so they go
    into both sets.
    """
    body = _fn_body("start_qc")
    head = body.index("let mut args: Vec<&str> = if realtime {")
    split = body.index("} else {", head)
    end = body.index("};", split)
    flag = lambda s: set(re.findall(r'"(--[a-z0-9-]+)"', s))
    shared = flag(body[end:])
    return {"realtime": flag(body[head:split]) | shared,
            "offline": flag(body[split:end]) | shared}


@pytest.mark.parametrize("driver", ["realtime", "offline"])
def test_every_flag_the_shell_passes_is_one_that_driver_parses(driver) -> None:
    """argparse exits 2 on an unknown flag, and the GUI shows that as "could not start"
    with the real cause in a log the operator has not opened.

    `mrnaspect-realtime` deliberately has no `--mode`, so the two command lines are not
    interchangeable and a flag copied from one branch to the other fails only at run
    time, on the instrument.

    Read off `--help` rather than from the parser object: both drivers build theirs
    inside `_parse_args`, and --help is also the text a person would check against.
    """
    module = "mrnaspect.realtime.main" if driver == "realtime" else "mrnaspect.main"
    helptext = subprocess.run([sys.executable, "-m", module, "--help"],
                              capture_output=True, text=True, check=True).stdout
    known = {w.rstrip(",").split("=")[0] for w in helptext.split() if w.startswith("--")}
    missing = sorted(_branch_flags()[driver] - known)
    assert not missing, (
        f"the shell passes {missing} to the {driver} driver, which does not parse them")


def test_an_overtaken_lookup_does_not_write_a_stale_hint() -> None:
    """Two edits in flight: the slower answer must not land last.

    `refreshConfig` runs on every edit of Config and Core, and python takes a moment.
    Leaving one field and then the other starts two lookups, and without a token the
    older one's answer overwrites the newer one's -- which on screen looks exactly like
    Core resolving the config wrongly.
    """
    ts = _TS.read_text()
    body = ts[ts.index("async function refreshConfig"): ts.index('$("config").addEventListener')]
    assert body.count("if (gen !== configGen) return;") == 2, (
        "refreshConfig must drop its result on BOTH paths -- after the await and in the "
        "catch -- when a newer lookup has been issued")
    assert body.index("const gen = ++configGen") < body.index("await invoke")


def test_a_command_that_waits_on_a_child_process_is_off_the_main_thread() -> None:
    """Tauri v2: "Commands without the async keyword are executed on the main thread
    unless defined with #[tauri::command(async)]".

    `read_config` waits ~0.3-0.5 s for python to start, so on the main thread it freezes
    the window on launch and on every edit of Config or Core. The symptom is a hitch
    nobody attributes to the right cause, so it is pinned here rather than noticed.
    Scoped to the class, not to this one function: `.spawn()` without a wait is fine, and
    that is what every other command in the file does.
    """
    rs = _RS.read_text()
    defined = set(re.findall(r"\nfn ([a-z_]+)\(", rs))

    def waits(name: str, seen: set[str]) -> bool:
        """The body waits on a child, or calls a function here that does. Followed through
        helpers since I8: start_qc began asking Core through print_config, and a check of
        the command's own text would have passed while the window froze."""
        if name in seen:
            return False
        seen.add(name)
        body = _fn_body(name)
        if re.search(r"\.(output|status|wait)\(\)", body):
            return True
        called = set(re.findall(r"\b([a-z_]+)\(", body)) & defined
        return any(waits(c, seen) for c in called - {name})

    offenders = []
    for m in re.finditer(r"#\[tauri::command(\(async\))?\]\s*\nfn ([a-z_]+)\(", rs):
        is_async, name = m.group(1), m.group(2)
        if waits(name, set()) and not is_async:
            offenders.append(name)
    assert not offenders, (
        f"{offenders} block on a child process but are plain #[tauri::command], so they "
        f"run on the main thread and freeze the window for as long as the child takes")
