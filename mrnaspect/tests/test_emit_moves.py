"""The move table (dorado's ``mv`` tag) is written only when ``basecall.emit_moves`` is on.

Nothing in mRNAspect reads it, and it is most of an aligned record: on 50,000 stock m1Ψ
reads it was 87.6% of the aligned SAM text, and the align step took 36.6 s with it and
19.0 s without (BAM 202 MB and 98 MB). It stays available for signal-level tools.

``run_basecall.sh`` runs for real here, against a stub client and a socket that only
listens, so the check is on the arguments the client actually receives.
"""

from __future__ import annotations

import os
import socket
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from mrnaspect.main import OfflinePipeline, load_config
from mrnaspect.tests.conftest import pod5_ready
from mrnaspect.wrappers import basecaller

_SCRIPT = Path(basecaller.__file__).resolve().parent / "run_basecall.sh"

# Writes its arguments one per line, and leaves a chunk BAM where -s points, as the real
# client would: the script will not publish an output that holds no BAM.
_STUB_CLIENT = """#!/bin/bash
printf '%s\\n' "$@" > "$ARGS_OUT"
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-s" ]]; then : > "$2/bam_0_0.bam"; fi
    shift
done
"""


def _client_args(tmp_path: Path, extra_env: dict[str, str]) -> list[str]:
    """Run run_basecall.sh against the stub client; return the arguments it received."""
    client = tmp_path / "ont_basecall_client"
    client.write_text(_STUB_CLIENT, encoding="utf-8")
    client.chmod(0o755)
    pod5 = tmp_path / "x.pod5"
    pod5.write_bytes(b"")
    model = tmp_path / "model"
    model.mkdir()
    args_out = tmp_path / "client_args.txt"
    env = {k: v for k, v in os.environ.items() if k != "EMIT_MOVES"}
    # The script refuses to start unless something listens on $DORADO_PORT.
    with closing(socket.socket()) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        env.update({"DORADO_CLIENT": str(client), "ARGS_OUT": str(args_out),
                    "DORADO_PORT": str(srv.getsockname()[1]), **extra_env})
        subprocess.run(["bash", str(_SCRIPT), str(pod5), "x", str(tmp_path / "out"),
                        str(model)], env=env, check=True, capture_output=True)
    return args_out.read_text(encoding="utf-8").splitlines()


def test_the_script_does_not_ask_for_the_move_table_by_default(tmp_path: Path) -> None:
    args = _client_args(tmp_path, {})
    assert "--estimate_poly_a" in args  # the stub saw the real call
    assert "--emit_moves" not in args


def test_the_script_asks_for_it_when_told_to(tmp_path: Path) -> None:
    assert "--emit_moves" in _client_args(tmp_path, {"EMIT_MOVES": "1"})


@pytest.mark.parametrize("kwargs, expected", [({}, "0"), ({"emit_moves": True}, "1")])
def test_shell_basecall_hands_the_setting_to_the_script(
    monkeypatch, tmp_path: Path, kwargs: dict, expected: str
) -> None:
    seen: dict = {}

    def fake_run_logged(cmd, log_path, *, stage, **kw):
        seen["env"] = kw["env"]
        (tmp_path / "basecall_x").mkdir(exist_ok=True)
        return log_path

    monkeypatch.setattr(basecaller, "run_logged", fake_run_logged)
    client = tmp_path / "ont_basecall_client"
    client.write_text("#!/bin/sh\n", encoding="utf-8")
    client.chmod(0o755)
    basecaller.shell_basecall(tmp_path / "x.pod5", tmp_path, client=str(client), **kwargs)
    assert seen["env"]["EMIT_MOVES"] == expected


def _pipeline(tmp_path: Path, reference: Path, **basecall) -> OfflinePipeline:
    cfg = pod5_ready(load_config(
        None, {"reference": str(reference), "mode": "pod5", "min_reads": 1}))
    cfg["basecall"].update(basecall)
    return OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)


def test_the_shipped_config_leaves_it_off(tmp_path: Path, curlcake_ref: Path) -> None:
    assert _pipeline(tmp_path, curlcake_ref).basecaller.keywords["emit_moves"] is False


def test_the_config_turns_it_on(tmp_path: Path, curlcake_ref: Path) -> None:
    pipe = _pipeline(tmp_path, curlcake_ref, emit_moves=True)
    assert pipe.basecaller.keywords["emit_moves"] is True


@pytest.mark.parametrize("value", ["false", 0, None])
def test_only_true_or_false_is_accepted(
    tmp_path: Path, curlcake_ref: Path, value
) -> None:
    # A quoted "false" is a non-empty string, which Python's bool() reads as true.
    with pytest.raises(ValueError, match="basecall.emit_moves"):
        _pipeline(tmp_path, curlcake_ref, emit_moves=value)
