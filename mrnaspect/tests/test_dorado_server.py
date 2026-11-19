"""dorado-server lifecycle (wrappers/dorado_server.py).

No GPU and no dorado here: the "server" is a few lines of Python that bind the port it was
told to bind. That is exactly the contract this module depends on — it decides readiness by
connecting to the port, not by inspecting the process — so the fake exercises the real code
path for start, readiness-wait, adoption and shutdown.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from mrnaspect.wrappers.dorado_server import (
    DEFAULTS,
    DoradoServerError,
    dorado_server,
    resolve_server_config,
    _start,
    select_devices,
    server_is_reachable,
)

# --- a stand-in server that actually accepts -------------------------------

@contextmanager
def listening_port():
    """Bind a port and DRAIN its accept queue, yielding the port number.

    The drain is the point. These tests used `listen(1)` and never accepted, so every
    reachability probe left a connection sitting in the backlog. `dorado_server()` probes
    twice (adopt decision + adopt log) and the assertion probes a third time — three
    unaccepted connections against a backlog of one.

    Linux tolerates backlog+1, so it passed here with exactly one connection of margin;
    macOS does not, and the test then reported `adopted server was killed` when nothing
    had been killed at all. Measured 2026-09-21 on Linux: with `listen(1)`, connects 1-2
    succeed and #3 is refused.

    Accepting removes the dependency on queue depth entirely, and is what a real
    dorado-server does anyway — so adding a fourth probe later cannot resurrect this.
    """
    with closing(socket.socket()) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(128)
        stop = threading.Event()

        def _drain() -> None:
            while not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                conn.close()

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        try:
            yield srv.getsockname()[1]
        finally:
            stop.set()


# --- fakes -----------------------------------------------------------------

_BINDS_THE_PORT = """
import socket, sys, time
port = int(sys.argv[sys.argv.index("--port") + 1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(128)
# Accept and drop, so probes do not pile up in the backlog (see listening_port).
import threading
def _drain():
    while True:
        try:
            c, _ = s.accept()
        except OSError:
            return
        c.close()
threading.Thread(target=_drain, daemon=True).start()
print("Starting server on port", port, flush=True)
time.sleep(300)
"""

_DIES = """
import sys
print("CUDA error: no such device", file=sys.stderr, flush=True)
sys.exit(3)
"""

_NEVER_LISTENS = """
import time
time.sleep(300)
"""


def _fake_binary(tmp_path: Path, body: str, name: str) -> Path:
    """An executable that runs ``body`` with the same interpreter, forwarding argv."""
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    launcher = tmp_path / name
    launcher.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{script}" "$@"\n',
                        encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cfg(tmp_path: Path, binary: Path, port: int, **over) -> dict:
    base = {"binary": str(binary), "port": port, "device": "cuda:0",
            "startup_timeout_s": 20.0, "stop_timeout_s": 5.0,
            "log_dir": str(tmp_path / "srvlog")}
    base.update(over)
    return base


# --- config ----------------------------------------------------------------

def test_resolve_fills_defaults_and_keeps_overrides():
    cfg = resolve_server_config({"port": 6000})
    assert cfg["port"] == 6000
    assert cfg["auto_start"] is DEFAULTS["auto_start"]
    assert cfg["binary"] == DEFAULTS["binary"]


def test_resolve_rejects_an_unknown_key():
    # A typo'd knob decides which GPU a multi-hour run lands on; it must not no-op.
    with pytest.raises(ValueError, match="unknown basecall.server key"):
        resolve_server_config({"prot": 5555})


def test_resolve_accepts_none():
    assert resolve_server_config(None) == dict(DEFAULTS)


# --- reachability ----------------------------------------------------------

def test_reachable_is_true_for_a_live_listener_and_false_for_a_closed_port():
    with listening_port() as port:
        assert server_is_reachable("127.0.0.1", port) is True

    # The "nothing is listening" half does NOT reuse the port just released. An ephemeral
    # port that has only been CLOSED is free for anyone to take, so on a busy machine
    # another process can bind it between the close and the check, and the assertion
    # fails with "True is False" — a race, reported as a defect in the code under test.
    # Observed 2026-10-06 running the suite in 6 parallel shards on a 72-core host; it
    # passes every time serially, which is exactly what makes it expensive to diagnose.
    #
    # Instead: bind a socket and never listen on it. connect() to a bound-but-unlistened
    # TCP socket is refused, and because this process holds the binding for the duration,
    # nothing else can take the port. Verified both halves against the real function.
    import socket
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    try:
        closed_port = held.getsockname()[1]
        assert server_is_reachable("127.0.0.1", closed_port, timeout_s=0.5) is False
    finally:
        held.close()


# --- GPU policy ------------------------------------------------------------

def _gpus(*used):
    return [{"index": i, "name": f"GPU{i}", "memory_used_mib": u, "utilization_pct": 0}
            for i, u in enumerate(used)]


def test_selects_the_idlest_gpu(monkeypatch):
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(20000, 500, 10, 3))
    # idle = {1, 2, 3}; the pick is by memory, so GPU 3 (3 MiB).
    assert select_devices(resolve_server_config({})) == "cuda:3"


def test_a_single_idle_gpu_machine_is_not_refused_by_default(monkeypatch):
    """The product case: a QC lab's workstation with exactly one GPU.

    A courtesy margin baked into the shipped default would make this machine — the
    pipeline's actual target — refuse to start. The margin is opt-in for shared hosts.
    """
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(4))
    assert select_devices(resolve_server_config({})) == "cuda:0"


def test_a_busy_gpu_is_refused_even_with_no_courtesy_margin(monkeypatch):
    """The always-on half of the policy: never take a card that is already working."""
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(20000))
    with pytest.raises(DoradoServerError, match="only 0 of 1 GPU"):
        select_devices(resolve_server_config({}))


def test_courtesy_margin_refuses_when_configured(monkeypatch):
    """A shared research host opts in; this project's own convention is 2."""
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(20000, 20000, 10, 3))
    cfg = resolve_server_config({"min_free_gpus_remaining": 2})
    with pytest.raises(DoradoServerError, match="min_free_gpus_remaining is 2"):
        select_devices(cfg)
    # ...and the same host with one more idle card is allowed
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(20000, 10, 10, 3))
    assert select_devices(cfg) == "cuda:3"


def test_multi_gpu_request_counts_against_the_margin(monkeypatch):
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.gpu_table",
                        lambda: _gpus(0, 0, 0, 0))
    cfg2 = {"min_free_gpus_remaining": 2}
    assert select_devices(resolve_server_config({"n_devices": 2, **cfg2})) == "cuda:0,1"
    with pytest.raises(DoradoServerError):
        select_devices(resolve_server_config({"n_devices": 3, **cfg2}))


# --- lifecycle -------------------------------------------------------------

def test_starts_waits_for_the_port_then_stops_what_it_started(tmp_path):
    binary = _fake_binary(tmp_path, _BINDS_THE_PORT, "fake_server")
    port = _free_port()
    with dorado_server(_cfg(tmp_path, binary, port), output_dir=tmp_path) as info:
        assert info["managed"] is True
        assert info["pid"] and info["device"] == "cuda:0"
        assert server_is_reachable("127.0.0.1", port)
        # the port the client will dial is exported for run_basecall.sh
        assert os.environ["DORADO_PORT"] == str(port)
    # stopped on the way out, and the env is left as we found it
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and server_is_reachable("127.0.0.1", port, timeout_s=0.2):
        time.sleep(0.2)
    assert not server_is_reachable("127.0.0.1", port, timeout_s=0.5)
    assert "DORADO_PORT" not in os.environ


def test_stops_the_server_even_when_the_block_raises(tmp_path):
    binary = _fake_binary(tmp_path, _BINDS_THE_PORT, "fake_server")
    port = _free_port()
    with pytest.raises(ValueError):
        with dorado_server(_cfg(tmp_path, binary, port), output_dir=tmp_path):
            raise ValueError("the run blew up")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and server_is_reachable("127.0.0.1", port, timeout_s=0.2):
        time.sleep(0.2)
    assert not server_is_reachable("127.0.0.1", port, timeout_s=0.5)


def test_adopts_a_running_server_and_does_not_stop_it(tmp_path):
    """The host is shared: a server we did not start must survive us untouched."""
    with listening_port() as port:
        # binary deliberately points at nothing: adoption must not try to start anything
        cfg = _cfg(tmp_path, tmp_path / "does_not_exist", port)
        with dorado_server(cfg, output_dir=tmp_path) as info:
            assert info["managed"] is False
            assert info["pid"] is None
        assert server_is_reachable("127.0.0.1", port), "adopted server was killed"


def test_refuses_when_nothing_listens_and_autostart_is_off(tmp_path):
    cfg = _cfg(tmp_path, tmp_path / "unused", _free_port(), auto_start=False)
    with pytest.raises(DoradoServerError, match="auto_start is false"):
        with dorado_server(cfg, output_dir=tmp_path):
            pass


def test_missing_binary_is_an_operator_error_not_a_traceback(tmp_path):
    """Still an operator error; the wording moved when discovery replaced the hardcoded
    path (2026-09-19). What has to hold is that it is a DoradoServerError and that the
    message NAMES the offending path — an error that does not say which path is not
    actionable."""
    missing = tmp_path / "no_such_server"
    cfg = _cfg(tmp_path, missing, _free_port())
    with pytest.raises(DoradoServerError, match="does not exist") as exc:
        with dorado_server(cfg, output_dir=tmp_path):
            pass
    assert str(missing) in str(exc.value)


def test_a_server_that_exits_during_startup_reports_its_log(tmp_path):
    binary = _fake_binary(tmp_path, _DIES, "dying_server")
    cfg = _cfg(tmp_path, binary, _free_port())
    with pytest.raises(DoradoServerError, match="exited during startup") as exc:
        with dorado_server(cfg, output_dir=tmp_path):
            pass
    # the reason must travel with the error — the operator has no other copy of it
    assert "CUDA error" in str(exc.value)


def test_a_server_that_never_listens_times_out_and_is_cleaned_up(tmp_path):
    binary = _fake_binary(tmp_path, _NEVER_LISTENS, "deaf_server")
    cfg = _cfg(tmp_path, binary, _free_port(), startup_timeout_s=2.0)
    with pytest.raises(DoradoServerError, match="did not accept connections"):
        with dorado_server(cfg, output_dir=tmp_path):
            pass
    # no orphan left behind
    assert not any(p.name.startswith("deaf_server") and p.is_dir()
                   for p in tmp_path.iterdir())


def test_env_var_is_restored_not_clobbered(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PORT", "9999")
    with listening_port() as port:
        with dorado_server(_cfg(tmp_path, tmp_path / "x", port), output_dir=tmp_path):
            assert os.environ["DORADO_PORT"] == str(port)
    assert os.environ["DORADO_PORT"] == "9999"


def test_a_managed_server_leaves_a_pid_breadcrumb_and_removes_it(tmp_path):
    """SIGKILL skips every `finally`, so the PID must reach disk while the server lives.

    Provenance carries it too, but that is written at finalize — exactly what a hard kill
    never reaches. Once the server is stopped the file would point at a PID the OS can
    reuse, so it goes with the process.
    """
    binary = _fake_binary(tmp_path, _BINDS_THE_PORT, "fake_server")
    port = _free_port()
    log_dir = tmp_path / "srvlog"
    with dorado_server(_cfg(tmp_path, binary, port), output_dir=tmp_path) as info:
        pid_file = log_dir / "server.pid"
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(info["pid"])
    assert not (log_dir / "server.pid").exists()


def test_an_adopted_server_leaves_no_breadcrumb(tmp_path):
    """We did not start it, so there is nothing of ours to clean up later."""
    with listening_port() as port:
        with dorado_server(_cfg(tmp_path, tmp_path / "unused", port),
                           output_dir=tmp_path) as info:
            assert info["managed"] is False
        assert not (tmp_path / "srvlog" / "server.pid").exists()


# ---------------------------------------------------------------------------
# The same environment variable must mean the same thing on both launch paths
# ---------------------------------------------------------------------------
# `start_server.sh` has always honoured $DORADO_DEVICE; this Python path never did, so the
# variable meant different things depending on how the pipeline was started. On macOS
# 2026-09-23 `DORADO_DEVICE=metal` was accepted by the script and ignored here, and the run
# died querying nvidia-smi — a machine with no NVIDIA anything being asked about its NVIDIA
# driver. Same shape as $DORADO_SERVER reaching the server but not the client's sibling
# search: one name, two behaviours, depending on the route in.

def _server_cfg(tmp_path: Path, **over):
    """A fake server that really binds its port, so `_start` reaches the device line.

    A stub that just exits leaves `_start` waiting for reachability and failing there —
    which would fail these tests for a reason that has nothing to do with device
    selection.
    """
    binary = _fake_binary(tmp_path, _BINDS_THE_PORT, "fake_server")
    return resolve_server_config(
        {"binary": str(binary), "port": _free_port(), "device": None,
         "startup_timeout_s": 20.0, "stop_timeout_s": 5.0,
         "log_dir": str(tmp_path / "srvlog"), **over})


def test_dorado_device_env_is_honoured_and_skips_gpu_selection(tmp_path, monkeypatch):
    """With the variable set, automatic selection must not even be consulted.

    Asserted by making `select_devices` explode: a test that only checked the resulting
    device string would pass on a machine that happens to have a GPU.
    """
    def _explode(_cfg):
        raise AssertionError("select_devices was called despite $DORADO_DEVICE being set")

    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.select_devices", _explode)
    monkeypatch.setenv("DORADO_DEVICE", "metal")
    cfg = _server_cfg(tmp_path)
    assert cfg["device"] is None                      # nothing in config; env must win
    proc, device = _start(cfg, tmp_path)
    try:
        assert device == "metal", device
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_config_beats_the_environment(tmp_path, monkeypatch):
    """Explicit config is more specific than an exported default, and must win."""
    monkeypatch.setattr("mrnaspect.wrappers.dorado_server.select_devices",
                        lambda _c: (_ for _ in ()).throw(AssertionError("not reached")))
    monkeypatch.setenv("DORADO_DEVICE", "metal")
    cfg = _server_cfg(tmp_path, device="cuda:7")
    proc, device = _start(cfg, tmp_path)
    try:
        assert device == "cuda:7", device
    finally:
        proc.terminate()
        proc.wait(timeout=30)


# --------------------------------------------------------------------------
# A host with no NVIDIA GPU is not a broken host
# --------------------------------------------------------------------------

def _gpu_table_raising(monkeypatch, exc):
    import subprocess as _sp
    from mrnaspect.wrappers import dorado_server as ds

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(ds.subprocess, "run", boom)
    with pytest.raises(ds.DoradoServerError) as e:
        ds.gpu_table()
    return str(e.value)


def test_absent_nvidia_smi_is_reported_as_absent_not_as_a_failed_query(monkeypatch):
    """Observed on a Mac 2026-09-23: "could not query nvidia-smi" sent the operator
    looking for a broken NVIDIA driver on a machine that never had an NVIDIA GPU.

    nvidia-smi ships WITH the driver, so its absence is the ordinary state of every
    non-NVIDIA host and says nothing is wrong — only that there is nothing to select
    from.
    """
    msg = _gpu_table_raising(monkeypatch, FileNotFoundError(2, "No such file"))
    assert "does not have" in msg and "nothing is broken" in msg
    assert "could not query" not in msg


def test_it_names_every_way_to_set_a_device_and_asserts_no_platform_default(monkeypatch):
    """The fix is NOT "use metal on a Mac".

    Which device strings a build accepts is dorado-server's question: `metal` was seen on
    one Apple-silicon Mac, an Intel Mac may have no usable Metal backend, and `cpu` is
    valid everywhere. So the message points at the server's own --help and offers
    examples, rather than this pipeline claiming a per-platform default it has not
    measured.
    """
    msg = _gpu_table_raising(monkeypatch, FileNotFoundError(2, "No such file"))
    for route in ("--dorado-device", "$DORADO_DEVICE", "basecall.server.device"):
        assert route in msg, f"the message must name {route}"
    assert "--help" in msg
    for example in ("cuda", "metal", "cpu"):
        assert example in msg, "an NVIDIA-only example is useless on a machine with none"


def test_a_present_but_failing_nvidia_smi_says_something_different(monkeypatch):
    """Installed and not answering IS a malfunction, unlike the branch above, and the
    two must not share a message: one needs a device named, the other needs fixing."""
    import subprocess as _sp
    msg = _gpu_table_raising(monkeypatch, _sp.CalledProcessError(1, "nvidia-smi"))
    assert "installed but did not answer" in msg
    assert "fix the driver" in msg
    assert "does not have" not in msg
