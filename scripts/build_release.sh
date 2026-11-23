#!/usr/bin/env bash
# Build the desktop app of one release tag, from nothing, somewhere that is not a
# developer's checkout; then check the archive as the person who downloads it gets it.
#
#     bash scripts/build_release.sh v0.1.0
#     bash scripts/build_release.sh v0.1.0 /path/to/a/clone       (another source of the tag)
#
# On macOS, Apple Silicon, with pixi, git, Node (npm), Rust (cargo) and the Xcode command
# line tools. It uploads nothing and moves no tag: it ends with the archive, its sha256 and
# what is left for a person to do.
#
# WHY THIS EXISTS. The first release was built in a developer's checkout, under that
# account's home folder, from a commit of the unpublished development repository:
#
# * about 4,000 files of the app named the home folder: compiled Python, the files conda
#   writes its install folder into, conda's own records, and the shell's binary;
# * the app's results named a commit no reader of the public repository can look up;
# * the staged environment was reused, so a file left by an earlier build could ship.
#
# So a release is built by this script and no other way:
#
# 1. A NEW clone of the tag. The commit in the app's results is then the tag's, and
#    nothing that is not in the tag can be in the build.
# 2. In a build folder that is not under anyone's home (BUILD below), holding the clone,
#    the environment, every cache, and Rust's registry. The paths conda and Rust write
#    into what they build then name that folder and no account.
# 3. The environment is kept OUTSIDE the clone (pixi's detached environments), so the
#    files conda writes its install folder into do not name the checkout either.
# 4. Everything is made new: no staged environment, no node_modules, no target folder.
# 5. The archive is unpacked and read by scripts/check_release.py: no file names the home
#    folder or the checkout; the build record is the tag's commit; Core is the tag's, byte
#    for byte; the package versions; the notices; the bundle checks; a run of the fixture.
#
# WHERE THE BUILD FOLDER MAY BE. Its path ends up inside the app: several libraries keep
# the folder they were installed in as a default place to look for configuration and for
# modules to load (openssl, glib, fontconfig). On the computer that runs the app that
# folder must not be one an ordinary user can create, or whatever they put there the app
# could load. So the build folder's parent must be writable only by an administrator
# (/opt is; /tmp and /Users/Shared are not), and this script refuses one that is not.
# Make it once:
#
#     sudo mkdir -p /opt/mrnaspect-build && sudo chown "$USER" /opt/mrnaspect-build
set -euo pipefail

TAG="${1:?usage: build_release.sh <tag> [<repository>]}"
REPO="${2:-https://github.com/ChangLabSNU/mRNAspect.git}"
BUILD="${MRNASPECT_BUILD_ROOT:-/opt/mrnaspect-build}"

# What the archive is held to, besides being the tag. Each line is a published advisory
# against the version the first release carried; raise them as new ones appear.
MIN_VERSIONS=(weasyprint=70.0 pillow=13.0.0)
ABSENT=(pypdf)          # the tests read PDFs with it; the app does not

fail() { printf '\n  FAIL  %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

for tool in git pixi npm cargo; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is not on PATH"
done

# --- the build folder -----------------------------------------------------------------
[ -d "$BUILD" ] || fail "no build folder at $BUILD. Make it once:
        sudo mkdir -p $BUILD && sudo chown \"\$USER\" $BUILD"
BUILD="$(cd "$BUILD" && pwd -P)"
[ -w "$BUILD" ] || fail "$BUILD is not writable by this account"
case "$BUILD" in
    *[[:space:]]*) fail "$BUILD has a space in it: the compiler's flags below are split on spaces" ;;
esac
case "$BUILD/" in
    "$(cd "$HOME" && pwd -P)"/*) fail "$BUILD is under this account's home folder: the app would name it" ;;
esac
PARENT="$(dirname "$BUILD")"
# The mode as `ls -ld` prints it, which is the same on macOS and Linux (`stat` is not):
# ten characters, the ninth being "w" when any user may write there.
MODE="$(ls -ld "$PARENT" | cut -c1-10)"
case "$MODE" in
    ????????w?) fail "$PARENT can be written by any user ($MODE), so on another computer
        anyone could create $BUILD and the app would look there for configuration and
        modules. Use a folder only an administrator can create, such as /opt/mrnaspect-build." ;;
esac
if [ -n "$(ls -A "$BUILD")" ]; then
    fail "$BUILD is not empty. A release starts from nothing, so that no file of an earlier
        build can reach it. Look at what is there, then empty it yourself:
        rm -rf $BUILD/src $BUILD/env $BUILD/cache $BUILD/cargo $BUILD/out"
fi

SRC="$BUILD/src"
export PIXI_CACHE_DIR="$BUILD/cache/pixi"
export RATTLER_CACHE_DIR="$BUILD/cache/pixi"
export npm_config_cache="$BUILD/cache/npm"
export CARGO_HOME="$BUILD/cargo"
mkdir -p "$BUILD/env" "$BUILD/cache" "$BUILD/cargo" "$BUILD/out"
# What the Rust compiler writes into the shell's binary for a panic's location: the
# checkout and the registry, as short names. The home folder last, for anything else of
# this account's (the toolchain). Building here already keeps the home folder out; this
# keeps the build folder out of the binary too.
export RUSTFLAGS="--remap-path-prefix=$SRC=mrnaspect --remap-path-prefix=$CARGO_HOME=cargo --remap-path-prefix=$HOME=home ${RUSTFLAGS:-}"

# --- 1. the tag, cloned new -------------------------------------------------------------
step "Cloning $TAG from $REPO"
git clone --quiet --depth 1 --branch "$TAG" "$REPO" "$SRC" \
    || fail "could not clone $TAG from $REPO"
COMMIT="$(git -C "$SRC" rev-parse HEAD)"
[ "$(git -C "$SRC" rev-parse "$TAG^{commit}")" = "$COMMIT" ] \
    || fail "$TAG is not what was checked out (a branch of that name?)"
[ -z "$(git -C "$SRC" status --porcelain)" ] || fail "the new clone is not clean"
printf '  %s is commit %s\n' "$TAG" "$COMMIT"

# --- 2. the environment, outside the clone ----------------------------------------------
step "Installing the environment the lock file names, outside the clone"
cd "$SRC"
pixi config set --local detached-environments "$BUILD/env" >/dev/null
pixi install --frozen || fail "pixi could not install the locked environment"
ENV_REAL="$(cd "$SRC/.pixi/envs/default" && pwd -P)" \
    || fail "there is no environment at $SRC/.pixi/envs/default"
case "$ENV_REAL/" in
    "$BUILD/env"/*) printf '  environment: %s\n' "$ENV_REAL" ;;
    *) fail "the environment is at $ENV_REAL, not under $BUILD/env: this pixi did not keep
        it outside the clone, and the app's files would name the checkout" ;;
esac
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ] \
    || fail "installing the environment changed a tracked file (the lock file?)"

# --- 3. the app -------------------------------------------------------------------------
step "Building the page and the app (staging runs first, through npm's prebuild)"
cd "$SRC/gui"
# Each of the three lock files is what gets built, or the build stops: pixi above
# (--frozen), npm here (`npm ci` installs the lock and nothing else), and cargo, which
# would otherwise write a new lock without a word when the one in the tag does not fit.
npm ci || fail "npm ci failed"
cargo metadata --locked --format-version 1 --manifest-path "$SRC/gui/src-tauri/Cargo.toml" \
    >/dev/null || fail "gui/src-tauri/Cargo.lock does not fit Cargo.toml: update it in the
        development checkout (cargo update), commit, and tag again"

step "Asking again whether what the lock files resolve to has a published advisory"
# The versions were chosen against the advisories known when the lock files were written.
# A release asks again, of what is resolved now: npm of its own registry, development
# packages included (the build tools run on this machine); cargo-audit of the Rust lock,
# where it is installed (`cargo install cargo-audit`). One found here stops the build:
# update the lock in the development checkout, commit, and tag again.
npm audit --include=dev || fail "npm audit reports an advisory for a package in gui/package-lock.json"
printf '  ok    npm audit: nothing reported for gui/package-lock.json\n'
if cargo audit --version >/dev/null 2>&1; then
    (cd "$SRC/gui/src-tauri" && cargo audit) \
        || fail "cargo audit reports an advisory for a crate in gui/src-tauri/Cargo.lock"
    printf '  ok    cargo audit: nothing reported for gui/src-tauri/Cargo.lock\n'
else
    printf '  NOTE  cargo-audit is not installed: the Rust lock was not asked. What it resolves\n'
    printf '        for this Mac, for the record:\n'
    (cd "$SRC/gui/src-tauri" && cargo tree --locked --target aarch64-apple-darwin -i tokio --depth 0 2>/dev/null) \
        | sed 's/^/        /' || true
fi
npm run tauri build -- --bundles app || fail "the app did not build"
APP="$SRC/gui/src-tauri/target/release/bundle/macos/mRNAspect.app"
[ -d "$APP/Contents" ] || fail "no app at $APP"
[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ] \
    || fail "the build changed a tracked file of the clone:
$(git -C "$SRC" status --porcelain --untracked-files=no)
        What was built is then not the tag."

step "Checking the built app (scripts/check_bundle.sh, held to $COMMIT)"
bash "$SRC/scripts/check_bundle.sh" "$APP" "$COMMIT" || fail "the bundle check failed"

# --- 4. the archive, and the archive read back ------------------------------------------
VERSION="$(sed -n 's/^ *"version": "\([^"]*\)".*/\1/p' "$SRC/gui/src-tauri/tauri.conf.json" | head -1)"
[ -n "$VERSION" ] || fail "could not read the version from tauri.conf.json"
ZIP="$BUILD/out/mRNAspect-$VERSION-macos-arm64.zip"
step "Archiving with ditto, which keeps the bundle as it was signed"
ditto -c -k --keepParent "$APP" "$ZIP" || fail "ditto could not write $ZIP"

step "Reading the archive back, unpacked somewhere new (scripts/check_release.py)"
CHECK=(--commit "$COMMIT" --source "$SRC" --forbid "$(cd "$HOME" && pwd -P)" --forbid "$SRC")
for pair in "${MIN_VERSIONS[@]}"; do CHECK+=(--min "$pair"); done
for name in "${ABSENT[@]}"; do CHECK+=(--absent "$name"); done
cd "$SRC"
pixi run --frozen python scripts/check_release.py "$ZIP" "${CHECK[@]}" \
    || fail "the archive did not pass (the lines above say what). Nothing was uploaded."

SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
printf '\n\033[1mBuilt and checked.\033[0m\n'
printf '  archive  %s\n  sha256   %s\n  commit   %s (%s)\n' "$ZIP" "$SHA" "$COMMIT" "$TAG"
printf '\nLeft for a person:\n'
printf '  1. On a Mac with no development Python, pixi or Homebrew: open the app, run the\n'
printf '     fixture BAM in BAM mode with the minimum read count at 1, and look at the\n'
printf '     HTML and PDF reports (tables, figures, fonts).\n'
printf '  2. Attach the archive to the release of %s, with the sha256 above.\n' "$TAG"
