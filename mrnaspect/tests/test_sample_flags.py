"""The sample and batch fields, from the window to the report (2026-11-09).

Core had the flags (--manufacturer, --product, --batch-id, --sample-id, --manufacture-date,
--operator) but the window had no field for them, so a report run from the GUI always
said "No sample or batch details were supplied". And only the offline driver took the
flags: the live driver, which a GUI live run launches, would have refused them as
unrecognized arguments. One table (main.SAMPLE_FLAGS) now feeds both parsers, and it must
name exactly the fields the report prints (reporter._DECLARED_FIELDS).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from mrnaspect.reporting.reporter import _DECLARED_FIELDS

from .test_gui_start_flow import _inside, _step, _tree

PY = [sys.executable, "-m", "mrnaspect.main"]
_GUI = Path(__file__).resolve().parents[2] / "gui"
_HTML = _GUI / "index.html"
_TS = _GUI / "src" / "main.ts"
_RS = _GUI / "src-tauri" / "src" / "lib.rs"

KEYS = [k for k, _name in _DECLARED_FIELDS]
#: One value per field, each distinct, so a value landing under the wrong key shows.
VALUES = {"manufacturer": "Acme Bio", "product": "QK-mRNA", "batch_id": "LOT-2026-11",
          "sample_id": "S-07", "manufacture_date": "2026-11-01", "operator": "tech-1"}


def _flag(key: str) -> str:
    """argparse's own rule, read backwards: --batch-id is stored as batch_id."""
    return "--" + key.replace("_", "-")


def _argv() -> list[str]:
    return [a for k in KEYS for a in (_flag(k), VALUES[k])]


# --- Core: one table, both drivers -----------------------------------------------------

def test_the_values_cover_every_field() -> None:
    assert set(VALUES) == set(KEYS)


def test_the_flags_are_the_fields_the_report_prints() -> None:
    from mrnaspect.main import SAMPLE_FLAGS

    assert [(flag, key) for flag, key, _help in SAMPLE_FLAGS] == [(_flag(k), k) for k in KEYS]


def test_the_offline_driver_takes_every_one() -> None:
    r = subprocess.run([*PY, "--print-config", *_argv(), "--reference", "x.fa"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    sample = json.loads(r.stdout)["resolved_config"]["sample"]
    assert {k: sample.get(k) for k in KEYS} == VALUES


def test_the_live_driver_takes_every_one_too(monkeypatch) -> None:
    """What the live driver hands load_config, caught before it builds anything (as in
    test_arm_flag). It reports config["sample"] as the offline driver does."""
    from mrnaspect.realtime import main as rt

    seen: dict = {}

    def capture(config, overrides, *args, **kwargs):
        seen.update(overrides)
        raise ValueError("stop: the overrides are what this test reads")

    monkeypatch.setattr(rt, "load_config", capture)
    assert rt.main([*_argv(), "--input-dir", "in", "--output-dir", "out"]) == 2
    assert {k: seen.get(f"sample.{k}") for k in KEYS} == VALUES


# --- the window (text checks: no Rust or TypeScript toolchain here) ----------------------

def _sample_table() -> list[tuple[str, str, str]]:
    """The window's SAMPLE table: (field id, config key, label)."""
    ts = _TS.read_text()
    start = ts.index("const SAMPLE")
    table = ts[start:ts.index("];", start)]
    return re.findall(r'\["([a-z]+)",\s*"sample\.([a-z_]+)",\s*"([^"]+)"\]', table)


def test_the_window_asks_for_each_field_the_report_prints() -> None:
    """Same keys, same names, same order as the report's Sample & Batch rows."""
    assert [(k, n) for _id, k, n in _sample_table()] == list(_DECLARED_FIELDS)


@pytest.mark.parametrize("key", KEYS)
def test_each_field_sits_in_the_sample_step_inside_the_lock(key: str) -> None:
    field = {k: i for i, k, _n in _sample_table()}[key]
    tree = _tree()
    # Their own screen since one choice per screen (2026-11-12); step 2 of three before.
    assert _step(tree, field) == "sample"
    assert _inside(tree, field, "setupfields")


def test_each_field_is_labelled_as_the_report_names_it() -> None:
    html = _HTML.read_text()
    for field, _key, name in _sample_table():
        assert html.count(f'id="{field}"') == 1, f"{field}: not exactly one element"
        assert re.search(rf'<label for="{field}">{re.escape(name)}</label>', html), field


def test_the_window_sends_each_one() -> None:
    ts = _TS.read_text()
    start = ts.index('"start_qc"')
    call = ts[start:ts.index("});", start)]
    for field, key, _n in _sample_table():
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), key)
        assert re.search(rf'\b{camel}:\s*val\("{field}"\)', call), camel


def test_rust_passes_each_only_when_typed_whichever_driver_runs() -> None:
    """After both drivers' argument lists are built, beside --arm, so the live run gets
    them too; and only when typed, so a blank field leaves the config's value."""
    rs = _RS.read_text()
    start = rs.index("fn start_qc(")
    sig = rs[start:rs.index("->", start)]
    body = rs[start:rs.index("\n}\n", start)]
    tail = body[body.index('args.push("--arm")'):]
    for key in KEYS:
        assert f"{key}: String" in sig, key
        assert re.search(rf'\("{_flag(key)}",\s*{key}\.as_str\(\)\)', tail), key
    loop = tail[tail.index("for (flag, value) in"):]
    assert re.search(r"if !value\.trim\(\)\.is_empty\(\) \{\s*args\.push\(flag\);"
                     r"\s*args\.push\(value\.trim\(\)\);", loop)


def test_a_config_s_sample_shows_as_each_field_s_hint() -> None:
    """PREFILL drives the grey "(from config)" hints, so the sample fields join it. SAMPLE
    must be declared first: a const read before its declaration throws when the module
    loads, and no compiler here would say so."""
    ts = _TS.read_text()
    assert ts.index("const SAMPLE") < ts.index("const PREFILL")
    prefill = ts[ts.index("const PREFILL"):ts.index("const AUTHORED")]
    assert re.search(r"\.\.\.SAMPLE\.map\(", prefill)


def test_the_review_lists_what_was_declared() -> None:
    ts = _TS.read_text()
    body = ts[ts.index("function reviewRows"):]
    body = body[:body.index("\n}\n")]
    assert re.search(r"SAMPLE\.map\(", body) and "eff(id, key)" in body
