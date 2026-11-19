"""Pod5 mode can always be given a model, whatever the config says.

Reported from the Mac, 2026-10-28: the Model list offered only "From the
config", so a run could take its model only from the config, and a config naming a wrong
one could not be run at all -- the two shipped examples name /path/to/... placeholders.
Core was never the obstacle: --basecall-model is a CLI value and beats the config. The
window had no way to send one. Since I6 the list was filled ONLY from This Mac's models
folder, and an unset folder and one pointed a level too high (an ONT server bundle keeps
its models in models/ or data/) both left it empty, under the same sentence.

No compiler or browser runs here, so the TypeScript and the Rust are held as text; the
precedence is checked through Core's own loader.
"""

from __future__ import annotations

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


def _code(src: str) -> str:
    """Source with comments removed, so a word in an explanation is not code."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _ts_fn(name: str) -> str:
    ts = _code(_TS.read_text())
    start = ts.index(f"function {name}(")
    return ts[start: ts.index("\n}\n", start)]


def _rs_fn(name: str) -> str:
    rs = _code(_RS.read_text())
    start = rs.index(f"fn {name}(")
    return rs[start: rs.index("\n}\n", start)]


# --- the route exists, and does not depend on the config or on This Mac -------------

def test_the_model_row_has_a_browse_of_its_own() -> None:
    html = _HTML.read_text()
    assert 'data-pick="model" data-for="model"' in html
    ts = _code(_TS.read_text())
    table = ts[ts.index("const PICKERS"):ts.index("for (const button")]
    assert re.search(r"\bmodel:\s*\{\s*directory:\s*true\s*\}", table), (
        "the Model Browse must open a FOLDER picker: a dorado model is a folder")


def test_the_list_starts_with_only_from_the_config() -> None:
    """Nothing pre-chosen: a model the window chose would be one the operator did not."""
    html = _HTML.read_text()
    select = re.search(r'<select id="model">(.*?)</select>', html, re.S).group(1)
    assert re.findall(r"<option[^>]*>", select) == ['<option value="">']


def test_a_picked_folder_becomes_an_option_never_a_value() -> None:
    """A <select> ignores a value it has no option for. Writing the path would select
    nothing, without a word, and the run would fall back to the config -- the very
    failure this route exists to get around."""
    ts = _code(_TS.read_text())
    handler = ts[ts.index('button.addEventListener("click"'):]
    handler = handler[:handler.index("\n  });\n")]
    branch = handler.index("if (field instanceof HTMLSelectElement) await pickModel(chosen)")
    write = handler.index("field.value = chosen")
    assert branch < write and "else" in handler[branch:write], (
        "the picker must route a <select> to pickModel BEFORE it ever writes a value")


def test_a_this_mac_refresh_cannot_drop_a_picked_model() -> None:
    """Every edit in This Mac re-lists its models. The old refresh removed every option
    but the first, which would have taken a picked model with it."""
    fill = _ts_fn("fillModels")
    assert "macModels" in fill and "picked" in fill
    refresh = _ts_fn("refreshModels")
    assert "fillModels()" in refresh
    assert not re.search(r"\bpicked\s*(=|\.length\s*=|\.splice|\.pop|\.shift)", refresh)
    whole = _code(_TS.read_text())
    assert ".remove(1)" not in whole, "options are rebuilt by fillModels, not removed ad hoc"


def test_a_picked_folder_is_checked_before_run_not_by_the_server_after() -> None:
    pick = _ts_fn("pickModel")
    assert 'invoke<Model[]>("list_models", { modelsDir: path })' in pick
    assert "found.length !== 1" in pick, "zero models or several: say so, do not guess"


def test_list_models_answers_for_a_model_folder_itself() -> None:
    """One call for both callers: This Mac's folder, and Browse, which picks ONE model.
    Without this a picked model folder would be read as a folder of models and list
    nothing."""
    body = _rs_fn("list_models")
    itself = body.index('if dir.join("config.toml").is_file()')
    assert itself < body.index("read_dir("), "the folder itself is asked first"


def test_an_empty_list_says_which_of_its_two_causes() -> None:
    """Unset, or pointed at a folder with no model directly inside -- different fixes."""
    refresh = _TS.read_text()
    refresh = refresh[refresh.index("async function refreshModels("):]
    refresh = refresh[:refresh.index("\n}\n")]
    assert "No models folder set in Device" in refresh
    assert "a level down" in refresh, "an ONT server bundle keeps its models in models/"


# --- and what it sends wins ----------------------------------------------------------

def test_the_picked_model_travels_as_the_cli_value() -> None:
    ts = _code(_TS.read_text())
    assert re.search(r'basecallModel:\s*val\("model"\)', ts)
    start_qc = _rs_fn("start_qc")
    assert 'args.push("--basecall-model")' in start_qc


def test_a_model_from_the_window_beats_a_wrong_one_in_the_config(tmp_path) -> None:
    """The case that was hit: a config naming a model that does not exist on this
    computer. The window's choice is a CLI value, and the CLI wins."""
    cfg = tmp_path / "run.yaml"
    cfg.write_text("basecall:\n  model: /path/to/dorado-server/models/rna004_130bps_hac@v5.2.0\n")
    resolved = load_config([cfg], {"basecall.model": "/models/picked_for_this_run"},
                           require_reference=False)
    assert resolved["basecall"]["model"] == "/models/picked_for_this_run"
