# The GUI ↔ Core contract

What passes between the Tauri shell and Core, who owns which rule, and which of those
rules no compiler checks. Written 2026-10-22; every statement here is either in the code
it names or was measured, and the places where something is still unproven say so.

**Why this document exists.** The boundary between the window and Core (sidecar or
PyO3) and the configuration contract were both settled by implementation, not by a
design note, so the product's own boundary is documented here.

---

## 1. Shape: a subprocess, not an embedding

**Decided, and in the code.** The shell starts Core and reads what comes back:

```
<env>/bin/python3 -m mrnaspect.main           # offline
<env>/bin/python3 -m mrnaspect.realtime.main  # live
```

Core writes **one JSON object to stdout** and a human sentence to stderr, so a caller and
an operator share one invocation. Keys are always present (`null`, never omitted):
`status`, `exit_code`, `run_dir`, `report`, `qc_result`, `mapped_reads`, `message`,
`error`. Exit codes: **0** a verdict was reached (including "not enough reads" — that *is*
an answer), **1** the run produced no verdict, **2** the command could not have worked.

PyO3 embedding was the alternative and is not used. Nothing needs to change to keep it
that way; this records which of the two happened.

**`-m`, never `bin/mrnaspect`.** A console script's shebang is an absolute path to its
build-time interpreter, so `bin/mrnaspect` does not survive being bundled into
`/Applications`. The module form does. Measured 2026-10-21: a staged environment copied
to a new path renders a full report under `env -i` through `-m`, while the console script's
shebang still names the build directory.

## 2. Which interpreter

`resolve_python` (`gui/src-tauri/src/lib.rs`), in order:

1. `$QCRNA_PYTHON` — an explicit override. **If it does not resolve, that is an error, not
   a fall-through.** Falling through would run a different Core than the operator named
   and never say so.
2. `../Resources/env/bin/python3` — the bundle. Also `env/bin/python3` beside the binary,
   for a flat layout.
3. `<core_dir>/.pixi/envs/default/bin/python` — a development checkout.

**The bundled environment is a built artifact, not the development one.**
`scripts/stage_bundle_env.sh` copies `.pixi/envs/default`, strips the editable install and
installs Core for real into `dist/bundle-env`, which is what `tauri.conf.json` ships. This
is not a preference: `pixi run dev-install` registers Core as an editable pointer at the
build machine's checkout, so bundling the development environment produces an `.app` that
resolves `import mrnaspect` to a directory existing on one computer. Pinned by
`mrnaspect/tests/test_bundle_resource_contract.py`.

## 3. The argument surface, and the boundary no compiler checks

TypeScript sends a **camelCase** object; Rust receives **snake_case** parameters. Nothing
type-checks across that gap: rename one side and the build succeeds, the app starts, and
the argument silently arrives as nothing.

| TypeScript (`main.ts`) | Rust (`lib.rs`) | becomes |
| --- | --- | --- |
| `coreDir` | `core_dir` | the dev-checkout fallback in `resolve_python` |
| `inputDir` | `input_dir` | `--input-dir` |
| `reference` | `reference` | `--reference` |
| `outputDir` | `output_dir` | `--output-dir` |
| `runName` | `run_name` | `--output-name` (see §5) |
| `mode` | `mode` | `--mode` **offline only** — see below. Set by the Start card since 2026-11-06 (BAM files = `bam`, POD5 files and Live run = `pod5`); not a field |
| `basecallModel` | `basecall_model` | `--basecall-model` |
| `doradoDevice` | `dorado_device` | `--dorado-device` |
| `doradoServer` | `dorado_server` | `$DORADO_SERVER` (env, not a flag) |
| `realtime` | `realtime` | which module is run; true only for the Live run card |
| `arm` | `arm` | `--arm`: the m1Ψ box, `m1psi` or `U` (I4) |

Guarded by `mrnaspect/tests/test_gui_core_contract.py`, in Python, because **the machines that
run this suite have no cargo, rustc, node or tsc** (measured 2026-10-22) — so the GUI's own
toolchain cannot run until the Mac builds it, and a static cross-language check is the only
guard that fires when the mistake is made. It checks four things: every key TS sends is a
parameter Rust takes, every `val("…")` id exists in `index.html`, the shell does not
re-implement §5's rule, and the shell does not set `min_reads`. It does not replace building
the app; it catches what a build would not.

**Blank fields are dropped on the Rust side.** An empty text input means "not set", never
an empty string passed through.

**One argument comes from the shell rather than from a field.**

- `--config <generated>.yaml`, **live mode only.** `mrnaspect-realtime` follows MinKNOW's
  POD5 files or the BAM files MinKNOW basecalls, and must be told which: it does not take
  the shipped default (`mode: bam`) as an answer, since a run folder holds both kinds.
  The Live run card asks, and the shell writes a one-key config next to the logs saying
  `mode: pod5` or `mode: bam`. It lands in the run's own output directory rather than a
  temp file, so it survives as a record of what the run was given.

`--min-reads 1` was a second one, in both modes, until 2026-11-02; see §7.

## 4. Configuration: three layers, and blank is absent

```
shipped mrnaspect/config/run_config.yaml   ←   user --config   ←   a CLI flag
                                                  (one level deep)      (wins)
```

The shipped YAML supplies every driver default; there is no in-code `DEFAULT_CONFIG`. The
resolved dict is what provenance records.

**What the config can answer, so the GUI can pre-fill it:** `input_dir`, `output_dir`,
`output_name`, `reference`, `mode`, `basecall.model`, `basecall.server.binary`,
`basecall.server.client`, `basecall.server.device`, `min_reads`, the `sample.*` block. On a
given instrument these do not change between runs, and retyping a path is how the wrong one
gets typed. A flag always wins, so a one-off run needs no config edit.

**Blank is absent, everywhere.** `--output-dir ""` is a missing value, not the current
directory. The flags are therefore `type=str` and never `type=Path`: `Path("")` is
`Path(".")`, which is truthy, so an unfilled field would become a silent answer. Both
drivers carry the identical declarations, and `test_config_supplies_paths.py` asserts both.

**Two keys have no default and never should.**

- `reference` — a default could only be someone else's construct, and the failure is
  silent: a syntactically valid FASTA of the wrong construct maps 0.00% and reads exactly
  like a dead flow cell (measured 0/19,992 on IVT_260407). The shipped YAML carries
  `reference: null` so the key is *documented without being answered*, and the GUI's
  Reference field opens empty for the same reason.
- `basecall.model` — *which* model is methodological (`rna004_130bps_hac@v5.3.0`); *where*
  it is installed cannot be shipped. `@v6.0.0` crashes the server.

**`basecall.server.device` is not inferred from the platform.** "macOS means metal" was
challenged as an assumption on 2026-10-05 and the challenge was right. Metal works —
A Mac basecalled 7,143 reads with `device metal` on 2026-09-23 — but the
operator names it.

**A config error is reported, not raised.** `load_config` runs before the run directory
exists, inside a `try`, so an operator mistake reaches the terminal as
`{"status": "error", "exit_code": 2, …}` in **both** drivers rather than a traceback.

## 5. Run naming

`--output-name` names this run's subdirectory of `--output-dir`; blank gives
`run_YYYYMMDD_HHMMSS`. **Naming an existing one CONTINUES that run** and reuses its
basecall cache — on a 20 h run, that is the entire GPU cost not paid twice. Every run gets
its own subdirectory because artifacts are named by KIND, not by run, so two runs sharing
a directory silently overwrite each other's report and `qc_result.json`.

**Whether a name continues a run is said, before and during the run.** Nothing did until
2026-11-23, so an operator could not tell a continued run from a new one. Before Start,
`--print-config` given `--output-dir` and `--output-name` adds `run_dir_state` for the
folder a Start would write (`runtime/run_dir_state.py`): `exists`; the earlier attempt's
last progress from its `run_status.json`; the reason of the report already there
(`run_end`, `user_abort`, or none); `inputs_on_record` (the live driver's manifest, which a
live run restores without processing again); `basecalls_cached` (the offline driver's
record, reused where the POD5 and the model are unchanged); `unreadable`. The shell's
`read_config` passes the two fields, and the hint under Run name says what Start will do.
While the run goes, `run_status.json` carries `continued`: `null` for a new run,
`{"inputs_restored": n}` from the live driver, `{"basecalls_cached": n}` from the offline
one. Each driver is told by its command line (`continues=existed`, sampled before logging
creates the folder), since the folder exists by the time the driver is built.

**A name continues a run only with the same input** (`runtime/run_input.py`, 2026-11-23).
A live run continued on another MinKNOW run restored the first run's files from its
manifest and went on with the second's, so one report held both. The first start in a run
folder writes `run_input.json`: driver (`live` / `offline`), mode, the input folder
resolved, the reference's path and the sha256 of its content (the content is compared:
the shell writes a pasted reference to a new file at every start). Both command lines call
`check_run_input` after building the driver and before anything runs; another driver,
mode, input folder or reference content is refused (`RunInputMismatch`, a `ValueError`,
so exit 2), naming each difference. A folder that holds an earlier run's files but no
record is refused too (it cannot be checked); `run.log` alone, or nothing, is a new run.
Before Start, `--print-config` given `--input-dir`, `--reference`, `--mode` and `--live`
adds `recorded_input`, `refusal` (the same function's words) and `not_compared` (a pasted
reference, not yet written) to `run_dir_state`; the shell's `read_config` passes what
`start_qc` would, the page shows a refusal in red under Run name and keeps Start off, and
it asks again 300 ms after the last key, not only when a field loses focus.

**Core's run-folder refusals carry a code** (`RunDirError`, a `ValueError`, 2026-11-23).
Their messages are the command line's ("Pass --output-dir <dir>, or set `output_dir:` in
your --config"), and the window showed that sentence in red under Run name before an output
folder was chosen. `--print-config` now adds `run_dir_error_code` (`no_output_dir`,
`no_input_dir`, `run_name_not_one_folder`, `two_run_names`, `nothing_to_continue`); the
page words the two it can reach itself ("Choose an output folder.", not red; "A run name
is one folder name: …", red, Start off) and shows any other as Core wrote it. A test holds
the page's codes to Core's.

**The name must be a single directory name.** `resolve_run_dir` refuses a separator, `..`,
`.` or an absolute path, because `output_dir / name` would otherwise leave the directory
the operator chose:

```
"/etc/passwd" -> /etc/passwd      an absolute name REPLACES output_dir
"../escaped"  -> /out/../escaped  climbs out
"."           -> /out             writes into the PARENT
```

The rule lives in **Core**, at the one chokepoint both drivers pass through, so the CLI is
covered and the shell does not carry a second copy that can drift.

**In the shell, the run name and the log-file prefix are two strings.** They were one, so
continuing a run would have overwritten the previous attempt's `.stdout` — the only place
its failure is recorded. The log stem keeps the operator's name plus a timestamp.

## 6. What the shell must never do

Each of these has been violated at least once and fixed:

- **Validate what Core validates.** Two copies of one rule drift apart. The shell trims
  whitespace and drops blanks; Core decides whether a value is acceptable.
- **Supply a default Core refuses to supply.** The Reference field shipped pre-filled with
  `curlcake_ref.fasta` until 2026-10-21, undoing that rule at the only surface an end user
  touches.
- **Carry the machine it was written on.** All four path fields shipped pre-filled with
  paths under the developer's home directory.
- **Compute a metric.** The shell renders; Core measures. That includes a ratio: a
  construct's share of the run's reads and its full-length low end are Core's
  (`share_of_reads` on each construct and on `unassigned`, `full_length_fraction_lower`),
  not a division in `main.ts`. Until 2026-11-16 the share was of the mapped reads
  (`share_of_mapped`, `unassigned.off_reference_share_of_mapped`); both fields are gone.
- **Group figures itself.** Which panel a figure is on is Core's `panel` key in
  `figures.json` (`reporter._FIGURE_PANEL`), as the report's grouping is Core's `section`.
- **Invent a colour.** The GUI's colours are `figspec.DARK`, copied into
  `gui/src/dark_theme.json` and checked; a chart's colours are swapped through that file's
  map, never picked in TypeScript.
- **Demultiplex.** Core routes on `read.reference_name` and nothing reads a `BC` tag. Per
  barcode QC is the job of a separate, researcher-side workflow,
  and Core must never depend on it.

## 6a. What the shell reads back (2026-11-06)

Rust's `extract_panel` reduces `latest_snapshot.json` to the `panel` that `poll_run`
returns. Since the redesign it also forwards `inputs_processed` and `inputs_total` (absent
for a live run), `qc_metrics.metrics` whole, and `run_summary.quality_score` with its
distribution. `Poll` gained `elapsed_s` and `finished_at`, kept by Rust from when it spawned
Core; `test_gui_poll_contract.py` holds the Rust struct and the TypeScript type equal. Two
commands were added and take no path, like `read_figures`: `read_provenance` (the
`provenance` block of the run's `qc_result.json`, `null` before it exists) and `host_name`
(removed 2026-11-16, with the computer's name in the top bar).
`open_report` opens `report_latest.html` while the run is going.

Since 2026-11-10 Core's own snapshots carry a time as well: `latest_snapshot.json` and
each `interim_<k>_snapshot.json` have `generated_at` (the local time with its UTC offset,
ISO-8601) and `elapsed_s` (seconds since Core built its pipeline, a moment after the shell
spawned it), and every report's subtitle gives both. `extract_panel` does not forward
them: the shell's Elapsed stays its own.

Since 2026-11-09 Core writes `figures.json` on every render, interims included, and
`Poll` carries `figures_changed` (the file's mtime moved since the last poll); the shell
reads the file again then, so the panels draw Core's charts during the run. Since
2026-11-10 the page `open_report` opens during a run, `report_latest.html`, embeds them
too (a second render of the interim, about 0.09 s and 4 MB on 15 constructs) and reloads
on a timer that the reader's first press on a figure stops; the numbered
`report_interim_<n>.html` copies stay without figures.

## 7. Open

- **Nothing in §3's table has been compiled.** `runName`/`run_name` and the
  config-supplied paths are new as of 2026-10-22 and build for the first time on the Mac.
- **Config pre-fill is not implemented.** The GUI does not read a config; §4 describes what
  it would read. The intended route is Core emitting the resolved config as JSON rather
  than a YAML crate in Rust — the shell does not compute.
- **No file pickers.** Paths are typed. A Finder dialog needs `tauri-plugin-dialog`, a
  plugin registration, a capability permission, an npm package and TS calls: five places
  across two toolchains, none buildable on the suite's machines.
- **`--continue-latest` cannot find a GUI run.** It globs `run_*`; the shell's default
  names are `gui_<epoch>`. Harmless once the operator names the run.
- ~~**The shell forces `--min-reads 1`, and nothing says why.**~~ **Removed 2026-11-02
  (testing done).** It overrode both the shipped default (1000) and whatever the
  operator put in their config. `min_reads` is what raises the "not enough reads to
  diagnose" banner — the report's one statement about read sufficiency — so at 1 it could
  only fire on a run with zero mapped reads, and the CLI and the GUI meant different things
  by one setting. The shell now passes no `--min-reads`, so the config decides on both
  routes; `test_gui_core_contract.py` keeps it that way.
- **The bundle is unproven on macOS.** Whether Tauri preserves the environment's 1,584
  symlinks is what `scripts/check_bundle.sh` settles, and it needs a Mac.
