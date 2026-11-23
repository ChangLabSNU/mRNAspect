#!/bin/bash
# =============================================================================
# minimap2 alignment (unaligned BAM chunks -> sorted, indexed aligned BAM).
# =============================================================================
#
# Aligns every BAM in basecall_<prefix>/: the chunks run_basecall.sh wrote there, or, in
# bam mode, a link to the input BAM (main.py puts it there). An alignment already in a
# BAM is discarded: samtools fastq gives back the reads as basecalled, primary records
# only, reverse-strand ones in their own orientation. CPU only; no server needed.
#
# Usage:
#   bash run_align.sh <output_prefix> <reference> [output_dir] [minimap2_flags]
#
#   output_dir (3rd arg): where basecall_<prefix>/ lives and aligned_<prefix>.bam is
#     written. Default: ../results next to this script. Must match the basecall step's.
#   minimap2_flags (4th arg): minimap2 flag string. Default:
#     "-ax map-ont -k7 -w5 --secondary=no -y" (k7 w5 for short mRNA / m1Ψ constructs).
#     Keep -y: it carries the basecaller's tags (pt among them) into the aligned BAM.
#   EMIT_MOVES=1 (environment) also carries the move table (mv tag).
#   ALIGN_THREADS=N (environment) runs minimap2 with -t N (align.threads); unset,
#     minimap2 keeps its own default.
#
# Output: <output_dir>/aligned_<prefix>.bam (+ .bai), <output_dir>/minimap2_<prefix>.log
# =============================================================================

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <output_prefix> <reference_fasta> [output_dir] [minimap2_flags]"
    echo "  output_prefix:   prefix used by the basecall step (finds basecall_<prefix>/)"
    echo "  reference_fasta: reference FASTA to align against"
    echo "  output_dir:      output directory (default: ../results next to this script)"
    echo "  minimap2_flags:  minimap2 flag string (default: -ax map-ont -k7 -w5 --secondary=no -y)"
    exit 1
fi

PREFIX=$1
REF=$2

# Set by wrappers/aligner.py from the running environment; run by hand (e.g. under
# `pixi run`), the tools on PATH are used.
# Always used quoted: inside the desktop app these are absolute paths, and the app may
# sit in a folder whose name holds a space.
MINIMAP2=${MINIMAP2:-minimap2}
SAMTOOLS=${SAMTOOLS:-samtools}
# 4th arg: minimap2 flag string. Unquoted on use so it word-splits into separate flags.
ALIGN_FLAGS=${4:-"-ax map-ont -k7 -w5 --secondary=no -y"}
# Threads: added after the flags. aligner.py refuses a -t in both places.
THREAD_FLAGS=""
if [[ -n "${ALIGN_THREADS:-}" ]]; then
    THREAD_FLAGS="-t $ALIGN_THREADS"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
BASE_DIR=$(dirname "$SCRIPT_DIR")
RESULTS_DIR=${3:-"$BASE_DIR/results"}

BASECALL_OUT="$RESULTS_DIR/basecall_${PREFIX}"
ALIGNED_BAM="$RESULTS_DIR/aligned_${PREFIX}.bam"
MINIMAP2_LOG="$RESULTS_DIR/minimap2_${PREFIX}.log"

if [[ ! -f "$REF" ]]; then
    echo "ERROR: Reference FASTA not found: $REF"
    exit 1
fi

# Existence is not enough: given a file with no FASTA records (empty, a .fai, a wrong
# path), minimap2 exits 0 and maps nothing. The driver checks this too; repeated here
# because this script can be run on its own.
if [[ "$REF" == *.gz ]]; then
    # `gzip -dc`, not `zcat`: the zcat of macOS is the one for compress(1) files. It looks
    # for "<name>.Z", finds none, and the count below is then 0 for a good reference.
    REF_RECORDS=$(gzip -dc -- "$REF" | grep -c '^>' || true)
else
    REF_RECORDS=$(grep -c '^>' -- "$REF" || true)
fi
if [[ "${REF_RECORDS:-0}" -eq 0 ]]; then
    echo "ERROR: Reference FASTA has no records (no '>' header lines): $REF"
    echo "       minimap2 would accept this silently and map nothing. Check the path"
    echo "       points at a FASTA, not a .fai/.mmi/empty file."
    exit 1
fi
if [[ ! -d "$BASECALL_OUT" ]]; then
    echo "ERROR: basecall output dir not found: $BASECALL_OUT (run run_basecall.sh first)"
    exit 1
fi

# --- locate the BAM(s) ---
# dorado-server writes at most 4000 records per BAM (bam_*_0_0.bam, _0_1.bam, ...), so
# one POD5 can give several; all of them are aligned. -L follows bam mode's link.
# A read loop, not mapfile: macOS ships bash 3.2, which has no mapfile.
UBAMS=()
while IFS= read -r _ubam; do
    UBAMS+=("$_ubam")
done < <(find -L "$BASECALL_OUT" -name "*.bam" -type f | sort)
if [[ ${#UBAMS[@]} -eq 0 ]]; then
    echo "ERROR: no BAM files in $BASECALL_OUT."
    exit 1
fi
echo "=== Alignment: ${#UBAMS[@]} BAM(s) -> $ALIGNED_BAM ==="

# Tags carried through alignment: `samtools fastq -T` puts them in the FASTQ comment and
# minimap2 -y writes them back. A tag not listed here is lost.
#   er     — end reason (why the read ended).
#   pi, sp — dorado read-split parent id and split point. A split child has its own
#            read id, and its alignment ends lie inside the parent molecule.
#   mv     — the move table, only when EMIT_MOVES=1 (basecall.emit_moves). A BAM
#            basecalled elsewhere with --emit-moves carries it on every read, and carrying
#            it about doubles the align time and the BAM's size.
# A listed tag that a read does not carry costs nothing. Modification tags (MM/ML) are
# not carried: nothing here uses them.
TAGS="pt,qs,du,ns,RG,ch,rn,st,mx,dx,f5,sm,sd,sv,ts,pa,er,pi,sp"
if [[ "${EMIT_MOVES:-0}" == "1" ]]; then
    TAGS="$TAGS,mv"
fi

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

# --- publish by rename --------------------------------------------------------
# A restart trusts whatever is at $ALIGNED_BAM, so it must only appear complete and
# indexed: build under a partial- name, then rename the index first and the BAM second.
# The name is a prefix because a finished output starts with aligned_ or basecall_, so
# no input name can make a finished output look unfinished (wrappers/_shell.py).
PARTIAL_BAM="$RESULTS_DIR/partial-aligned_${PREFIX}.bam"
rm -f "$PARTIAL_BAM" "$PARTIAL_BAM".bai

# Merge every chunk BAM (same header), so every basecalled read reaches minimap2.
"$SAMTOOLS" cat "${UBAMS[@]}" | \
    "$SAMTOOLS" fastq -T "$TAGS" - | \
    "$MINIMAP2" $ALIGN_FLAGS $THREAD_FLAGS "$REF" - 2>"$MINIMAP2_LOG" | \
    "$SAMTOOLS" sort -o "$PARTIAL_BAM" -

# --- every primary read in comes out once --------------------------------------
# minimap2 -a writes every read it is given as one primary record, mapped or not, so the
# counts must match; a read lost on the way would be missing from every number without
# a word. (A record with no sequence is not lost: it comes back unmapped.)
COUNT_THREADS=""
if [[ -n "${ALIGN_THREADS:-}" ]]; then
    COUNT_THREADS="-@ $ALIGN_THREADS"
fi
IN_PRIMARY=$("$SAMTOOLS" cat "${UBAMS[@]}" | "$SAMTOOLS" view -c -F 0x900 $COUNT_THREADS -)
OUT_PRIMARY=$("$SAMTOOLS" view -c -F 0x900 $COUNT_THREADS "$PARTIAL_BAM")
echo "[Alignment] primary reads: $IN_PRIMARY in, $OUT_PRIMARY out"
if [[ "$IN_PRIMARY" -ne "$OUT_PRIMARY" ]]; then
    echo "ERROR: $IN_PRIMARY primary read(s) went into the alignment and $OUT_PRIMARY came out."
    echo "       Every read should come out once, mapped or not. Nothing was published."
    rm -f "$PARTIAL_BAM" "$PARTIAL_BAM".bai
    exit 1
fi

"$SAMTOOLS" index "$PARTIAL_BAM"

mv -f "${PARTIAL_BAM}.bai" "${ALIGNED_BAM}.bai"
mv -f "$PARTIAL_BAM" "$ALIGNED_BAM"

END_MS=$(now_ms)
ALIGN_MS=$(( END_MS - START_MS ))
echo "[Alignment] Wall time: ${ALIGN_MS}ms ($(echo "scale=1; $ALIGN_MS/1000" | bc)s)"

echo ""
echo "=== done ==="
echo "Aligned BAM: $ALIGNED_BAM"
echo "Index:       ${ALIGNED_BAM}.bai"
echo ""
"$SAMTOOLS" flagstat "$ALIGNED_BAM" | head -5
