#!/usr/bin/env bash
# Does the pixi environment still work after it is MOVED?
#
# This is the question a shipped .app turns on. The Tauri shell starts
# `<bundle>/Contents/Resources/env/bin/python -m mrnaspect.main`, so the environment that
# `pixi install` built in `.pixi/envs/default` has to run from a path it has never seen.
# A conda environment bakes its build prefix into ~60 `bin/` scripts and into `sysconfig`,
# which is why this needs measuring rather than assuming.
#
# Measured on linux-64 (2026-10-15). macOS is the platform that actually ships, and
# conda-forge relocates it differently there (`@loader_path` RPATHs instead of `$ORIGIN`,
# plus ad-hoc code signatures on every dylib on Apple Silicon), so the Linux result does
# not transfer. Run this there.
#
#     bash scripts/check_relocation.sh
#
# What a copy on THIS computer cannot show by itself: the original is still here. conda
# names a few libraries by the folder the environment was installed in, the copy keeps
# those names, and they go on resolving to the original. A copy that passed everything
# below would then run on this computer and on no other (found on the built app,
# 2026-11-18). So step 0b applies the rewrite staging applies
# (scripts/bundle_load_paths.py) and fails if a library is still named outside the copy.
#
# What it does NOT test: that Core itself is bundled. `pixi run dev-install` puts a .pth
# in the environment pointing back at this checkout, and the copy inherits it, so
# `import mrnaspect` still resolves through the original source tree. That is correct for this
# test -- the question is whether numpy/pysam/WeasyPrint's NATIVE stacks survive the move.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/.pixi/envs/default"
# macOS sets TMPDIR with a trailing slash (/var/folders/.../T/), so this used to
# build "T//mrnaspect-relocated-NNN". Python normalises that away, which made every
# path comparison below disagree with itself and reported a clean relocation on
# macOS as the packaging blocker.
_TMP="${TMPDIR:-/tmp}"
DEST="${_TMP%/}/mrnaspect-relocated-$$"
PASS=0; FAIL=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if [ "$1" = 0 ]; then ok "$2"; else bad "$2"; fi; }

trap 'rm -rf "$DEST"' EXIT

[ -d "$SRC" ] || { echo "no environment at $SRC -- run 'pixi install' first"; exit 2; }

say "0. copy the environment to a path it has never seen"
echo "  $SRC"
echo "    -> $DEST"
cp -a "$SRC" "$DEST" || { echo "copy failed"; exit 2; }
# APPARENT size. `du -sh` reports blocks on disk, and this checkout lives on a
# compressed ZFS dataset: the same tree is 573M there, 496M under /tmp and 823M of
# actual bytes. A .app bundle carries the bytes.
if du -sh --apparent-size "$DEST" >/dev/null 2>&1; then
    du -sh --apparent-size "$DEST" | sed 's/^/  size (apparent): /'
else                       # BSD/macOS du has no --apparent-size
    printf '  size (apparent): %s\n' \
        "$(find "$DEST" -type f -print0 | xargs -0 stat -f '%z' 2>/dev/null \
           | awk '{t+=$1} END {printf "%.0fM", t/1048576}')"
fi

PY="$DEST/bin/python"
[ -x "$PY" ] || { echo "no interpreter at $PY"; exit 2; }

# env -i: nothing inherited. PATH is the two system dirs and nothing else, so anything
# found is found under sys.prefix rather than because this shell could already see it.
run() { env -i PATH=/usr/bin:/bin HOME="$DEST/fakehome" "$@"; }
mkdir -p "$DEST/fakehome"

say "0b. no library in the copy is named by a path outside it"
# What staging does to the environment the app ships, done to this copy: each library
# named by the original's folder is renamed to the copy beside it and the changed file is
# signed again. On Linux there is no Mach-O file and the script says so.
run "$PY" -I "$REPO/scripts/bundle_load_paths.py" "$DEST"
check $? "every library is named inside the copy, or is the system's"

say "1. the interpreter runs, and believes it lives in the copy"
# Compared with realpath rather than as strings: on macOS /var is a symlink to
# /private/var, so the same directory has two correct spellings and `=` is the wrong
# tool for the question being asked.
run "$PY" - "$DEST" <<'PY'
import os, sys
want, got = (os.path.realpath(p) for p in (sys.argv[1], sys.prefix))
print(f"  sys.prefix = {sys.prefix}")
if want != got:
    print(f"  resolved:   {got}\n  expected:   {want}")
sys.exit(0 if want == got else 1)
PY
check $? "sys.prefix is the copy"

say "2. every native stack loads FROM THE COPY, not from the original"
run "$PY" - "$DEST" "$SRC" <<'PY'
import importlib, sys
import os.path
dest, src = (os.path.realpath(p) for p in sys.argv[1:3])
real = os.path.realpath
bad = 0
for mod in ("numpy", "pysam", "yaml", "jinja2", "weasyprint", "matplotlib", "mrnaspect"):
    try:
        m = importlib.import_module(mod)
    except Exception as exc:
        print(f"  FAIL  {mod}: {type(exc).__name__}: {exc}"); bad += 1; continue
    where = real(getattr(m, "__file__", "") or "")
    if mod == "mrnaspect":
        # Expected to come from the CHECKOUT: dev-install is an editable .pth.
        print(f"  note  mrnaspect loads from the checkout (editable install): {where}")
        continue
    if where.startswith(src):
        print(f"  FAIL  {mod} loaded from the ORIGINAL env: {where}"); bad += 1
    elif where.startswith(dest):
        print(f"  PASS  {mod}")
    else:
        print(f"  FAIL  {mod} loaded from neither copy nor original: {where}"); bad += 1
# A C extension is the real test: pure Python would relocate trivially. `numpy._core`,
# not `numpy.core` -- the latter is a deprecated pure-Python shim and resolving it
# printed a .py, which proved nothing about a native extension.
import numpy._core._multiarray_umath as mu
import pysam.libcalignmentfile as la
for label, m in (("numpy", mu), ("pysam/htslib", la)):
    where = real(m.__file__)
    if not where.endswith((".so", ".dylib", ".pyd")):
        print(f"  FAIL  {label} resolved a shim, not a native extension: {where}")
        bad += 1
    else:
        print(f"  PASS  {label} C extension: {where.replace(dest, '<copy>')}")
sys.exit(1 if bad else 0)
PY
check $? "imports resolve from the copy"

say "3. the external binaries in the copy execute"
for tool in minimap2 samtools; do
    v=$(run "$DEST/bin/$tool" --version 2>&1 | head -1)
    if [ -n "$v" ]; then ok "$tool: $v"; else bad "$tool did not run"; fi
done

say "4. WeasyPrint's native stack (the one most likely to break on a move)"
run "$PY" -c '
import weasyprint, weasyprint.text.ffi as ffi
print("  pango/harfbuzz loaded:", ffi.pango is not None)
' 2>&1 | tail -3
check $? "WeasyPrint binds its native libraries"

say "5. the whole pipeline, from the copy"
# One BAM, in a folder of its own: the fixture folder holds BAMs called by two basecaller
# models, and Core takes one model per run.
IN="$DEST/in"
mkdir -p "$IN"
cp "$REPO/mrnaspect/tests/data/small_aligned.bam" "$IN/" \
    || { echo "the fixture mrnaspect/tests/data/small_aligned.bam is missing"; exit 2; }
OUT="$DEST/run"
run "$PY" -m mrnaspect.main \
    --input-dir "$IN" \
    --output-dir "$OUT" --output-name reloc \
    --reference "$REPO/mrnaspect/tests/data/curlcake_ref.fasta" \
    --mode bam --min-reads 1 \
    > "$DEST/run.log" 2>&1
rc=$?
check $rc "exit code 0 (see $DEST/run.log)"
[ $rc = 0 ] || tail -20 "$DEST/run.log" | sed 's/^/    /'

say "6. it produced a real report, not an empty one"
R="$OUT/reloc"
for f in qc_result.json report_run_end.html; do
    if [ -s "$R/$f" ]; then ok "$f ($(wc -c < "$R/$f" | tr -d ' ') bytes)"
    else bad "$f missing or empty"; fi
done
pdf=$(ls "$R"/*.pdf 2>/dev/null | head -1)
if [ -n "$pdf" ] && [ -s "$pdf" ]; then ok "PDF ($(wc -c < "$pdf" | tr -d ' ') bytes)"
else bad "no PDF"; fi

n=$(run "$PY" -c "
import json,sys
print(json.load(open(sys.argv[1]))['report']['mapped_reads'])" "$R/qc_result.json" 2>/dev/null)
[ "$n" = "166" ] && ok "mapped_reads = 166 (same as on linux-64)" \
                 || bad "mapped_reads = ${n:-?}, expected 166"

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ] && echo "The environment relocates on $(uname -sm)." \
                || echo "Relocation is NOT clean on $(uname -sm) -- this is the packaging blocker."
exit $([ "$FAIL" = 0 ]; echo $?)
