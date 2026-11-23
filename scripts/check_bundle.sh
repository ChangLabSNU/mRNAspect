#!/usr/bin/env bash
# Does a built .app actually carry a working interpreter?
#
# `resolve_python` looks for `Contents/Resources/env/bin/python3` and `bundle.resources`
# is what puts it there. Neither half can be verified from Linux -- the bundler runs on
# the Mac -- so this is the check to run on the first build, before concluding anything.
#
#     bash scripts/check_bundle.sh "gui/src-tauri/target/release/bundle/macos/mRNAspect.app"
#     bash scripts/check_bundle.sh <the .app> <commit>     a release: built from that commit
#
# The thing most likely to be wrong is SYMLINKS. The environment has 1,584 of them and
# `bin/python3` is one (-> python3.14), which is precisely the path the shell looks for.
# If the bundler dereferences them the app still works but the bundle is larger than it
# should be; if it drops them, the interpreter is simply not there.
set -u

APP="${1:?usage: check_bundle.sh <path to .app> [<commit the release is of>]}"
# For a release: check 12 then asks that the app was built from exactly this commit, from
# an unmodified tree, whatever this checkout is at.
EXPECT="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Without this a wrong path ran every check against nothing and ended "0 passed,
# 4 failed", which reads as a broken bundle (2026-10-29: the path had lost bundle/).
if [ ! -d "$APP/Contents" ]; then
    printf 'not an .app bundle: %s\n' "$APP" >&2
    printf 'tauri build puts it in gui/src-tauri/target/release/bundle/macos/\n' >&2
    exit 2
fi
# A full path from here on. Checks 5 and 6 compare it with where the bundle's Python says
# it is, and Python answers in full: typed as `src-tauri/target/...` from gui/, a whole
# bundle failed both (2026-11-14, the Mac).
APP="$(cd "$APP" && pwd -P)"
RES="$APP/Contents/Resources/env"
PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }

printf '\n\033[1m== 1. the environment is in the bundle\033[0m\n'
[ -d "$RES" ] && ok "$RES" || bad "no env at $RES"

printf '\n\033[1m== 2. the interpreter is where resolve_python looks\033[0m\n'
PY="$RES/bin/python3"
if [ -f "$PY" ]; then
    if [ -L "$PY" ]; then ok "bin/python3 (symlink preserved -> $(readlink "$PY"))"
    else ok "bin/python3 (a real file: the bundler dereferenced it, which still works)"; fi
else
    bad "bin/python3 missing -- the shell will not find Core"
fi

printf '\n\033[1m== 3. symlinks across the whole env\033[0m\n'
N=$(find "$RES" -type l 2>/dev/null | wc -l | tr -d ' ')
printf '  %s symlinks (the source tree has 1,584)\n' "$N"
[ "$N" -gt 0 ] && ok "symlinks preserved" \
               || printf '  \033[33mNOTE\033[0m  all dereferenced; check the size below\n'

printf '\n\033[1m== 4. size\033[0m\n'
du -sh "$APP" 2>/dev/null | sed 's/^/  on disk:   /'
printf '  source env apparent: 420M on osx-arm64\n'

printf '\n\033[1m== 5. it runs, from inside the bundle, with nothing inherited\033[0m\n'
# -B on every run of the bundled Python below: never write bytecode. The first sealed
# build was checked without it, and the check itself rewrote .pyc files inside the app and
# so broke the seal it went on to test (2026-11-18). A check leaves the app as it found it.
if [ -f "$PY" ]; then
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -B -c 'import sys; print(sys.prefix)' 2>&1)
    printf '  sys.prefix = %s\n' "$out"
    case "$out" in "$RES"*) ok "sys.prefix is the bundled env";;
                   *) bad "sys.prefix is not the bundle: $out";; esac
    # Captured, not piped: the status of `... | tail -3` is tail's, so a stack that
    # failed to import used to PASS.
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -B -c '
import importlib, sys
for m in ("numpy", "pysam", "weasyprint", "matplotlib"):
    importlib.import_module(m)
print("  all native stacks import")' 2>&1)
    check=$?
    printf '%s\n' "$out" | tail -3
    [ $check = 0 ] && ok "native stacks import from the bundle" \
                   || bad "a native stack failed to import"
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 6. Core resolves INSIDE the bundle\033[0m\n'
printf '  The defect this catches: `pixi run dev-install` registers Core as an EDITABLE\n'
printf '  install, so an env copied straight from .pixi resolves `import mrnaspect` to the\n'
printf '  build machine. scripts/stage_bundle_env.sh is what prevents it; this is where\n'
printf '  the prevention is confirmed on the artifact.\n'
if [ -f "$PY" ]; then
    WHERE=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B \
            -c 'import mrnaspect; print(mrnaspect.__file__)' 2>&1 | tail -1)
    case "$WHERE" in
        "$APP"/*) ok "import mrnaspect -> $WHERE" ;;
        *)        bad "import mrnaspect resolves OUTSIDE the app: $WHERE" ;;
    esac
    if env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B \
           -c 'import mrnaspect.reporting.reporter' >/dev/null 2>&1; then
        ok "the render stack imports from the bundle"
    else
        bad "mrnaspect.reporting.reporter does not import inside the bundle"
    fi
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 7. what the app must not carry is not in the bundle\033[0m\n'
printf '  Staging leaves out readline (GPL-3.0-only), the hyphenation dictionaries of\n'
printf '  pyphen, and pypdf, which only the tests use. Asked of the artifact: their files,\n'
printf '  the records, Python'"'"'s readline module, every binary that could still link the\n'
printf '  library, and that pyphen still imports.\n'
if [ -f "$PY" ]; then
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/strip_bundle_env.py" --check "$RES" 2>&1)
    check=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    [ $check = 0 ] && ok "what staging leaves out is not in the bundle, and nothing links it" \
                   || bad "something staging leaves out is still in the bundle"
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 8. the third parties'"'"' notices are in the bundle, and whole\033[0m\n'
printf '  Staging writes THIRD-PARTY-NOTICES.txt from what it stages.\n'
printf '  Asked of the artifact: the file is there, it lists every package the bundle\n'
printf '  records, and nothing in it is marked INCOMPLETE (a licence text not found, or the\n'
printf '  Rust crates or the page left out).\n'
if [ -f "$PY" ]; then
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/make_notices.py" --check "$RES" 2>&1)
    check=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    [ $check = 0 ] && ok "the notices list everything the bundle carries" \
                   || bad "the third parties' notices are missing or incomplete"
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 9. every library is named inside the app, or is the system'"'"'s\033[0m\n'
printf '  The defect this catches: conda writes the folder an environment was installed in\n'
printf '  into some libraries, and a few name ANOTHER library by that full path. On the\n'
printf '  computer that built the app the folder exists, so checks 5 and 6 pass and the app\n'
printf '  runs; on any other Mac the library is not found. Read from the files themselves:\n'
printf '  every Mach-O in the app, every library it names.\n'
if [ -f "$PY" ]; then
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/bundle_load_paths.py" --check "$APP" 2>&1)
    check=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    [ $check = 0 ] && ok "no library is named by a path outside the app" \
                   || bad "a library is named by a path outside the app"
else
    bad "skipped: no interpreter"
fi

# Checks 10 and 12 ask macOS itself, so they are for the Mac the app was built on.
SYSTEM_NAME="$(uname -s)"

printf '\n\033[1m== 10. what a run loads comes from the app or the system\033[0m\n'
printf '  Check 9 reads what the files say. This one runs samtools the six ways Core runs\n'
printf '  it, minimap2, and the report'"'"'s native stack (a PDF is written), and has macOS\n'
printf '  list every library each loaded, which also covers the ones opened by name while\n'
printf '  running. Not `samtools --version`: that lists htslib'"'"'s plugins for remote files,\n'
printf '  looked for in a folder compiled into the library. No command Core runs opens them.\n'
if [ "$SYSTEM_NAME" != Darwin ]; then
    printf '  \033[33mNOTE\033[0m  not macOS: only dyld can answer this\n'
elif [ -f "$PY" ]; then
    check=0
    WORK="$(mktemp -d "${TMPDIR%/}/mrnaspect-check.XXXXXX")"
    # One read on a 60 nt reference: enough for every command to do its work.
    printf '@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:ref\tLN:60\nr1\t0\tref\t1\t60\t20M\t*\t0\t0\tACGTACGTACGTACGTACGT\tIIIIIIIIIIIIIIIIIIII\n' \
        > "$WORK/in.sam"
    loaded() {
        out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/bundle_load_paths.py" \
              --loaded "$APP" -- "$@" 2>&1) || check=1
        printf '%s\n' "$out" | sed 's/^/  /'
    }
    # The commands of wrappers/run_align.sh, on files instead of pipes.
    loaded "$RES/bin/samtools" sort -o "$WORK/sorted.bam" "$WORK/in.sam"
    loaded "$RES/bin/samtools" index "$WORK/sorted.bam"
    loaded "$RES/bin/samtools" view -c -F 0x900 "$WORK/sorted.bam"
    loaded "$RES/bin/samtools" fastq -T '*' "$WORK/sorted.bam"
    loaded "$RES/bin/samtools" cat -o "$WORK/cat.bam" "$WORK/sorted.bam"
    loaded "$RES/bin/samtools" flagstat "$WORK/sorted.bam"
    loaded "$RES/bin/minimap2" --version
    loaded "$PY" -I -B -c '
import numpy, pysam, matplotlib, weasyprint
assert weasyprint.HTML(string="<p>Loaded.</p>").write_pdf()[:4] == b"%PDF"'
    rm -rf "$WORK"
    [ $check = 0 ] && ok "nothing was loaded from outside the app" \
                   || bad "a run loaded a library from outside the app, or did not run"
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 11. Python needs no recompiling, and writes nothing into the app\033[0m\n'
printf '  A .pyc that records the time of its source goes stale when a copy does not keep\n'
printf '  file times, and Python then compiles the module again and writes the new .pyc into\n'
printf '  the app, under a signature that covers every file. Asked of the artifact: every\n'
printf '  source has bytecode holding a hash of that source, which Python loads as it is.\n'
if [ -f "$PY" ]; then
    out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/bundle_bytecode.py" --check "$RES" 2>&1)
    check=$?
    printf '%s\n' "$out" | sed 's/^/  /'
    [ $check = 0 ] && ok "no Python source would be compiled again when the app runs" \
                   || bad "Python would be compiled again, and rewritten, inside the app"
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 12. a result made by the app names the commit it was built from\033[0m\n'
printf '  Inside the app Core is a copy, in no repository, so git has no answer for it.\n'
printf '  Staging writes the commit into the installed package (scripts/bundle_build_record.py)\n'
printf '  and Core reads it there. Asked twice: the record is there and is one; and Core,\n'
printf '  run from the app, reports that commit, which is what a result will carry. Without\n'
printf '  it a result names a version and no build, and many builds share one version.\n'
printf '  The commit must also be the one this checkout is at, or for a release the one\n'
printf '  given as the second argument: an app of another commit is not this source'"'"'s.\n'
if [ -f "$PY" ]; then
    if [ -n "$EXPECT" ]; then
        out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/bundle_build_record.py" \
              --check "$RES" --source "$HERE/.." --expect "$EXPECT" 2>&1)
        check=$?
    else
        out=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B "$HERE/bundle_build_record.py" \
              --check "$RES" --source "$HERE/.." 2>&1)
        check=$?
    fi
    printf '%s\n' "$out" | sed 's/^/  /'
    recorded=$(printf '%s\n' "$out" | sed -n 's/.*built from \([0-9a-f]\{40\}\) (.*/\1/p' | head -1)
    if [ $check != 0 ]; then
        bad "the app has no usable record of the commit it was built from, or it is of another commit"
    elif [ -z "$recorded" ]; then
        printf '  \033[33mNOTE\033[0m  no commit was there to record: results from this app name none\n'
    else
        said=$(env -i PATH=/usr/bin:/bin HOME="$TMPDIR" "$PY" -I -B -c \
               'from mrnaspect import provenance; print(provenance.git_commit() or "")' 2>&1)
        [ "$said" = "$recorded" ] && ok "a result made by this app will name commit $recorded" \
            || bad "Core in the app reports '$said', not the recorded $recorded"
    fi
else
    bad "skipped: no interpreter"
fi

printf '\n\033[1m== 13. the app is sealed, after everything above ran in it\033[0m\n'
printf '  Every file in the app is signed ad hoc on its own, but macOS judges a downloaded\n'
printf '  app by the signature of the WHOLE bundle (Contents/_CodeSignature). Without it a\n'
printf '  copy that arrives through a browser is refused as damaged; with it, macOS offers\n'
printf '  Open Anyway in System Settings. tauri.conf.json asks the bundler for it\n'
printf '  (bundle.macOS.signingIdentity "-"). Last, so that it also shows no check above\n'
printf '  changed a file of the app.\n'
if [ "$SYSTEM_NAME" != Darwin ]; then
    printf '  \033[33mNOTE\033[0m  not macOS: codesign is what answers this\n'
elif [ ! -d "$APP/Contents/_CodeSignature" ]; then
    bad "no Contents/_CodeSignature: the bundle is not signed as a whole"
else
    out=$(codesign --verify --deep --strict --verbose=2 "$APP" 2>&1)
    check=$?
    printf '%s\n' "$out" | tail -5 | sed 's/^/  /'
    [ $check = 0 ] && ok "the bundle's signature covers what is in it" \
                   || bad "codesign does not accept the bundle's signature"
fi

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
exit $([ "$FAIL" = 0 ]; echo $?)
