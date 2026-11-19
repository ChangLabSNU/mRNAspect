# mRNAspect GUI (Tauri)

The product's one shipped artifact. It consumes Core as an installed package and
**must never depend on Snakemake** (`CLAUDE.md`, Key Design Decisions).

It lives in this repository, next to Core, because the two are version-matched: the GUI
reads `latest_snapshot.json` and the CLI result object, and both are Core's contract. A
separate repository would leave that pairing unmanaged.

Nothing here is packaged into the `mrnaspect` wheel — `pyproject.toml` has
`include = ["mrnaspect*"]`, so this directory is invisible to setuptools.

## Layout

```
gui/
├── index.html          # the page the WKWebView loads: shell, pages, dialogs, tokens
├── package.json        # front-end deps + the tauri CLI
├── src/main.ts         # the front end: start flow, polling, Overview, panels, Settings
├── src/tween.ts        # the Overview's two seconds of movement on a new result (R5)
├── src/dark_theme.json # GENERATED from figspec (scripts/gen_gui_themes.py); do not edit
├── src/light_theme.json # GENERATED likewise, the light theme (2026-11-12)
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json
    └── src/lib.rs      # the Rust host: spawns Core, polls the snapshot, cancels
```

## The window (redesign of 2026-11-06)

Dark by default, MinKNOW-style; Light in Settings › Display (since
2026-11-12). Cmd + and Cmd − zoom the window (Tauri's own shortcut). A left menu (Start · Current run · Log · Settings ›), and Help at
the top right:

* **Start** — three cards (Live run, POD5 files, BAM files) that fix Core's `--mode` and
  the live driver, then one choice per screen (2026-11-12): Input · Reference · Modification ·
  Model (POD5 and Live) · Sample · Save to · Review. The reference is a file or a pasted
  sequence. The input takes any level of a
  MinKNOW run: Core's `--describe-input` (Rust `describe_input`) names the folder the run
  reads, counts its inputs and lists the runs below; *Find MinKNOW runs* asks it about
  MinKNOW's data folder. From the start of a run, Core's `run_status.json` (Rust
  `Poll.status`) gives the run card its input count.
* **Current run** — the run card (Elapsed · Inputs · Updated/Finished, Details, *Stop
  analysis*, *View PDF report*, *Show in Finder*) over a tab row of eight panels. The Overview is drawn from
  `latest_snapshot.json`; the other seven take their figures from `figures.json` by the
  `panel` key Core writes, so the GUI never groups figures itself. Core rewrites
  `figures.json` on every interim, so the charts appear during the run. Under
  `min_reads` mapped reads a panel of reads says so and draws nothing. When a new result
  arrives the Overview's numbers, bars and histograms move to it in two seconds
  (`tween.ts`); the panels do not.
* **Settings** — Device (server, Processor, models folder; opens on first launch, with
  Skip), Display (the theme), About (version, copyright, licence, the disclaimer, and a
  button that opens the third parties' notices the build carries).
* **Help** — which folder each run kind takes (MinKNOW's folder tree), what each
  Overview card shows, what the colours mean (the colour per result type and the two
  statuses, with swatches drawn from the theme's own tokens), where the results go.

Every chart Core builds is drawn in the report's light colours; `main.ts`'s `themeSpec`
swaps each colour through the current theme's map (`dark_theme.json`, or
`light_theme.json`, which moves only the grid) before Plotly draws it. The report and its
PDF stay white paper. Change a GUI colour in `figspec.py`, then run
`python scripts/gen_gui_themes.py`; `test_gui_dark_theme.py` and `test_gui_light_theme.py`
fail until you do.

Each result type has a colour (`figspec.RESULT_COLOURS`, four families): the report's
figures carry it, and the page's own marks take it from `--mark`, which `[data-result]`
sets per card and panel. The light theme is `figspec.LIGHT`, the report's paper, in
`light_theme.json` and `index.html`'s `:root[data-theme="light"]` block.

Text sizes are the GUI's own ladder, `figspec.GUI_TYPE_PX` (13 / 15 / 17 / 20 / 24 px;
the report keeps its own). `index.html`'s `:root` carries a checked
copy, and Plotly's text reads its sizes from that `:root` (`onLadder` in `main.ts`).

`main.ts` can be type-checked on a lab host without `node_modules`: unpack TypeScript
and the two `@tauri-apps` packages at the lockfile's versions into a scratch
`node_modules`, point a scratch `tsconfig.json` that extends this one at them with
`paths`, and run `tsc --noEmit` with VS Code Server's node.

## What the Rust half knows that is not obvious

These are measured, not assumed, and the comments in `lib.rs` carry the evidence:

* Core is spawned into **its own process group** (`.process_group(0)`). Python is the
  direct child now, not a `pixi run` wrapper, but Core starts dorado and minimap2 beneath
  it, and a signal to python alone would leave those running.
* Cancellation uses the **`kill(2)` syscall**, not the `kill` binary:
  `/usr/bin/kill -TERM -<pgid>` parses the negative pid as an option, does nothing, and
  **exits 0** — a silent no-op through every layer.
* **SIGTERM, not SIGKILL**: Core routes SIGTERM to a graceful abort that still finalises
  and writes a `user_abort` report with whatever it had. SIGKILL throws that away.
* The panel is rebuilt only when `latest_snapshot.json`'s **mtime** moves, so polling
  faster than Core writes costs nothing.

## Development

Tauri builds on macOS (the OS WKWebView) with no extra system packages. It does **not**
build on headless Linux hosts: they and have no webkit2gtk, and installing
it needs root.

```bash
cd gui
npm ci
npm run tauri dev
```

`npm ci` installs exactly `package-lock.json`. `src-tauri/Cargo.lock` is the Rust side's
lock, and it has to be the one the Mac builds with: staging lists the window's crates for
the third parties' notices with `cargo metadata --locked`, which fails on a lock the build
would change. After a build, `git status` on the Mac says whether it did: a modified
`Cargo.lock` is to be committed, or the repository's is behind the app that was built.

To build the app, and what the build is checked with: `docs/DEVELOPMENT.md`.
