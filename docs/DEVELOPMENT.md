# Developing mRNAspect

This page is for working on mRNAspect. For using it, see the [README](../README.md).

## Status

Pre-release (`0.1.0`), MIT. The library and both CLIs work and are tested, and the desktop
GUI that consumes them is built: a Tauri app for macOS on Apple Silicon that bundles its
own Python environment. Not yet settled: a signature from Apple (the app is signed ad
hoc), and the installer image, whose step failed on the last build.

The distribution, the commands and the import package are all `mrnaspect`
(`import mrnaspect`, `python -m mrnaspect.main`). The name was chosen on 2026-10-22, when
the package stopped being `qcrna`; nothing answers to the old name.

## Environment

```bash
pixi install
pixi run dev-install
```

`dev-install` registers this checkout in the environment without copying it, so edits are
live. Run it again after any `pixi install` that rebuilds the environment; the comments
on the task in `pixi.toml` say why.

`pixi install` prints `the lock file ... uses an older format (v6), run pixi lock to
upgrade to v7`. Ignore it: pixi 0.67.2 writes v6 even for a lock it creates from
scratch, so there is nothing to upgrade from this side. Nothing is wrong with the
environment.

## Tests

```bash
pixi run test
```

The fixtures live in `mrnaspect/tests/data/` and are tracked in git on purpose, so the suite
runs from a clone with no lab filesystem attached. `pixi run test-fast` runs the same tests
in parallel processes.

bam mode aligns every input with the real minimap2, as a user's run does. A test whose
numbers were fixed on a BAM's own alignment (the golden run: `small_aligned.bam` was
aligned with `-k14` and minimap2 2.28), or that needs a BAM aligned to another reference,
passes `aligner=passthrough_align` (`mrnaspect/tests/conftest.py`) to `OfflinePipeline`.
It is a test seam, not a setting: there is no config key or flag that turns alignment off.

`scripts/check_like_ci.sh` runs what CI runs, on this machine: a fresh clone of the
committed HEAD, the locked environment installed frozen, and the suite in shards. It
covers one platform per run, so while GitHub Actions is not running, a release needs it
on both a Mac and a Linux box.

```bash
bash scripts/check_like_ci.sh
```

## Building the desktop app

On macOS, Apple Silicon. It needs, besides pixi: the Xcode command line tools
(`xcode-select --install`), Rust (`rustup`, which brings `cargo`) and Node with npm.

```bash
pixi install && pixi run dev-install
cd gui && npm ci && npm run tauri build
bash ../scripts/check_bundle.sh src-tauri/target/release/bundle/macos/*.app
```

The app is `gui/src-tauri/target/release/bundle/macos/mRNAspect.app`. The build then makes
an installer image (`.dmg`); when that step fails, as it did on 2026-11-14, the app is
already there, and `npm run tauri build -- --bundles app` builds the app alone.

`check_bundle.sh` has thirteen checks. On a Tauri build its clean result is "13 passed,
0 failed" with a NOTE at check 3: the bundler copies the environment's symlinks as files,
so that check has nothing to pass. Check 7 is that what staging leaves out (readline,
pyphen's dictionaries) is not in the app; check 8, that the third parties' notices are in
it and whole, which needs `npm ci` and `cargo` to have run on this machine.

Checks 9 to 11 and 13 are about a Mac that did not build the app. The build machine still holds
the environment the app's copy was made from, so a library the app names by that folder's
path is found there and nowhere else, and checks 1 to 8 cannot see it. Check 9 reads every
Mach-O file in the app and fails on a library named by a path outside the app and the
system; check 10 runs samtools as Core runs it, minimap2 and the report's native stack,
and has macOS list what each loaded; check 11 asks whether any Python source would be
compiled again, and so rewritten, inside the app; check 13 asks `codesign` whether the
bundle is sealed as a whole, last, so that it also shows no check changed a file of the
app. `scripts/bundle_load_paths.py` and `scripts/bundle_bytecode.py` are what staging runs
and what checks 9 to 11 call; [packaging.md](packaging.md) has the account.

Check 12 is about results. Inside the app Core is a copy, in no repository, so git has no
answer for it; staging writes the commit of this checkout into the installed package
(`scripts/bundle_build_record.py`; the file is `_build.json` in the app's copy of the
mrnaspect package) and `provenance.py` reads it there, before git. The check asks that the record is in the app
and that Core, run from the app, reports that commit. Its line says "MODIFIED" when tracked
files differed from the commit at staging: the app then holds code no commit describes,
and its reports say so beside the commit. Build an app that is to be given out from a
clean working tree.

The app is signed ad hoc: sealed, with no identity from Apple. A copy that reached another
Mac through a browser or a chat client is quarantined by macOS, which stops the first open
with "Apple could not verify 'mRNAspect' is free of malware…": close the dialog, then
allow it in System Settings › Privacy & Security (Open Anyway), or clear the mark with
`xattr -dr com.apple.quarantine mRNAspect.app`. Seen on macOS 27 with a copy downloaded
through a browser (2026-11-18): the dialog, then the app opened and a run completed.

To hand the app to someone, archive it with `ditto`, which keeps the bundle as it was
signed:

```bash
ditto -c -k --keepParent mRNAspect.app mRNAspect-0.1.0-macos-arm64.zip
```

`npm run tauri dev` works the same way. Both stage `dist/bundle-env` first -- the
environment the app ships, built by `scripts/stage_bundle_env.sh` -- through npm's
`prebuild`/`predev` hooks, so it cannot be forgotten. It is gitignored, so a fresh clone
has to build it, and that first run takes about a minute; afterwards the copy is reused
and it takes a second. `npm run stage-env -- --force` rebuilds it from scratch.

See [packaging.md](packaging.md) for what ships and what has been measured.

## Layout

`main.py` offline driver · `realtime/` watcher, backpressure, sealing, MinKNOW mimic ·
`analysis/` engine, shared pileup, QC modules · `reporting/` QC report · `runtime/` aborts,
checkpoints, reference checks · `wrappers/` dorado-server, minimap2, samtools (always
orchestrated, never reimplemented). See [architecture.md](architecture.md)
for the data flow and [gui_core_contract.md](gui_core_contract.md) for what
passes between the desktop app and Core.
