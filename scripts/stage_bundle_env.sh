#!/usr/bin/env bash
# Build the environment the .app ships, as an artifact — not the development one.
#
# WHY THIS EXISTS. `pixi run dev-install` registers Core as an EDITABLE install: a
# finder in site-packages holding an absolute path back to this checkout. That is
# correct for development (edits are live) and fatal for a bundle, because the
# interpreter inside a shipped .app then resolves `import mrnaspect` to a directory that
# exists on exactly one computer. `scripts/check_bundle.sh` check 6 is what reports it.
#
# So the two environments are separated: `.pixi/envs/default` stays editable and is what
# you develop against, and this script produces `dist/bundle-env` — the same environment
# with Core installed for real. `tauri.conf.json` ships THAT one.
#
# Copying rather than solving a second environment is deliberate: relocation is already
# proven (scripts/check_relocation.sh, 10/10 on linux-64 and Darwin arm64), and a second
# solve would be a second set of versions to keep in step with the first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/.pixi/envs/default"
DEST="$ROOT/dist/bundle-env"

fail() { printf '\n  FAIL  %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

[ -d "$SRC" ] || fail "no environment at $SRC — run 'pixi install' first."
# A folder of that name that is not an environment. Without this line the script stopped
# a few lines below with no message at all: `ls` of the missing conda-meta failed inside a
# pipe whose errors were sent to /dev/null, and `set -e` ended the script there
# (2026-11-18, on the Mac, after .pixi had been moved away and back: see the font step).
[ -d "$SRC/conda-meta" ] && [ -x "$SRC/bin/python" ] || fail \
    "$SRC is not a whole environment (no conda-meta, or no bin/python). Look at what is in $ROOT/.pixi, or run 'pixi install'."

step "Clearing stale build artifacts"
# Not housekeeping. setuptools reuses `build/lib`, so a stale tree there is installed in
# place of the current source — silently, with no version change. A stale template was
# found in exactly this directory on 2026-10-20.
rm -rf "$ROOT/build"

# The copy is the slow part (~860 MB) and the environment changes only when pixi
# re-solves it, while CORE changes every commit. So the copy is skipped when the package
# set is unchanged, and Core is reinstalled every time regardless — that is the part that
# must never be stale. Without this the script was a ~2 minute tax on every `tauri dev`,
# which is how it ends up not being run.
STAMP="$DEST/.staged-from"
WANT="$(ls "$SRC/conda-meta" | sort | cksum)"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$WANT" ] \
   && [ -x "$DEST/bin/python3" ] && [ "${1:-}" != "--force" ]; then
    step "Reusing $DEST (same package set; pass --force to rebuild it)"
else
    step "Copying $SRC -> $DEST"
    rm -rf "$DEST"
    mkdir -p "$(dirname "$DEST")"
    cp -a "$SRC" "$DEST"
    printf '%s' "$WANT" > "$STAMP"
fi

step "Removing the editable pointer"
# The copy inherits the development install. Both the finder and its .pth have to go, or
# the real install below lands beside a shim that still wins on sys.meta_path.
# Also runs on a REUSED tree: a previous `pixi run dev-install` can have put one back.
#
# The globs name NO distribution. They used to be `__editable__*mrnaspect*` and
# `mrnaspect-*.dist-info`, and renaming the distribution to mrnaspect (2026-10-22) silently
# made both match nothing — setuptools derives those filenames from the DISTRIBUTION name,
# not the import name. The staged tree stayed clean only because `pip install .`
# uninstalls the same distribution first and takes its editable files with it. That is
# pip's behaviour doing the job this step is supposed to do, which is not a guarantee.
#
# `__editable__*` alone is safe here: a pixi environment installs everything else through
# conda, so ours is the only editable install in it. The dist-info is read from
# pyproject.toml so it cannot go stale again.
DIST="$(sed -n 's/^name = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
[ -n "$DIST" ] || fail "could not read the distribution name from pyproject.toml"
find "$DEST"/lib/python*/site-packages -maxdepth 1 \
     \( -name '__editable__*' -o -name "$DIST-*.dist-info" \) \
     -exec rm -rf {} + 2>/dev/null || true

step "Leaving out what the app must not carry"
# readline is GPL-3.0-only and comes with every solve, because conda-forge's Python depends
# on it. Only Python's interactive prompt uses it, and the app never opens one, so the copy
# that ships does without it (2026-11-13). The script below
# removes the package, its record and Python's `readline` module, and fails if a binary
# still links the library.
#
# pyphen's hyphenation dictionaries go too (L6): each is under its own licence, "GPL, LGPL
# and/or MPL", and the report never hyphenates, so WeasyPrint never opens one. pyphen
# itself stays, because WeasyPrint imports it.
#
# Before the install and the checks that follow, so each of them runs on what ships. Also
# on a REUSED tree: one staged before this step existed still holds the library.
"$DEST/bin/python" -I "$ROOT/scripts/strip_bundle_env.py" "$DEST" \
    || fail "could not leave out of $DEST what the app must not carry (the lines above say why)"

step "Making every library load from inside the environment"
# conda writes the folder an environment is installed in into some of its libraries, and a
# few of them name ANOTHER library that way: ncurses re-exports libtinfo by its full path,
# and samtools loads ncurses. The copy made above therefore keeps asking for a file in
# .pixi/envs/default, which is on this computer and on no other. The app ran here, every
# check made here passed, and its samtools would not have started on any other Mac
# (2026-11-18). The script rewrites each such reference to the copy beside it, signs the
# changed file again, reads the files back, and fails if a library is still named outside
# the environment. On Linux there is no Mach-O file and it says so.
#
# Before Core is installed and the checks below run, so each of them runs on what ships.
# Also on a REUSED tree: one staged before this step existed still holds the references.
"$DEST/bin/python" -I "$ROOT/scripts/bundle_load_paths.py" "$DEST" \
    || fail "a library in $DEST is still loaded from outside it (the lines above say which)"

step "Keeping the font cache out of the folder this environment was built in"
# fonts.conf lists where fontconfig may keep its cache, and the first place is a folder of
# the environment as it was INSTALLED, by its full path. fontconfig creates that folder
# when it is missing. So the app, run on the build machine with .pixi moved out of the way,
# made a new .pixi/envs/default holding nothing but a font cache; moving the real one back
# then put it INSIDE that folder, and the next staging found no environment (2026-11-18).
# On any other Mac the path cannot be created and fontconfig goes on to the next place,
# the user's own cache folder, which is where the cache belongs. The line is taken out, so
# every Mac uses that.
CONF="$DEST/etc/fonts/fonts.conf"
if [ -f "$CONF" ]; then
    sed -i.bak "\\|<cachedir>$SRC/|d" "$CONF" && rm -f "$CONF.bak"
    if grep -q "<cachedir>$SRC/" "$CONF"; then
        fail "$CONF still keeps its cache in $SRC"
    fi
    grep -q '<cachedir' "$CONF" || fail "$CONF names no cache folder at all any more"
    printf '  ok    fonts.conf keeps its cache in the user'"'"'s folder, not in %s\n' "$SRC"
fi

step "Writing the third parties' notices"
# What the app carries of other people's software, each with its licence text, in one file
# inside the staged tree, which About opens. It is written here, from
# this tree's own records, so it lists what ships: after the step above, and on every
# staging, because a re-solve changes the list.
#
# What cannot be found is printed as WARN and written into the file as INCOMPLETE. That
# does not stop a staging; `check_bundle.sh` stops a release on it. The window's Rust
# crates are listed only where cargo is installed, which is where the app is built.
"$DEST/bin/python" -I "$ROOT/scripts/make_notices.py" "$DEST" --root "$ROOT" \
    || fail "the third parties' notices could not be written into $DEST"

step "Installing Core for real"
# --no-deps for the same reason dev-install uses it: conda is the sole provider of
# binaries here, and letting the installer resolve would swap conda's pysam (built as a
# matched pair with its htslib) for a PyPI wheel carrying its own.
"$DEST/bin/python" -m pip install "$ROOT" \
    --no-deps --no-build-isolation --no-cache-dir --quiet

step "Recording the commit this environment is built from"
# Inside the app Core is a copy in site-packages, in no repository, so there is no git to
# ask: a result made by the app named a version and no commit, and four builds made on one
# day were all "0.1.0" (2026-11-18: a tester's result could not be traced to its build).
# What git says now is written into the installed package, where provenance.py reads it.
#
# After the install, every time, also when the environment is reused: the record is not
# one of the files pip installs, so pip would leave the last staging's in place.
"$DEST/bin/python" -I -B "$ROOT/scripts/bundle_build_record.py" "$DEST" --source "$ROOT" \
    || fail "the commit could not be recorded in $DEST (the lines above say why)"

# ---------------------------------------------------------------------------
# Verification. The point of the whole script is one property, so it is asserted
# rather than assumed.
# ---------------------------------------------------------------------------
step "Verifying"

leftover="$(find "$DEST"/lib/python*/site-packages -maxdepth 1 -name '__editable__*' | head -1)"
[ -z "$leftover" ] || fail "an editable pointer survived: $leftover"

# Resolved from / with -I, so neither the current directory nor PYTHONPATH can supply the
# answer. This is the assertion that distinguishes a bundled Core from a linked one.
resolved="$(cd / && "$DEST/bin/python" -I -c 'import mrnaspect; print(mrnaspect.__file__)')"
case "$resolved" in
  "$DEST"/*) ;;
  *) fail "import mrnaspect resolved OUTSIDE the staged env: $resolved" ;;
esac
printf '  ok    import mrnaspect -> %s\n' "$resolved"

# Package data. Without these an installed copy raises FileNotFoundError at the final
# render — after all the work, and only on a packaged install.
for rel in reporting/templates/sample_coa.html config/run_config.yaml; do
  found="$(cd / && "$DEST/bin/python" -I -c \
      "import mrnaspect,os;print(os.path.join(os.path.dirname(mrnaspect.__file__),'$rel'))")"
  [ -f "$found" ] || fail "package data missing from the install: $rel"
done
printf '  ok    package data present (templates, config)\n'

# The fixtures are 2.2 MB and an end user never runs pytest. pyproject excludes them;
# this confirms the exclusion took effect in a real install, which it cannot in an
# editable one.
if (cd / && "$DEST/bin/python" -I -c 'import mrnaspect.tests' 2>/dev/null); then
  fail "mrnaspect.tests was installed — pyproject's exclude did not take effect"
fi
printf '  ok    mrnaspect.tests excluded\n'

# What was left out stays out. The entry point is run just below on this interpreter, which
# is what shows that Core does not need the module.
if (cd / && "$DEST/bin/python" -I -c 'import readline' 2>/dev/null); then
  fail "readline still imports from the staged env"
fi
printf '  ok    readline (GPL-3.0-only) does not import\n'

# The one thing the missing dictionaries could break is the PDF. So one is written here,
# with hyphenation asked for, by the renderer the report uses. It also shows, at every
# staging, that Pango loads from the staged tree.
if ! (cd / && "$DEST/bin/python" -I -c '
import weasyprint
page = "<html lang=\"en\"><body style=\"width:3cm; hyphens:auto\"><p>Uncharacteristically long words.</p></body></html>"
assert weasyprint.HTML(string=page).write_pdf()[:4] == b"%PDF"' >/dev/null 2>&1); then
  fail "weasyprint does not write a PDF from the staged env"
fi
printf '  ok    weasyprint writes a PDF without the hyphenation dictionaries\n'

# The route the shell actually uses. `lib.rs` runs `python3 -m mrnaspect.main`, never
# the console script, and the difference is not cosmetic: its shebang is an ABSOLUTE
# path to its build-time interpreter, so bin/<name> does not survive being
# bundled to /Applications. `-m` does. Checking the console script instead would pass on
# this machine and prove nothing about the .app.
if ! (cd / && "$DEST/bin/python3" -I -m mrnaspect.main --help >/dev/null 2>&1); then
  fail "python3 -m mrnaspect.main does not run from the staged env"
fi
printf '  ok    python3 -m mrnaspect.main runs (the route the shell uses)\n'

# The record is read by the same code that stamps a result, from the staged environment:
# what a result will say is what is asked, not what the file holds. Only in a checkout;
# from a downloaded archive there is no commit and the step above has said so.
if [ -e "$ROOT/.git" ]; then
  want="$(git -C "$ROOT" rev-parse HEAD)" || fail "git rev-parse HEAD failed in $ROOT"
  said="$(cd / && "$DEST/bin/python" -I -c \
      'from mrnaspect import provenance; print(provenance.git_commit() or "")')" \
      || fail "the staged Core could not be asked for its commit"
  [ "$said" = "$want" ] || fail \
      "the staged Core reports commit '$said'; this checkout is at $want"
  printf '  ok    a result made by this environment will name commit %s\n' "$want"
fi

step "Compiling Python so that a copy needs no recompiling"
# A .pyc records the time its source was last changed, and a copy does not always keep
# file times: the bundler, a zip, a download. Stale, every module is compiled again when
# the app first runs and the new .pyc is written INSIDE the app, which the app's signature
# does not cover: `codesign` reported "file modified" on the first sealed build
# (2026-11-18). The script compiles the whole environment as bytecode that holds a hash of
# its source instead of a time, which Python loads without looking at the file's time.
#
# Last, on purpose: everything above that ran this interpreter may have written a .pyc of
# the other kind, and Core was installed just now. A second staging compiles only what
# changed. It fails if any source would still be compiled again when the app runs.
"$DEST/bin/python" -I -B "$ROOT/scripts/bundle_bytecode.py" "$DEST" \
    || fail "Python in $DEST would be compiled again inside the app (the lines above say which)"

# APPARENT size, for the reason check_relocation.sh spells out: `du -sh` reports blocks
# on disk and this checkout lives on a compressed ZFS dataset, where the same tree reads
# 573M against 823M of actual bytes. A .app carries the bytes, so the compressed figure
# would understate what the user downloads by a third.
#
# `--apparent-size` is GNU-only. It was written here without the BSD branch that
# check_relocation.sh already had ten lines of sibling script away, so on macOS -- the
# platform this script exists to build FOR -- du failed, 2>/dev/null ate the error and
# cut returned nothing: `Staged: <checkout>/dist/bundle-env ()`. Reported by
# the first real Mac run, 2026-10-23.
apparent_size() {
  if du -sh --apparent-size "$1" 2>/dev/null | cut -f1 | grep -q .; then
    du -sh --apparent-size "$1" | cut -f1
  else                     # BSD/macOS du has no --apparent-size
    find "$1" -type f -print0 | xargs -0 stat -f '%z' 2>/dev/null \
      | awk '{t+=$1} END {printf "%.0fM", t/1048576}'
  fi
}
printf '\nStaged: %s (%s)\n' "$DEST" "$(apparent_size "$DEST")"
