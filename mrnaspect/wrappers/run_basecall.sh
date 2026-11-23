#!/bin/bash
# =============================================================================
# dorado-server basecalling (POD5 -> unaligned BAM chunks). No alignment.
# =============================================================================
#
# Needs no reference; run_align.sh aligns the output.
#
# Usage:
#   bash run_basecall.sh <pod5_file> <output_prefix> [output_dir] [model]
#
#   output_dir (3rd arg): where basecall_<prefix>/ is written. Default: ../results next to
#     this script. The offline driver passes its pod5->BAM cache dir.
#   model (4th arg, required): dorado-server --model, an absolute path to a model folder
#     (a stock model or an exported fine-tune); being absolute, it needs no -d. See
#     MODEL below for which models the server can load.
#
# Prerequisite: a dorado-server listening on $DORADO_PORT (default 5555); see
# start_server.sh.
# EMIT_MOVES=1 also writes the move table (mv tag), for signal-level tools; nothing in
# mRNAspect reads it. Off unless set (basecall.emit_moves).
# Output: <output_dir>/basecall_<prefix>/  (unaligned BAM chunk(s) + logs)
# =============================================================================

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <pod5_file> <output_prefix> [output_dir] [model]"
    echo "  pod5_file:     input POD5 file path"
    echo "  output_prefix: output file prefix (e.g. sample1)"
    echo "  output_dir:    output directory (default: ../results next to this script)"
    echo "  model:         dorado-server --model — absolute model folder path (stock or fine-tuned)"
    exit 1
fi

POD5_FILE=$1
PREFIX=$2

# Set by wrappers/basecaller.py; run by hand, ont_basecall_client on PATH is used. The
# client must be the running server's version (7.13.6) for `--model <folder>` to work.
CLIENT=${DORADO_CLIENT:-ont_basecall_client}
# 4th arg: absolute path to a model folder (a stock dorado model, or an exported
# fine-tune). No default: models live wherever the user downloaded them. The pipeline is
# specified for rna004_130bps_hac@v5.3.0; the @v6.0.0 models crash the server.
MODEL=${4:-}
# The port the driver's server bound (wrappers/dorado_server.py); 5555 may already be
# taken by another server on a shared host.
PORT=${DORADO_PORT:-5555}
# Left unquoted on use, so an empty value adds no argument.
MOVES_FLAG=""
if [[ "${EMIT_MOVES:-0}" == "1" ]]; then
    MOVES_FLAG="--emit_moves"
fi

# Output directory: 3rd arg if given, else ../results relative to this script.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
BASE_DIR=$(dirname "$SCRIPT_DIR")
RESULTS_DIR=${3:-"$BASE_DIR/results"}
mkdir -p "$RESULTS_DIR"

BASECALL_OUT="$RESULTS_DIR/basecall_${PREFIX}"

# --- input validation ---
if [[ -z "$MODEL" ]]; then
    echo "ERROR: no model given (4th argument)."
    echo "       Pass the absolute path to a dorado model folder, e.g. your copy of"
    echo "       rna004_130bps_hac@v5.3.0, or set basecall.model in the config."
    exit 1
fi
if [[ ! -f "$POD5_FILE" ]]; then
    echo "ERROR: POD5 file not found: $POD5_FILE"
    exit 1
fi

# Check that a server listens on the port this client will use (pgrep would also match
# other users' servers on a shared host). /dev/tcp is a bash builtin.
if ! (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then
    echo "ERROR: no dorado-server listening on 127.0.0.1:$PORT."
    echo "       The driver normally starts one (basecall.server.auto_start);"
    echo "       to start one by hand: bash start_server.sh"
    exit 1
fi

echo "=== Basecalling: $POD5_FILE -> $BASECALL_OUT ==="

# ont_basecall_client only accepts a directory as input -> build a symlink directory.
POD5_DIR=$(mktemp -d /tmp/pod5_input_XXXXXX)
ln -sf "$(realpath "$POD5_FILE")" "$POD5_DIR/"

# --- publish by rename --------------------------------------------------------
# run_align.sh merges every BAM in $BASECALL_OUT, so chunks left there by a killed run
# would come back as duplicate reads. Chunks go to a partial- sibling, renamed into
# place only once validated (same directory, so the rename does not cross filesystems).
# The name is a prefix because a suffix collides: x.partial.pod5 basecalls into
# basecall_x.partial, which a suffix would also make x.pod5's unfinished folder.
PARTIAL_OUT="$RESULTS_DIR/partial-basecall_${PREFIX}"
rm -rf "$PARTIAL_OUT"
mkdir -p "$PARTIAL_OUT"

# Milliseconds since the epoch. BSD date (macOS) prints %N literally, so there the
# value is whole seconds x 1000; it is only logged.
now_ms() {
  _t=$(date +%s%N)
  case "$_t" in
    *N) echo $(( ${_t%N} * 1000 )) ;;
    *)  echo $(( _t / 1000000 )) ;;
  esac
}

START_MS=$(now_ms)

"$CLIENT" \
    -i "$POD5_DIR" \
    -s "$PARTIAL_OUT" \
    --port $PORT --use_tcp \
    --model "$MODEL" \
    --estimate_poly_a \
    --output_mode bam \
    --disable_qscore_filtering \
    $MOVES_FLAG

END_MS=$(now_ms)
BASECALL_MS=$(( END_MS - START_MS ))
echo "[Basecalling] Wall time: ${BASECALL_MS}ms ($(echo "scale=1; $BASECALL_MS/1000" | bc)s)"

rm -rf "$POD5_DIR"

# Publish only an output that holds a BAM: a restart reuses whatever is published.
if [[ -z "$(find "$PARTIAL_OUT" -name '*.bam' -type f -print -quit)" ]]; then
    echo "ERROR: no BAM files in the basecalling output ($PARTIAL_OUT)."
    exit 1
fi

# `mv partial-A A` nests when A exists, so the old directory goes first. In between, the
# path is absent, which a restart treats as a cache miss.
rm -rf "$BASECALL_OUT"
mv "$PARTIAL_OUT" "$BASECALL_OUT"
echo "=== done: $BASECALL_OUT ==="
