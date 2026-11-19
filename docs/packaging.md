# Packaging the app

What a shipped mRNAspect is, what is already in place, and what is measured rather than
assumed. Written 2026-10-15 after a spike; every number here was produced by running
something, and the two places where that was not possible say so.

## What ships

One `.app`: the Tauri shell, plus a Python environment it drives. The shell never
computes anything — it starts `<env>/bin/python -m mrnaspect.main` and reads the JSON that
comes back. That boundary is the reason this is tractable: the thing being bundled is an
ordinary interpreter with an ordinary environment, not a framework.

## Already in place

`resolve_python` (`gui/src-tauri/src/lib.rs`) already looks for the bundled interpreter
at `Contents/Resources/env/bin/python3`, falls back to a flat `env/` beside the binary,
and only then to the development checkout's `.pixi` env. `$QCRNA_PYTHON` overrides all of
it, and an override that does not resolve is an error rather than a silent fall-through.

So the lookup is done. **`tauri.conf.json` declares the resource that satisfies it:**

```json
"resources": { "../../dist/bundle-env": "env" }
```

The map form rather than a glob, because the map names the DESTINATION and the
destination is the one thing `resolve_python` is specific about: a glob would place the
tree under its own source path instead of at `Contents/Resources/env`.

### What this replaces, and why

Until 2026-10-21 that line read `"../../.pixi/envs/default": "env"` -- the development
environment, bundled directly. It was wrong, and the way it was wrong is worth keeping:
the `.app` would build, start, and find an interpreter, and only then resolve Core's
import -- the package was called `qcrna` then -- to **an absolute path on the machine that
built it**. `pixi run dev-install` registers Core as an editable install, so the
environment contains a finder holding that path and nothing else (measured under the old
name):

```
MAPPING = {'qcrna': '<checkout>/qcrna'}
```

A shipped app would have had no Core at all. `scripts/check_bundle.sh` reported this
from the day it was written, under a heading saying it was not expected to pass -- which
is how a release blocker spent a week looking like a known limitation.

So the two environments are now separated. `.pixi/envs/default` stays editable and is
what you develop against; **`scripts/stage_bundle_env.sh` builds `dist/bundle-env`** from
it, with Core installed for real, and that is what ships:

```bash
npm run stage-env          # or just build: prebuild/predev run it for you
```

**Wired into npm's lifecycle rather than left to memory.** `gui/package.json` declares
`prebuild` and `predev`, which npm runs synchronously before `build` and `dev` -- and
those two are exactly what `tauri.conf.json`'s `beforeBuildCommand` and
`beforeDevCommand` call. There is no race with the Rust build's resource check: Tauri
waits for the dev server before starting cargo (observed 2026-10-22 on the Mac -- vite
prints its URL, *then* `Running DevCommand (cargo run ...)`).

This was not the original arrangement, and the gap bit immediately. `dist/bundle-env` is
gitignored, so the first `npm run tauri dev` after pulling the A1 change failed with
`resource path '../../dist/bundle-env' doesn't exist` -- correct behaviour (better than
shipping an app with no Core) but an error that names the missing path and not the
command that makes it.

The copy is also skipped when the package set is unchanged: the environment moves only
when pixi re-solves it, while Core moves every commit, so Core is reinstalled every run
and the ~860 MB copy is not. Measured: 4.8 s fresh, 1.6 s reused.

It copies rather than solving a second environment -- relocation is already proven
(`scripts/check_relocation.sh`, 10/10 on linux-64 and Darwin arm64), and a second solve
would be a second set of versions to keep in step with the first. It then strips the
editable pointer, installs Core, and asserts the property the whole arrangement exists
for: `import mrnaspect`, run from `/` under `-I`, must resolve inside the staged tree.

Two things fall out of using a real install rather than a link. The fixtures under
`mrnaspect/tests/data/` (2.2 MB) stay out of the app, because `pyproject.toml` excludes
`mrnaspect.tests*` and an editable install cannot honour an exclusion. And `build/` is
cleared first: setuptools reuses `build/lib`, so a stale tree there is installed in place
of the current source, silently and with no version change. That is not hypothetical --
a stale template was found in exactly that directory on 2026-10-20.

Measured on linux-64 (2026-10-22): the staged environment is 859 MB apparent — 33 MB of
that is `poppler`, test-only tooling the suite needs to read a rendered PDF back, which
lands here because it is declared in the one environment — and
`dist/bundle-env/bin/mrnaspect`, run from `/` under `env -i`, processes the fixture BAM and
renders both HTML and PDF with exit code 0. The osx-arm64 environment measures 420 MB.

The source path is relative to `src-tauri/`, so **the build must follow
`pixi install` and then `stage_bundle_env.sh` on the build machine**, and fails loudly if
it has not. The agreement between the two files is pinned by
`mrnaspect/tests/test_bundle_resource_contract.py`.

### Unverified, and how to settle it

> **Settled on the Mac, 2026-11-14:** the second outcome below. `check_bundle.sh` on the
> built .app counted 0 symlinks: Tauri's bundler copies every link as a file. The app runs
> and is 715M on disk for a 406M staged environment; the size is accepted for now.
> On such a bundle the script's clean result is
> "8 passed, 0 failed" with a NOTE at check 3, not 9 passed (eight checks then; twelve
> from 2026-11-18 and thirteen since 2026-11-19, clean at "13 passed"). What follows is the note as it was written
> before that run.

This cannot be checked from Linux: the bundler runs on the Mac, and this checkout has no
node, npm or cargo. What is written above is a configuration, not a measurement, and the
thing most likely to be wrong is **symlinks**.

The environment has **1,584** of them, and `bin/python3` is one of them (-> `python3.14`)
-- precisely the path the shell looks for. Three outcomes, in descending order of
happiness:

* preserved -- ideal, and the bundle is about the 420 MB the env measures;
* dereferenced -- the app still works, because `resolve_python` tests `is_file()` and a
  dereferenced copy is a file, but the bundle carries duplicate payloads and every one of
  them is another Mach-O to sign later;
* dropped -- `bin/python3` is simply absent and the shell reports that it found no
  interpreter, which is at least the failure this lookup was written to state clearly.

`scripts/check_bundle.sh <path to .app>` settles it in a minute: it checks the env is
there, that `bin/python3` is (and whether it stayed a symlink), counts the symlinks that
survived against 1,584, prints the size, and runs the bundled interpreter with the
environment stripped to `PATH=/usr/bin:/bin` to confirm `sys.prefix` is the bundle and the
native stacks import from it.

**Core is a separate step and the script says so.** `pixi run dev-install` leaves a `.pth`
pointing at this checkout and the bundle inherits it, so `import mrnaspect` resolves through
the source tree on the build machine and nowhere else. Shipping the environment and
shipping Core are two problems; this is the first one.

## Measured

**The environment relocates.** This was the open question, because a conda environment
bakes its build prefix into 62 `bin/` scripts and into `sysconfig`. It does not matter:
Core is invoked as `<env>/bin/python -m mrnaspect.main`, so no console script runs, and
CPython derives `sys.prefix` from the executable at startup. Copied to an unrelated
path, with `PATH` stripped to `/usr/bin:/bin`, the environment ran the whole pipeline —
BAM in, 166 reads, a 2.28 MB HTML and a 1.24 MB PDF out, exit 0. numpy, pysam and
WeasyPrint's native stack all loaded from the copy. `resolve_tool` found minimap2 and
samtools, because it looks under `sys.prefix` rather than on `PATH`.

**The environment is 823 MB** of actual bytes, which is what a bundle has to carry.
Earlier text here said 573 MB; that was `du -sh` on a compressed ZFS dataset, so it was
blocks-on-disk rather than bytes, and the same tree measures 496 MB under `/tmp` and
823 MB apparent. The "down from 1.1 GB" figure was taken the same way, so the *reduction*
is real and roughly proportional but both endpoints understate the payload. A DMG is not
on compressed ZFS.

What the reduction was: the `matplotlib` meta-package pulled `pyside6` -> `qt6-main` ->
`libclang` -> `libllvm`, about 280 MB unpacked, into a process that selects the Agg
backend and never opens a window. See the commit that switched to `matplotlib-base`; it
is guarded both at run time and in `pixi.toml`.

**`osx-arm64` is already a pixi platform**, and pysam, minimap2 and samtools all publish
builds for it, so `pixi install` on the Mac produces the environment to bundle.

## What the build machine cannot show: paths to itself (2026-11-18)

Everything under "Measured" was measured on the computer that built the environment, and
that computer still holds the environment the copy was made from. That is a blind spot,
and it hid a defect that would have stopped every run on any other Mac.

**The defect.** conda writes the folder an environment is installed in into some of its
files. On osx-arm64, ncurses' `libncursesw.6.dylib` (and three sibling files, and
`libpython3.dylib`) name another library that way: a re-export of
`<install folder>/lib/libtinfow.6.dylib`, by its full path. `samtools` loads
`libncursesw`, and Core starts samtools for every alignment. In the built app the
reference still named the development environment. On the build machine the file is there,
so the app ran and `check_relocation.sh` and `check_bundle.sh` passed; on another Mac
macOS finds no such file and does not start samtools.

**How it was found.** By reading the built app's files, on Linux, without running it:
every Mach-O's load commands, and a search of every file for the build folder's path.
Then confirmed on the build machine with `DYLD_PRINT_LIBRARIES=1 <app>/.../bin/samtools
--version`: macOS listed `libtinfow.6.dylib` twice, once from the app and once from the
development environment.

**What is done about it.**

* `scripts/bundle_load_paths.py <env>` runs at every staging. It rewrites each such
  reference to `@loader_path/<the copy in the same environment>` with Apple's
  `install_name_tool`, signs the changed file again (a changed Mach-O no longer matches
  its signature, and Apple Silicon does not run it), reads the files back and fails if a
  library is still named outside. Five references in five files on the environment of
  2026-11-18.
* `check_bundle.sh` check 9 reads the built app the same way (`--check`, which runs on
  any platform); check 10 runs samtools, minimap2 and the report's native stack and has
  macOS list every library loaded (`--loaded`), which also covers libraries opened by
  name at run time. samtools is run the six ways `run_align.sh` runs it, and not as
  `samtools --version`: that command lists htslib's plugins for remote files, which
  htslib looks for in a folder compiled into the library, so on the build machine it
  loaded seventeen libraries from the development environment. No command Core runs opens
  a plugin (measured with `strace` on linux-64: three plugin files opened by `--version`,
  none by `sort`, `index`, `view`, `fastq`, `cat`, `flagstat`), and on a Mac without that
  folder there is nothing to open. A test holds the check to the commands
  `run_align.sh` uses.
* fontconfig, which the PDF's text goes through, reads its configuration from a folder
  compiled into the library, and that too was the build machine's. Core now sets
  `FONTCONFIG_PATH` to `<sys.prefix>/etc/fonts` before WeasyPrint is imported
  (`reporter._use_this_environments_fontconfig`). Without it another Mac would still have
  found the system's fonts, through fontconfig's built-in fallback, but not the rules in
  `conf.d`.

**What else carries the build folder's path, and is left.** About 1,270 files of the app:
the source path recorded in compiled Python (`.pyc`), pkg-config and cmake files, conda's
own records, console-script shebangs (never run: the shell starts `python3 -m
mrnaspect.main`), defaults compiled into libraries the app does not use that way (openssl's
certificate folder, glib's module folder, ncurses' terminfo folder), and the panic
locations in the shell's own binary. None is read at run time. They do mean the app shows
where on the build machine it was built.

**The app must not write into itself.** A seal covers every file, so a file the app
changes or adds after it is signed breaks it. Python does exactly that by default: a .pyc
records the time its source was last changed, a copy does not always keep file times, and
a stale .pyc is compiled again and rewritten in place; a module with no .pyc gets one
added. On the first sealed build (2026-11-18) the bundle check ran the bundled Python once
and `codesign` then reported "file modified" for .pyc files. Two things hold it:

* `scripts/bundle_bytecode.py <env>` is the last step of staging. It compiles every source
  under `lib/python3.x` as unchecked hash-based bytecode (PEP 552): the .pyc holds a hash
  of its source, and Python loads it without looking at the file's time. 3,335 sources,
  about 9 s the first time and under a second after; a second staging compiles only what
  changed, which is Core. Check 11 asks the same of the built app, and compares each hash
  with its source, since Python itself no longer does.
* the shell starts Python with `-B` (never write bytecode) at each of its four starts of
  Core, and every check in `check_bundle.sh` does the same, so a check leaves the app as
  it found it.

**The seal.** Each file of the environment is signed ad hoc on its own, which is what lets
it run on Apple Silicon. The bundle as a whole was not signed: no `Contents/_CodeSignature`.
`tauri.conf.json` now asks the bundler for it (`bundle.macOS.signingIdentity: "-"`, still
no Apple identity). Read in the bundler's source for the version in `package-lock.json`
(CLI 2.11.5): it signs the main executable and then the bundle, and nothing it copied
under `Resources`, so the environment's files keep the signatures they have.
`hardenedRuntime` is set to `false` because the bundler otherwise adds it to that
signature, and it serves nothing without notarization. `minimumSystemVersion` is `11.0`,
which is what every binary in the app declares; the bundler's default wrote 10.13 into
`Info.plist`. The last check (12 then, 13 since 2026-11-19) asks
`codesign --verify --deep --strict`, after every other check has run in the app. On the first Mac build with these settings (2026-11-18) the bundler
wrote the seal and `install_name_tool` and `codesign` did the rewrite: checks 1 to 9
passed there.

**`check_relocation.sh` had the same blind spot,** since it copies the environment beside
the original. It now applies the same rewrite to its copy and counts the result (eleven
checks; the "10/10" above is the script of 2026-10-16). Its pipeline step had also stopped
running: it passed an argument Core no longer takes and pointed at the fixture folder,
which now holds BAMs of two basecaller models. It runs one fixture BAM from a folder of
its own.

**Shown on the build Mac (2026-11-18, the app built from `73fd8cd`).** `check_bundle.sh`
ends "12 passed, 0 failed" (twelve checks then): 625 Mach-O files with no library named outside the app; the
six samtools commands load 89 libraries each, minimap2 83 and the Python stack 687, all
from the app or the system; 3,335 sources with bytecode that does not depend on file
times; and `codesign` reports the bundle "valid on disk" after every other check ran in
it.

Two things were then done by hand on the same Mac. With `.pixi` and `dist/bundle-env`
renamed away, the app was opened from Finder and a BAM-mode run completed: it no longer
needs the folder its environment was copied from. And a copy archived with `ditto`,
downloaded through a browser, was met with "Apple could not verify 'mRNAspect' is free of
malware…", allowed in System Settings, and ran clean.

**The build machine also has developer tools, and a user's Mac need not.** The report asks
for Core's git commit at every render. Where Apple's command line developer tools are not
installed, `/usr/bin/git` is a stand-in that opens a system dialog offering to install
them, so the app would have raised that dialog during every run, on exactly the Macs it is
for. Core now starts git only when its own code sits in a repository (a `.git` in the
folder or above), which code inside the app never does (`provenance._git_at`). The dialog
itself was not observed: the build Mac has the tools.

**Shown on a second Mac (2026-11-18).** A MacBook Air (M1, 2020, 16 GB) on macOS 15.7.9,
with no developer tools, given the archive of a build one fix older than the git change
above. macOS met it with the same "Apple could not verify…" dialog; it then opened, and
its window and charts drew as on the build Mac. It was run straight from Downloads, so
macOS ran it from a translocated, read-only folder, and a BAM-mode run of 28,990 reads
still completed (exit 0, 28,578 mapped, the bundled minimap2 2.30 and samtools 1.24, a
23-page PDF whose text is set as on the build Mac). The run's logs hold no font or library
error. Two things it showed that the build Mac could not:

* the "install the command line developer tools" dialog did appear, at the first interim
  report, which is what the git change above answers (shown answered on the third Mac,
  below);
* the first reports were slow: 95 s of silence before the first interim report and 67 s
  inside the PDF, of a run of 4 min 20 s whose alignment and QC took one minute. A new
  Mac builds its font caches and checks a downloaded app's libraries once; which of the
  two it was is not measured, nor is a second run there.

**What trying it on the build machine did to the build machine.** The run with `.pixi`
renamed away left a new `.pixi/envs/default` behind, holding one font cache: `fonts.conf`
names, first, a cache folder inside the environment as it was installed, and fontconfig
creates that folder when it is missing. Renaming the real `.pixi` back then moved it
INSIDE the new one, and the next staging stopped after its first step with exit 1 and no
message (a failed `ls` in a pipe whose errors were discarded, under `set -e`). Staging now
takes that line out of the `fonts.conf` that ships, so every Mac keeps the cache in the
user's own folder, and it refuses a folder that is not a whole environment in words.

**Shown on a third Mac (2026-11-18).** A MacBook Air (M2, 2022, 8 GB) on macOS 15.6.1,
with no developer tools, ran the same input twice from a translocated folder. The first
run was of the older build: the developer-tools dialog appeared, and the tester cancelled
it. The second was of the build from `5d24c01`, which has the git change: no dialog. Both
ran to the end (exit 0, 28,578 of 28,599 primary reads mapped, a 23-page PDF with the same
five embedded fonts as on the M1 Mac).

**The values are the same as on Linux.** The same BAM and reference were run on Linux from
the repository's environment, and each `qc_result.json` compared value by value. Outside
`provenance` and `timing` it holds 168,518 values; the number that differ from the Linux
run is 0 for the M1 Mac's run and 0 for each of the M2 Mac's two (the report's path
aside). All record minimap2 2.30-r1287 and samtools 1.24. So the environment in the app
computes on Apple Silicon what the x86-64 environment computes, on machines that never saw
the folder it was built in.

**Where a Mac's time goes is not explained yet.** In seconds, Linux / the M2 Mac's first
run / its second: from the end of the input to the report code's first log line 0.3 /
34.9 / 11.3; from WeasyPrint's "Step 1" to "Step 2" 0.4 / 22.7 / 20.6; writing the PDF
(Step 6 to 7) 8.3 / 7.6 / 7.1; the whole run about 48 / 111 / 71. On the M1 Mac's one run
the first two were 95.0 and 66.9. Part of it is paid once (matplotlib's font cache, 5 s;
some 24 s before the first report). About 11 s and 21 s remain on a second run where
Linux needs under a second for both. A log of a live run on the build Mac (2026-11-09, an
older Core, another input) shows 0.3 to 1.1 s between Step 1 and Step 2, so the gap is
not WeasyPrint on a Mac as such. Not tested: fontconfig finding no usable cache on a Mac
that is not the build machine, and macOS checking the libraries of a downloaded app each
time it starts. A one-minute run is not a blocker; it is the first thing a new user sees.

**A result did not say which build made it (2026-11-19).** When the M2 Mac's second result
came back, nothing in it could tell whether the new build had run: `qc_result.json`
recorded version 0.1.0 and `git_commit: null`, because Core in the app is a copy in no
repository, and four builds made that day were all 0.1.0. Staging now writes the commit
of the checkout, whether its tracked files differed from that commit, and the time into
the installed package (`scripts/bundle_build_record.py` writes `_build.json` beside the
installed Core after `pip install`, and replaces an older one: the staged environment is
reused, and pip does not own that file). `provenance.py` reads it and believes it BEFORE git. The order
matters: `dist/bundle-env` and the built `.app` lie inside the checkout that built them,
where git answers for the checkout as it is now, which after a pull is not the code that
was copied; a result from the app run in place had been naming that. The stamp carries
the record as `build` beside `git_commit` and `git_dirty`, and the report's "Source
commit" row shows it, with "working tree modified" when the build was made from changed
files. Staging asks the staged Core what it reports and stops if that is not the
checkout's commit; `check_bundle.sh` check 12 asks the same of the built app. A source
with no `.git` (a downloaded archive) has no commit to give: the record is removed and
both scripts say so. Shown on Linux: a copy of the staged environment outside any
checkout, run with nothing inherited, wrote a result naming the commit, and a report with
the row. Shown on the build Mac (2026-11-19, the app built from `cd7ca18`):
`check_bundle.sh` ends "13 passed, 0 failed"; check 12 reads "built from cd7ca18… (working
tree clean)" and Core in the app reports that commit; the other counts are as on
2026-11-18 (625 Mach-O files, 89 / 83 / 687 libraries loaded, 3,335 sources, the seal
valid after every check ran in the app). A run of that app on the build Mac, the same day
(one BAM, exit 0, 28,990 records): `qc_result.json` has `git_commit` cd7ca18…,
`git_dirty` false and the `build` record staging wrote, and the report's "Source commit"
row reads `cd7ca18`.

One limit: the commit is the checkout's. An app built from the working repository names a
commit of that repository, which a reader of the published one cannot look up. Build what
is given out from a checkout of the published repository, or say in the release's notes
which published commit that commit is.

**Still not shown.** A macOS older than 15; POD5 files or a live run on any Mac but the
build machine; the app built from `cd7ca18` on a Mac other than the build machine (the
two other Macs ran earlier builds, which differ from it by the build record and
documents).

## Not measured, and why

**Codesigning and notarization.** Apple requires every Mach-O in the bundle to be signed,
and a 573 MB environment is a few hundred dylibs. This cannot be tried from Linux and
needs an Apple Developer account. It is the largest remaining unknown and it is a
*process* unknown, not a technical one — the usual answer is `codesign --deep` plus a
hardened-runtime entitlement for the interpreter, then `notarytool`.

~~**Whether the relocation result holds on macOS.**~~ **Measured 2026-10-16 on
Darwin arm64 (MacBook Pro): it holds.** `scripts/check_relocation.sh` copied the
environment into `$TMPDIR`, stripped the environment to `PATH=/usr/bin:/bin`, and ran the
whole pipeline from the copy: numpy, pysam, yaml, jinja2, WeasyPrint and matplotlib all
resolved from the copy, the native extensions as `cpython-314-darwin.so`, the bundled
minimap2 2.30-r1287 and samtools 1.24 executed, WeasyPrint bound pango, exit 0, 166 mapped
reads, a 2.31 MB HTML and a 1.10 MB PDF.

Two numbers from that run worth keeping:

* **The osx-arm64 environment is 420 MB apparent, against 823 MB on linux-64** — about
  half. osx-arm64 is the platform that ships, so 420 MB is the figure a DMG has to carry.
* **The PDF is not byte-identical across platforms.** 1,104,536 bytes on macOS against
  ~974,000 on linux-64, a 13% difference, while the HTML differs by 15 bytes. WeasyPrint
  embeds whatever the system font stack resolves to, and the two platforms resolve the
  CSS stack differently. Nothing here depends on a reproducible PDF, but a QC report is the
  kind of document somebody eventually expects to hash.

## The PDF renderer: kept, measured 2026-11-10; figures vector since 2026-11-12

The question was whether the PDF renderer is heavy or incompatible enough to replace with a
lighter one. It is not. The PDF is drawn by WeasyPrint 69.0 (HTML and CSS paged media to
PDF; its own `pydyf` writer, Pango for text), from the same jinja2 template as the HTML,
with each figure drawn by matplotlib as SVG beforehand. WeasyPrint keeps SVG as vector
paths, so the PDF holds no raster image. (Vector figures were asked for, 2026-11-12;
until then they were 200 dpi PNGs, 194 of them in the report measured below.)

- **Install.** What the environment carries only for WeasyPrint is 12 conda packages,
  14.4 MB of a 1,403.8 MB linux-64 environment (sizes from `conda-meta`): `pyphen` 6.4 MB
  (hyphenation dictionaries the template never uses), `pango` 4.1 MB, `weasyprint`
  1.3 MB, `cffi` 1.0 MB, glib's tools, and five small Python packages. The heavy native
  libraries are there anyway: cairo, fontconfig, glib and fribidi arrive with
  `matplotlib-base -> libraqm -> libharfbuzz`, and icu with Python's sqlite. Dropping
  WeasyPrint saves those 14.4 MB, not the text stack.
- **Time.** `import weasyprint` 0.46-0.53 s. A 15-construct final report (43 A4 pages,
  from the 11-10 timing run's snapshot) renders in 33.0-34.0 s with vector figures:
  matplotlib 10.1 s for the 13 figure specs, WeasyPrint 21.4-22.3 s, the rest 1.5-1.6 s;
  2.45 MB; peak RSS 392-394 MB (2026-11-12, two runs). With PNG figures it was 14.2-14.6 s
  (WeasyPrint 3.1 s), 2.94 MB, 245 MB (Core 93b94f3, same snapshot). Interim reports are
  HTML only, so this is paid once per run. WeasyPrint draws SVG node by node in Python,
  so its time follows the number of SVG nodes: a numeric bar trace is drawn as one path
  (with a node per bar the same report took 50.1 s, WeasyPrint 33.6 s), and text as
  glyph outlines is a node per glyph. Live text would cut WeasyPrint's time by about a
  third (1.61 to 1.05 s on the 13 figures' first panels) but sets the figure text in
  whatever font the machine resolves, not the one matplotlib laid it out in.
- **Compatibility.** `pixi.lock` pins weasyprint 69.0 (noarch) and pango 1.58.2 for both
  platforms that ship, linux-64 and osx-arm64; on Darwin arm64 the relocated environment
  bound Pango and wrote the PDF (2026-10-16, above). The PDF is A4, PDF 1.7, with its
  fonts embedded as subsets (TeX Gyre Heros and Noto Sans Mono on linux-64; macOS
  resolves Helvetica first), so it opens anywhere. Licences: WeasyPrint is BSD-3, the
  Pango/glib stack LGPL and dynamically linked.
- **The alternatives** would each mean rewriting the print layout: the page footer with
  the sample identifier and page counter, the per-construct blocks kept together, the
  printed tables. reportlab, fpdf2 and matplotlib's PdfPages lay pages out in Python
  code, not from the template; a headless browser is far heavier. None saves more than
  the 14.4 MB above, and WeasyPrint is also what keeps SVG figures vector without a
  further converter.

## POD5 on the Mac: measured, not open

**POD5 mode is the shipping default, because the fine-tune only applies to signal.** If
MinKNOW basecalls with a stock model and writes BAM, this project's second contribution
cannot be applied at all and the QC runs on exactly the reads the fine-tune exists to
improve. BAM mode is the path for data that is already called. (2026-10-20.)

**It runs. Measured end-to-end on 2026-09-23:**

    16:36:42 dorado-server: ready on 127.0.0.1:5555 after 5.0s (pid 14328, device metal)
    16:36:42 [1/1] SIM41175_..._1.pod5: basecalling...
    16:46:39 [1/1] SIM41175_..._1.pod5: aligning...
    16:46:43 Ingested 7143 reads (0 skipped)

ONT ships an Apple-silicon `dorado_basecall_server`, it accepts `metal`, and Core drives
it there unchanged. The only code involved was `DORADO_DEVICE=metal`; `dorado_server.py`
already declines to guess a device when there is no `nvidia-smi`, which is what makes a
Mac run a configuration rather than a port.

**What that run also measured is the throughput:**

| | reads/s | per-POD5 basecall |
|---|---|---|
| lab, 2x2080 Ti | 334.1 | 17.5 s (median 5,891 reads) |
| MacBook, Metal | **12.0** | 597 s (7,143 reads) |

MinKNOW writes a POD5 about every 180 s, so a median POD5 needs 491 s on Metal: **2.7x
over budget**. The project's decision (2026-10-05/07) is to assume a newer Apple silicon
clears it; the bar is **~33 reads/s sustained**, and **which Mac produced 12.0 was never
recorded**, which is the only reason the number is not simply transferable.

**And since the backpressure work, falling short is no longer a failure.** The 2026-09-23
entry concluded "a MacBook cannot keep up with a live run", which was true of the pipeline
as it then stood. Inputs are now admitted at a fixed stride when the queue backs up, the
run keeps pace, and the report says it is a sample and what fraction of the reads it rests
on. A slow Mac yields a sample rather than a stall.

### What this replaces

An earlier version of this section asked whether MinKNOW could basecall on a **remote**
dorado server, on a workstation GPU, and called that the question deciding the bundle size.
It was carried from a meeting note and never checked -- this project's own "verify against
real tool behaviour" rule, broken by the file that keeps citing it. Two things are wrong
with it. MinKNOW's basecalling settings offer model, modification and output and nothing
else (2026-10-20). And Core could not have used a remote server anyway:
`dorado_server.py` defaults `host` to `127.0.0.1`, starts the daemon locally, gates on the
local `nvidia-smi`, and passes only `DORADO_PORT` onward. There is no host in that path.

A second version then asked whether ONT ships an Apple-silicon build at all -- which the
2026-09-23 run had already answered, in this repo's own log, with a pid and a device name.

## Next, in order

1. On the Mac: `bash scripts/check_relocation.sh`, after `pixi install` and
   `pixi run dev-install`. It copies the environment to a path it has never seen, runs
   the whole pipeline from there with the environment stripped to `PATH=/usr/bin:/bin`,
   and checks ten things. Two minutes, and it either settles the relocation question or
   turns it into the main problem. **Done 2026-10-16: 10/10 on both linux-64 and
   Darwin arm64.**
2. Measure sustained reads/s on the Mac that will actually ship, and RECORD WHICH MAC.
   The bar is ~33 reads/s; the one measurement on file is 12.0 from an unrecorded machine.
   `samtools view <out>.bam | head -1 | tr '\t' '\n' | grep -E '^(er|MM|pt):'` says what
   the resulting BAM carries -- `er:Z` will be absent, which is why end reason comes from
   `sequencing_summary_*.txt`.
3. Build an unsigned `.app` (`resources` is now declared) and run
   `bash scripts/check_bundle.sh <the .app>`. That is the first build that could run on a
   machine without the checkout, and the script says how close it is.
4. Codesigning and notarization, which needs the Developer account.
