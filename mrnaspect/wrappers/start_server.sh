#!/bin/bash
# =============================================================================
# dorado-server start/stop/status script (7.13.6 bundle)
# =============================================================================
#
# Usage:
#   bash start_server.sh [start|stop|status]
#     start:  start the server (default)
#     stop:   stop the server this script started on $DORADO_PORT
#     status: show server status
#
# Options (via environment variables):
#   DORADO_SERVER=<path>          dorado_basecall_server binary (default: found on PATH)
#   DORADO_PORT=5555              server port (default: 5555)
#   DORADO_DEVICE=cuda:0          device (default: one idle GPU, auto-selected from
#                                 nvidia-smi as in a pipeline run; needs mrnaspect)
#   DORADO_LOG=<dir>              log directory
#                                 (default: ${TMPDIR:-/tmp}/mrnaspect_dorado_logs)
#   PYTHON=python3                interpreter used for the auto-selection
#
# Examples:
#   bash start_server.sh                            # start on an auto-selected GPU
#   DORADO_DEVICE=cuda:1 bash start_server.sh       # start on a GPU you chose
#   bash start_server.sh stop
#   bash start_server.sh status
#
# Models are loaded on demand: the client passes an absolute --model folder path (a
# stock model or an exported fine-tuned one), so the server needs no -d/data_path.
# =============================================================================

set -euo pipefail

# --- config (7.13.6 bundle) ---
# dorado is ONT-proprietary and cannot ship with this pipeline: point at YOUR install.
SERVER=${DORADO_SERVER:-dorado_basecall_server}
PORT=${DORADO_PORT:-5555}
# Empty: one idle GPU is picked below by dorado_server.select_devices(), the same
# selection a pipeline run makes.
DEVICE=${DORADO_DEVICE:-}
PYTHON=${PYTHON:-python3}
# Only this script uses this default; a pipeline run logs into its own run directory.
LOG_PATH=${DORADO_LOG:-${TMPDIR:-/tmp}/mrnaspect_dorado_logs}

# --- identifying OUR server --------------------------------------------------------
# Never `pgrep -f`: it matches every process whose command line mentions the binary,
# including this script's own caller. Everything below is scoped to $PORT:
#   "is my port taken?"        -> probe the port
#   "what do I stop?"          -> the pid this script wrote down, re-verified
#   "is that pid still ours?"  -> the EXECUTABLE name of that one pid
PID_FILE="$LOG_PATH/dorado_server_$PORT.pid"

port_in_use() {
    # bash's own /dev/tcp: no nc/lsof/ss dependency, and present in bash 3.2 (macOS).
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null
}

is_our_server() {
    # `comm` is the executable name of ONE pid, not a scan of command lines. Linux
    # truncates it to 15 chars ("dorado_basecall"), macOS does not: hence the substring
    # match, which a shell cannot satisfy (its comm is bash/zsh whatever its arguments).
    [ -n "${1:-}" ] || return 1
    case "$(ps -p "$1" -o comm= 2>/dev/null)" in
        *dorado_basecall*) return 0 ;;
        *)                 return 1 ;;
    esac
}

server_pid() {
    # The pid recorded for THIS port, and only if it is still that server. A stale file
    # (crash, reboot, pid reuse) yields nothing rather than a wrong target to kill.
    [ -f "$PID_FILE" ] || return 1
    _pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
    is_our_server "$_pid" || return 1
    printf '%s' "$_pid"
}
mkdir -p "$LOG_PATH"

ACTION=${1:-start}

case "$ACTION" in
    start)
        if [[ -z "$DEVICE" ]]; then
            # The pipeline's own selection. On failure its message (the nvidia-smi table
            # and the rule that refused) is shown verbatim.
            if ! DEVICE=$("$PYTHON" -c "
from mrnaspect.wrappers.dorado_server import select_devices, DEFAULTS
print(select_devices({**DEFAULTS, 'n_devices': 1}))" 2>&1); then
                echo "ERROR: could not auto-select a GPU."
                echo "$DEVICE" | sed 's/^/  /'
                echo
                echo "Pick one yourself once you know it is idle:"
                echo "    DORADO_DEVICE=cuda:0 bash start_server.sh"
                echo "(If the import failed, this needs the mrnaspect package: pixi run dev-install)"
                exit 1
            fi
            echo "auto-selected device: $DEVICE  (nvidia-smi policy, same as the pipeline)"
        fi
        if ! command -v "$SERVER" >/dev/null 2>&1 && [[ ! -x "$SERVER" ]]; then
            echo "ERROR: dorado-server not found: $SERVER"
            echo "       Set DORADO_SERVER to your ONT dorado-server binary."
            exit 1
        fi
        mkdir -p "$LOG_PATH"
        if port_in_use; then
            echo "WARNING: something is already listening on port $PORT."
            if _p=$(server_pid); then
                echo "  It is the dorado-server this script started (pid $_p)."
                echo "  Stop it first:   DORADO_PORT=$PORT bash start_server.sh stop"
            else
                echo "  It was NOT started by this script, so this script will not touch it."
                echo "  Use another port: DORADO_PORT=<n> bash start_server.sh"
            fi
            exit 1
        fi

        echo "=== GPU status ==="
        nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total \
            --format=csv,noheader 2>/dev/null || echo "(nvidia-smi unavailable)"
        echo ""

        mkdir -p "$LOG_PATH"

        echo "=== starting dorado-server ==="
        echo "Port:   $PORT"
        echo "Device: $DEVICE"
        echo "Log:    $LOG_PATH"
        echo ""

        nohup $SERVER \
            --port "$PORT" \
            --use_tcp \
            --device "$DEVICE" \
            --log_path "$LOG_PATH" \
            > "$LOG_PATH/server_stdout.log" 2>&1 &

        SERVER_PID=$!
        echo "$SERVER_PID" > "$PID_FILE"
        echo "Server PID: $SERVER_PID  (recorded in $PID_FILE)"

        echo -n "waiting for the server to be ready"
        for i in $(seq 1 30); do
            if grep -q "Starting server on port" "$LOG_PATH/server_stdout.log" 2>/dev/null; then
                echo " OK"
                echo ""
                grep "Starting server" "$LOG_PATH/server_stdout.log"
                grep "gpu device" "$LOG_PATH/server_stdout.log" 2>/dev/null || true
                exit 0
            fi
            echo -n "."
            sleep 1
        done
        echo " TIMEOUT"
        echo "WARNING: server not ready within 30s. Check the log:"
        echo "  tail -20 $LOG_PATH/server_stdout.log"
        ;;

    stop)
        if ! PID=$(server_pid); then
            if port_in_use; then
                echo "Port $PORT is in use, but by a process this script did not start"
                echo "(no usable pid file at $PID_FILE). Not touching it."
                exit 1
            fi
            echo "dorado-server is not running on port $PORT."
            rm -f "$PID_FILE"
            exit 0
        fi
        echo "stopping dorado-server on port $PORT (pid $PID)..."
        kill "$PID"
        for _i in $(seq 1 10); do
            is_our_server "$PID" || break
            sleep 1
        done
        if is_our_server "$PID"; then
            echo "WARNING: still alive after 10s; sending SIGKILL..."
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
        echo "stopped."
        ;;

    status)
        if PID=$(server_pid); then
            echo "dorado-server: running on port $PORT (pid $PID)"
            ps -p "$PID" -o pid=,etime=,%cpu=,%mem=,args= 2>/dev/null || true
        elif port_in_use; then
            echo "dorado-server: port $PORT is in use by a process this script did not start"
        else
            echo "dorado-server: not running on port $PORT"
        fi
        ;;

    *)
        echo "Usage: $0 [start|stop|status]"
        exit 1
        ;;
esac
