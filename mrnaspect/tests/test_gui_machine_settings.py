"""This Mac: facts about the computer, set once, passed as the FIRST config layer.

Until 2026-10-28 the window asked for the ONT server, the device and a model FOLDER on
every run, and remembered none of them. Two defects came with that shape:

* the server travelled as $DORADO_SERVER, and resolve_tool ranks an explicit config value
  ABOVE the environment -- so once the window could pass a config (I2), a config naming a
  server silently beat the binary the operator had just picked (I5);
* the device had a tempting default, "metal on a Mac", which this project had already
  refused once (2026-10-05): which strings a dorado-server build accepts is that build's
  --help, observed on one Apple-silicon machine and never on the population.

Now the shell keeps settings.json per computer and derives a machine layer from it, passed
to Core first, so anything more specific overrides it. No compiler runs on this host, so
these hold the Rust, the TypeScript and Core's loader against each other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mrnaspect.main import load_config

_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

pytestmark = pytest.mark.skipif(
    not (_HTML.exists() and _TS.exists() and _RS.exists()),
    reason="GUI sources are not in this checkout")


def _fn(name: str) -> str:
    rs = _RS.read_text()
    start = rs.index(f"fn {name}(")
    return rs[start: rs.index("\n}\n", start)]


def _code(src: str) -> str:
    """Source with comments removed, so a word in an explanation is not a value."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# --- I5 is closed by construction, not patched ---------------------------------------

def test_the_server_no_longer_travels_as_an_environment_variable() -> None:
    body = _code(_fn("start_qc"))
    assert "DORADO_SERVER" not in body, (
        "start_qc sets $DORADO_SERVER again; a config outranks the environment, so the "
        "binary the operator picked would silently lose to any config naming one")
    for flag in ("--dorado-device", "--dorado-server"):
        assert flag not in body, f"start_qc passes {flag} per run; it is This Mac's fact"


def test_print_config_is_asked_with_the_same_stack_the_run_gets() -> None:
    """Otherwise "what would this run use" answers a different question from the run.

    One function asks, for the window's hints AND for start_qc deciding where the run
    writes (I8): two copies of the question are how three of four hints came to be false.
    """
    body = _fn("print_config")
    assert body.index("machine_layer(app)") < body.index("config.trim()"), (
        "print_config must pass the machine layer before the operator's config")
    assert "print_config(" in _fn("read_config")
    assert "print_config(" in _fn("start_qc")


def test_the_machine_layer_says_only_where_the_server_is_and_what_it_runs_on() -> None:
    """Every key in it is a default for every run on this computer; there are two."""
    body = _code(_fn("machine_layer"))
    assert set(re.findall(r'server\.insert\("(\w+)"', body)) == {"binary", "device"}
    assert '"basecall": { "server": server }' in body


# --- the layer works, through Core's own loader ---------------------------------------

def _serde_shaped(tmp_path: Path, binary: str = "", device: str = "") -> Path:
    """What machine_layer writes: serde_json pretty JSON, UTF-8, only the set keys."""
    server = {k: v for k, v in (("binary", binary), ("device", device)) if v}
    p = tmp_path / "machine.yaml"
    p.write_text(json.dumps({"basecall": {"server": server}}, indent=2,
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_a_json_machine_layer_is_a_valid_core_config(tmp_path) -> None:
    """JSON is valid YAML; this is the claim the Rust rests on, checked on the hard case."""
    tricky = '/Users/a b/ONT: "dorado" #1/bin/dorado_basecall_server'
    cfg = load_config([_serde_shaped(tmp_path, tricky, "metal")], {},
                      require_reference=False)
    server = cfg["basecall"]["server"]
    assert server["binary"] == tricky and server["device"] == "metal"
    assert {"port", "host", "auto_start"} <= set(server), "the shipped block was replaced"


def test_a_run_config_overrides_this_mac_and_this_mac_overrides_the_shipped_default(
        tmp_path) -> None:
    machine = _serde_shaped(tmp_path, "/opt/ont/bin/dorado_basecall_server", "metal")
    run = tmp_path / "run.yaml"
    run.write_text("basecall:\n  server:\n    device: cpu\n")
    cfg = load_config([machine, run], {}, require_reference=False)
    assert cfg["basecall"]["server"]["device"] == "cpu", "the run config must win"
    assert cfg["basecall"]["server"]["binary"] == "/opt/ont/bin/dorado_basecall_server"
    shipped = load_config(None, {}, require_reference=False)["basecall"]["server"]["device"]
    alone = load_config([machine], {}, require_reference=False)["basecall"]["server"]
    assert alone["device"] == "metal" != shipped


# --- no platform assertion ---------------------------------------------------------------

def test_no_device_is_ever_chosen_for_the_operator() -> None:
    """2026-10-05: no per-platform default anywhere. Since 2026-11-06 the field is a
    Processor menu: Auto sends nothing, so the server picks,
    and it is what a fresh install shows. GPU maps to the platform's value only when the
    operator chooses it. Rust still names no device."""
    assert '"metal"' not in _code(_RS.read_text()), "lib.rs picks a device"
    html = _HTML.read_text()
    select = re.search(r'<select id="device"[^>]*>(.*?)</select>', html, re.S)
    assert select, "the Processor menu is gone"
    options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]+)</option>', select.group(1))
    assert options[0] == ("", "Auto"), options
    assert [label for _, label in options] == ["Auto", "GPU", "CPU"]
    ts = _code(_TS.read_text())
    load = ts[ts.index("async function loadMachine("):]
    load = load[:load.index("\n}\n")]
    assert "setProcessor(m.device)" in load, "the saved value must be shown as saved"


def test_a_saved_gpu_value_round_trips() -> None:
    """A saved cuda:1 must not come back as cuda:0 because the menu has one GPU entry."""
    ts = _code(_TS.read_text())
    fn = ts[ts.index("function setProcessor("):]
    fn = fn[:fn.index("\n}\n")]
    assert 'saved && saved !== "cpu" ? saved : GPU_DEVICE' in fn


# --- settings are kept, and kept honestly -------------------------------------------------

def test_a_corrupt_settings_file_is_reported_not_reset() -> None:
    """Resetting would quietly forget the server path, and the next run would fail far
    from the cause."""
    body = _code(_fn("read_machine"))
    assert "is not valid settings JSON" in body
    assert "unwrap_or_default" not in body and "unwrap_or(" not in body


def test_a_model_folder_is_recognised_by_its_config_toml() -> None:
    assert 'join("config.toml")' in _fn("list_models")


def test_device_is_saved_on_every_edit_and_loaded_before_the_first_lookup() -> None:
    ts = _TS.read_text()
    assert re.search(r'const MACHINE = \["server", "device", "models"\]', ts)
    assert 'for (const id of MACHINE) $(id).addEventListener("change", saveMachine)' in ts
    boot = ts[ts.index("loadMachine().then("):]
    boot = boot[:boot.index("\n});\n")]
    assert "await refreshConfig();" in boot, (
        "the first print-config must already include This Mac, or its hints describe a "
        "stack without the machine layer")


# --- every call across the boundary, not only the newest ----------------------------------

def _calls() -> dict[str, set[str]]:
    """Command -> the argument keys TypeScript sends, for every invoke in main.ts."""
    ts = _TS.read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r'invoke(?:<[^>]*>)?\(\s*"([a-z_]+)"\s*(,\s*\{)?', ts):
        keys: set[str] = set()
        if m.group(2):
            start = m.end() - 1
            depth, i = 0, start
            while True:
                depth += {"{": 1, "}": -1}.get(ts[i], 0)
                if depth == 0:
                    break
                i += 1
            body = re.sub(r"//[^\n]*", "", ts[start:i + 1])
            keys = set(re.findall(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body))
        out.setdefault(m.group(1), set()).update(keys)
    return out


def test_the_scan_found_every_command() -> None:
    assert {"start_qc", "read_config", "load_machine", "save_machine",
            "list_models"} <= set(_calls())


@pytest.mark.parametrize("cmd", sorted(_calls()) or ["-"])
def test_typescript_sends_exactly_what_rust_takes(cmd) -> None:
    """camelCase in, snake_case out, and nothing type-checks the crossing."""
    rs = _RS.read_text()
    sig = rs[rs.index(f"fn {cmd}("): rs.index("->", rs.index(f"fn {cmd}("))]
    rust = set(re.findall(r"([a-z_][a-z0-9_]*)\s*:\s*(?:String|bool)", sig))
    ts = {re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower() for k in _calls()[cmd]}
    assert ts == rust, f"{cmd}: TypeScript sends {sorted(ts)}, Rust takes {sorted(rust)}"
