"""dorado-server lifecycle: start one if none is reachable, stop only what we started.

A pod5-mode run needs a dorado-server for its whole duration. :func:`dorado_server`
adopts a server already listening on the configured port, and never stops it, or starts
one and stops it when the run ends.

* Reachability is a TCP connect to the configured port; shutdown only ever signals the
  process this module started.
* Automatic device selection never takes a GPU already in use (``nvidia-smi``).
  ``min_free_gpus_remaining`` can also keep idle GPUs free for other users; it defaults
  to 0 so that a one-GPU machine can start.

The port reaches ``run_basecall.sh`` through ``DORADO_PORT`` in the environment, so the
client dials the server adopted or started here.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from ._shell import resolve_tool

logger = logging.getLogger(__name__)


class DoradoServerError(RuntimeError):
    """The daemon could not be reached, started, or safely placed on a GPU.

    A class of its own so a CLI can report it as an operator problem without also
    catching a RuntimeError raised inside the run, which is a bug.
    """

#: Defaults for the ``basecall.server`` config section. Use a server and a client from
#: the same dorado-server bundle (this pipeline uses 7.13.6).
DEFAULTS: Mapping[str, Any] = {
    "auto_start": True,
    # Path to dorado_basecall_server. None -> $DORADO_SERVER, else searched for
    # (resolve_tool). dorado is ONT-proprietary and not shipped with this package.
    "binary": None,
    # Path to ont_basecall_client. None -> discovered, including beside ``binary``.
    "client": None,
    "host": "127.0.0.1",
    "port": 5555,
    # None -> $DORADO_DEVICE, else idle GPUs picked from nvidia-smi under the policy
    # below. A string (e.g. "cuda:0") is used as given, bypassing the policy.
    "device": None,
    "n_devices": 1,
    # Idle GPUs that must stay idle after this server takes its own: a shared-host
    # courtesy, 0 by default so a one-GPU machine can start. A busy GPU is never taken
    # by automatic selection, whatever this says.
    "min_free_gpus_remaining": 0,
    # A GPU using at most this many MiB counts as idle: above driver overhead, far below
    # any real job.
    "free_gpu_mib": 1024,
    "log_dir": None,           # None -> <output_dir>/dorado_server
    "startup_timeout_s": 120.0,
    "stop_timeout_s": 20.0,
}

_READY_POLL_S = 0.5


def resolve_server_config(config: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge ``config`` onto :data:`DEFAULTS`, raising on an unknown key."""
    config = dict(config or {})
    unknown = sorted(set(config) - set(DEFAULTS))
    if unknown:
        raise ValueError(
            f"unknown basecall.server key(s) {unknown}; allowed: {sorted(DEFAULTS)}")
    return {**DEFAULTS, **config}


# ---------------------------------------------------------------------------
# reachability
# ---------------------------------------------------------------------------

def server_is_reachable(host: str, port: int, *, timeout_s: float = 1.0) -> bool:
    """True if something accepts TCP on ``host:port``.

    Any listener counts: this cannot tell a dorado-server from another program.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex((host, int(port))) == 0


# ---------------------------------------------------------------------------
# device selection
# ---------------------------------------------------------------------------

def gpu_table() -> list[dict[str, Any]]:
    """``nvidia-smi`` as a list of ``{index, name, memory_used_mib, utilization_pct}``."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    except FileNotFoundError as exc:
        # No nvidia-smi: a host without an NVIDIA driver, not a fault. No device is
        # guessed instead; which values a build accepts is dorado-server's to say.
        raise DoradoServerError(
            "automatic GPU selection needs nvidia-smi, which this machine does not have. "
            "It ships with the NVIDIA driver, so a host with no NVIDIA GPU never has it "
            "and nothing is broken — there is simply nothing to select from. Name the "
            "device yourself, with --dorado-device, $DORADO_DEVICE or "
            "basecall.server.device. Which values your build accepts is dorado-server's "
            "question, not this pipeline's: ask it with `<dorado_basecall_server> --help` "
            "(commonly 'cuda:N' on NVIDIA, 'metal' on an Apple-silicon Mac, 'cpu' "
            "anywhere)." ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        # nvidia-smi exists but failed: a real fault (driver, permissions, a timeout).
        raise DoradoServerError(
            f"nvidia-smi is installed but did not answer ({exc}), so idle GPUs cannot be "
            "measured. Name a device explicitly (--dorado-device, $DORADO_DEVICE or "
            "basecall.server.device, e.g. 'cuda:0') to start the server without automatic "
            "selection — or fix the driver, since a basecall will need it either way."
        ) from exc
    rows: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        idx, name, used, util = (f.strip() for f in line.split(",", 3))
        rows.append({"index": int(idx), "name": name,
                     "memory_used_mib": int(used), "utilization_pct": int(util)})
    return rows


def _format_gpu_table(rows: list[dict[str, Any]]) -> str:
    return "; ".join(f"{r['index']}:{r['name']} {r['memory_used_mib']}MiB "
                     f"{r['utilization_pct']}%" for r in rows)


def select_devices(cfg: Mapping[str, Any]) -> str:
    """Pick the ``n_devices`` least-loaded idle GPUs (ties by index), or raise.

    The error carries the GPU table and the rule that refused.
    """
    rows = gpu_table()
    idle = [r for r in rows if r["memory_used_mib"] <= cfg["free_gpu_mib"]]
    n = int(cfg["n_devices"])
    keep = int(cfg["min_free_gpus_remaining"])
    if len(idle) < n:
        raise DoradoServerError(
            f"refusing to start dorado-server: only {len(idle)} of {len(rows)} GPU(s) are "
            f"idle (<= {cfg['free_gpu_mib']} MiB) and {n} are needed. Taking a GPU that is "
            f"already working would slow or OOM whatever is on it. Current state: "
            f"{_format_gpu_table(rows)}. Wait, or name a device explicitly to override.")
    if len(idle) - n < keep:
        raise DoradoServerError(
            f"refusing to start dorado-server: {len(idle)} of {len(rows)} GPU(s) are idle "
            f"(<= {cfg['free_gpu_mib']} MiB), and taking {n} would leave {len(idle) - n} "
            f"free — this host's basecall.server.min_free_gpus_remaining is {keep}. That "
            f"is a shared-host courtesy setting, not a hardware limit. Current state: "
            f"{_format_gpu_table(rows)}. Wait, lower the setting, or name a device.")
    chosen = sorted(idle, key=lambda r: (r["memory_used_mib"],
                                         r["utilization_pct"], r["index"]))[:n]
    device = "cuda:" + ",".join(str(r["index"]) for r in chosen)
    logger.warning(
        "dorado-server: auto-selected %s (%s); %d idle GPU(s) left. Pin "
        "basecall.server.device to choose a specific card instead.",
        device, ", ".join(r["name"] for r in chosen), len(idle) - n)
    return device


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def _log_tail(path: Path, n: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-n:])
    except OSError:
        return "(no server log)"


def _start(cfg: Mapping[str, Any], log_dir: Path) -> tuple[subprocess.Popen, str]:
    try:
        binary = Path(resolve_tool(
            "dorado_basecall_server", cfg.get("binary"), env_var="DORADO_SERVER",
            hint="dorado-server is ONT-proprietary and cannot be shipped with this "
                 "pipeline, so it must already be installed. Set "
                 "basecall.server.binary to its path, export $DORADO_SERVER, or set "
                 "basecall.server.auto_start=false and start one yourself."))
    except FileNotFoundError as exc:
        raise DoradoServerError(str(exc)) from exc
    # Config, then $DORADO_DEVICE, then automatic selection.
    device = cfg["device"] or os.environ.get("DORADO_DEVICE") or select_devices(cfg)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "server_stdout.log"
    cmd = [str(binary), "--port", str(cfg["port"]), "--use_tcp",
           "--device", str(device), "--log_path", str(log_dir)]
    logger.info("dorado-server: starting — %s", " ".join(cmd))
    # A new session: a terminal Ctrl-C must not kill the server under an in-flight
    # basecall. It is stopped when the dorado_server() block exits.
    with open(stdout_path, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)

    # A SIGKILL skips every `finally` and orphans the server; this file keeps its pid
    # where the operator can find it.
    pid_path = log_dir / "server.pid"
    try:
        pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    except OSError as exc:  # never fail a run over a breadcrumb
        logger.warning("could not write %s (%s)", pid_path, exc)

    deadline = time.monotonic() + float(cfg["startup_timeout_s"])
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DoradoServerError(
                f"dorado-server exited during startup (code {proc.returncode}). Last lines "
                f"of {stdout_path}:\n{_log_tail(stdout_path)}")
        if server_is_reachable(cfg["host"], cfg["port"]):
            waited = float(cfg["startup_timeout_s"]) - (deadline - time.monotonic())
            logger.info("dorado-server: ready on %s:%s after %.1fs (pid %d, device %s)",
                        cfg["host"], cfg["port"], waited, proc.pid, device)
            return proc, str(device)
        time.sleep(_READY_POLL_S)

    _stop(proc, cfg)
    raise DoradoServerError(
        f"dorado-server did not accept connections on {cfg['host']}:{cfg['port']} within "
        f"{cfg['startup_timeout_s']}s. Last lines of {stdout_path}:\n"
        f"{_log_tail(stdout_path)}")


def _stop(proc: subprocess.Popen, cfg: Mapping[str, Any]) -> None:
    """Terminate OUR server process; escalate to SIGKILL only if it will not go."""
    if proc.poll() is not None:
        logger.warning("dorado-server: already gone before shutdown (exit code %s) — any "
                       "input that failed late in this run may have failed for that "
                       "reason", proc.returncode)
        return
    logger.info("dorado-server: stopping pid %d", proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=float(cfg["stop_timeout_s"]))
    except subprocess.TimeoutExpired:
        logger.warning("dorado-server: pid %d ignored SIGTERM for %.0fs; sending SIGKILL",
                       proc.pid, float(cfg["stop_timeout_s"]))
        proc.kill()
        proc.wait(timeout=10)
    logger.info("dorado-server: stopped")


@contextlib.contextmanager
def dorado_server(config: Optional[Mapping[str, Any]], *,
                  output_dir: Path | str) -> Iterator[dict[str, Any]]:
    """Ensure a reachable dorado-server for the duration of the block.

    * **adopted**: something already listens on the port. It is used and never stopped.
    * **managed**: this process started it, and stops it however the block exits.
    * **refused**: nothing is listening and ``auto_start`` is off, or a server cannot be
      started (no binary, no GPU free under the policy, startup failed). Raises
      :class:`DoradoServerError` before any input is processed.

    ``DORADO_PORT`` is set to the port for the block and restored afterwards. Yields a
    dict describing the outcome, which the run records in its provenance.
    """
    cfg = resolve_server_config(config)
    host, port = str(cfg["host"]), int(cfg["port"])
    prior_env = os.environ.get("DORADO_PORT")
    os.environ["DORADO_PORT"] = str(port)
    proc: Optional[subprocess.Popen] = None
    log_dir: Optional[Path] = None
    try:
        if server_is_reachable(host, port):
            logger.info(
                "dorado-server: adopting the server already listening on %s:%d. This "
                "process did NOT start it and will NOT stop it.", host, port)
            yield {"managed": False, "host": host, "port": port, "device": None,
                   "pid": None}
            return
        if not cfg["auto_start"]:
            raise DoradoServerError(
                f"no dorado-server on {host}:{port} and basecall.server.auto_start is "
                f"false. Start one with `bash mrnaspect/wrappers/start_server.sh` "
                f"or set auto_start: true.")
        log_dir = (Path(cfg["log_dir"]) if cfg["log_dir"]
                   else Path(output_dir) / "dorado_server")
        proc, device = _start(cfg, log_dir)
        yield {"managed": True, "host": host, "port": port, "device": device,
               "pid": proc.pid, "log_dir": str(log_dir)}
    finally:
        if proc is not None:
            _stop(proc, cfg)
        if log_dir is not None:
            # Gone with the process: once it is stopped, the OS may reuse the pid.
            (log_dir / "server.pid").unlink(missing_ok=True)
        if prior_env is None:
            os.environ.pop("DORADO_PORT", None)
        else:
            os.environ["DORADO_PORT"] = prior_env
