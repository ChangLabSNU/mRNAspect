# mRNAspect

Quality control for mRNA therapeutics from Oxford Nanopore **direct RNA** sequencing
(RNA004). One sequencing run in, one comprehensive QC report out — offline over a
finished BAM, or **live** while the run is still on the flow cell.

> Status: pre-release (`0.1.0`). Both command-line tools work and are tested. The desktop
> app for macOS is signed ad hoc, not by Apple: [The desktop app](#the-desktop-app) says
> how to open it, and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) how to build it. Licence:
> MIT ([LICENSE](LICENSE)). Running the tests and the code's layout are in the same page.

## What it measures

| | metric | |
|---|---|---|
| 1 | **Per-base identity** | reads aligned to your reference construct; identity with its substitution, deletion and insertion rates, and the positions where at most half the reads carry the reference base; reverse-strand, chimeric and Dorado-split reads are left out |
| 2 | **Poly(A) tail length** | per-read distribution; reverse-strand, chimeric and Dorado-split reads are left out |
| 3 | **Full length (5' reach)** | fraction of reads whose alignment starts within 50 nt of the reference's 5' end (a setting), as a range: over the reads admitted, and over every read; the same three kinds of read are left out |

Rates are reported **pooled** — summed numerators over summed denominators — so a run
number is not an average of per-construct averages.

## Install

Core runs on **linux-64 and osx-arm64**. There is no Windows build: `pysam` has no
win-64 conda package, so Windows means WSL2. **There is no Intel-Mac build either** —
`osx-arm64` means Apple Silicon, so a pre-2020 Mac is not covered and `pixi install`
will fail to resolve rather than explain itself. The desktop app targets **macOS on
Apple Silicon**, which is where the `.app` is built and bundled.

From a copy of this repository, with [pixi](https://pixi.sh) installed:

```bash
pixi install
pixi run dev-install
```

The commands below then run inside that environment: open it with `pixi shell`, or put
`pixi run` in front of each. `pixi install` prints `the lock file ... uses an older format
(v6), run pixi lock to upgrade to v7`; ignore it, nothing is wrong with the environment.

Two things are deliberately **not** installable and must be supplied by you:

- **A reference FASTA.** There is no default. A valid FASTA of the *wrong* construct
  maps ~0% and reads exactly like a dead flow cell, so the pipeline refuses to start
  without one rather than guessing.
- **dorado-server and a basecalling model** (POD5 mode only). The server is in ONT's
  proprietary bundle; models are downloaded by you into no predictable location. This
  pipeline is specified for `rna004_130bps_hac@v5.3.0` and for `m1psi_tuned_v1`, the m1Ψ
  fine-tune of it (the exported folder `qcrna_m1psi_v1_ov2500`; how it is distributed is
  not settled yet): both carry the poly(A) constants the correction needs, and any other
  model gets dorado's tail length uncorrected. dorado-server 7.13.6 loads both (checked on
  CUDA, 2026-10-28). It runs on CUDA and, measured
  2026-09-23, on Apple Silicon with `device metal`. **Name the device** -- mRNAspect does not
  infer one from the platform, and will say so rather than guess. Point at them with
  `basecall.model` / `basecall.server.*`, `$DORADO_SERVER` / `$DORADO_CLIENT`, or have
  them on `PATH` — the discovery order is reported in the error when it fails.

BAM mode needs neither.

### The desktop app

A built app for **macOS on Apple Silicon** is attached to this repository's releases as
`mRNAspect-<version>-macos-arm64.zip`. It carries its own Python, minimap2 and samtools,
so BAM mode needs nothing else installed. POD5 files and live runs need ONT's
dorado-server on the same Mac, as above.

The app is signed ad hoc, not by Apple, so macOS stops the first open of a downloaded copy
with "Apple could not verify 'mRNAspect' is free of malware that may harm your Mac or
compromise your privacy." Close that dialog, open System Settings › Privacy & Security,
and choose **Open Anyway** next to the line about mRNAspect. macOS asks once per copy.

It has been run on **macOS 27**, where it is built, and on **macOS 15** on two other
Macs (a MacBook Air M1 with 16 GB and a MacBook Air M2 with 8 GB; BAM files,
2026-11-18). On both, every value in the result was the same as from a run of the same
input on Linux. Its binaries declare macOS 11 as the oldest system they run on; no system
older than 15 has been tried. The first report on a Mac that has not run the app before
can take a few minutes; later ones took about half a minute on the M2 Mac.

A result says which build made it: `provenance.git_commit` in `qc_result.json`, and the
"Source commit" row of the report, hold the commit the app was built from.

### BAM input: basecalled reads, aligned or not

BAM mode takes a folder of BAM files of basecalled direct-RNA reads, from MinKNOW, dorado
or dorado-server, aligned or not. mRNAspect aligns every read itself, with the same
minimap2 settings as POD5 mode (`align.minimap2_flags`, shipped as
`-ax map-ont -k7 -w5 --secondary=no -y`); an alignment already in a file is discarded,
so both modes report on the same alignment whatever was done upstream. It takes each
read's primary record, in the orientation it was sequenced. Every primary read that goes
into the alignment must come out of it once, mapped or not; if the counts differ, that
input stops and `run.log` gives both. A primary that another aligner hard-clipped comes
back shortened, not lost, so this does not catch it.

- **Basecaller tags.** Poly(A) needs `pt` (dorado `--estimate-poly-a`); without it
  poly(A) is reported as not measured, and identity and full-length are unaffected. `RG`
  names the model, which picks the poly(A) correction's constants. `pi`/`sp` from dorado's
  read splitting feed the read rule. The move table (`mv`) is left behind unless
  `basecall.emit_moves` is on: carrying it about doubles the alignment time.
- **Failed reads too.** MinKNOW writes `bam_pass/` and `bam_fail/`. Leaving `bam_fail/`
  out is a qscore filter, and m1Ψ reads have lower qscores, so put both in one folder.
- **One folder, not searched recursively**, on purpose: barcode subfolders would be
  pooled silently (see below).
- No GPU and no dorado-server. minimap2 runs on the CPU, on every CPU the process may use
  unless `align.threads` says otherwise; the aligned BAMs are kept under `basecall_cache/`
  in the run folder.

### What the input must be: one sample, or already demultiplexed

**mRNAspect does no barcode demultiplexing.** It splits a run by **reference contig**, not by
barcode: each read goes to a per-construct track keyed on its `reference_name`, and
nothing reads a `BC` tag. The practical consequences:

- A **multi-construct** run is handled, and reported per construct. That is the normal
  case and needs nothing from you.
- A **barcoded** run whose barcodes carry *different* constructs separates correctly, as
  a side effect of the constructs differing.
- A **barcoded** run whose barcodes carry the *same* construct — two batches of one
  product, say — is **silently pooled into one result.** Both barcodes map to the same
  contig, so there is nothing for mRNAspect to split on. The number is not wrong; it is a
  number about the pool rather than about either batch.

So: feed one sample per run, or demultiplex upstream and run mRNAspect once per barcode.

**Every construct in the pool must be in the reference.** A read goes to the reference it
matches best, so a construct missing from the reference does not disappear: its reads align
to its nearest relative and show up there as lower identity, with the mismatch rate raised
to about the missing construct's share of the reads where the two differ. Nothing in
mRNAspect detects this; check the reference against what was pooled. A reference that is
simply wrong at some positions shows as flagged positions near 1.0 in the per-position figure;
a warning for that case is recorded in [docs/after_preprint.md](docs/after_preprint.md).

**Each reference record is the transcript, exactly: from its first transcribed base to the
last base before the poly(A).** Leave out the promoter and anything upstream, the poly(A)
itself, and anything after it. Both ends are measured against:

- **The 5' end** is where a full-length read has to reach, within 50 nt by default
  (`module_params.terminal_reach.full_length_5p_max_short_nt`). Extra sequence
  there is a stretch no read covers: a T7 promoter left in (17 nt) already uses a third of
  that allowance, and longer additions push the full-length ratio towards zero.
- **The 3' end** feeds the poly(A) correction: U12 is the number of U in the last 12 nt,
  and on the m1Ψ arm each extra U lowers the corrected tail by about 0.95 nt. A reference
  that runs into the poly(A) has U12 = 0, outside the range the correction was fitted on,
  and the report flags it; one that ends a few bases early or late shifts the tail
  without a flag.

The sequence may hold only A, C, G, T or U, in either case. Anything else, N included,
stops the run with the record, line and column named.

## Use

Every path below can instead live in your config (`input_dir:`, `output_dir:`,
`output_name:`, `reference:`, `basecall.model:`, `basecall.server.binary:`), because on a
given instrument they do not change between runs. A flag always wins over the config, so
a one-off run needs no config edit.

```bash
# offline, over BAMs of basecalled reads (aligned or not: mRNAspect aligns them)
mrnaspect --input-dir ./bams --reference ref.fasta --output-dir ./out --mode bam

# or, with all of that in a config
mrnaspect --config my_run.yaml --mode bam --output-name batch_0412

# live, watching a MinKNOW run directory (read "Live runs" below first)
mrnaspect-realtime --input-dir /path/to/run --output-dir ./out --config my_config.yaml
```

Both write **one JSON object to stdout** and a human sentence to stderr, so a caller and
an operator can share one invocation:

```bash
mrnaspect ... | jq -r .qc_result      # the machine-readable result file
```

Keys are always present (`null`, never omitted): `status`, `exit_code`, `run_dir`,
`report`, `qc_result`, `mapped_reads`, `message`, `error`. Exit codes: **0** a verdict was
reached (including "not enough reads" — that *is* an answer), **1** the run produced no
verdict, **2** the command could not have worked.

Every setting, and why it has the value it has, is in
[docs/config_reference.md](docs/config_reference.md). `mrnaspect --print-config` prints the
configuration a run would use, without running anything.

## Live runs

`mrnaspect-realtime` follows a MinKNOW run while it is sequencing. Its config must say
`mode: pod5`, and it needs dorado-server and a model (see Install).

**Point `--input-dir` at the run directory, not at its `pod5/` folder.** That is the
directory MinKNOW makes for each run, `<experiment>/<sample>/<run_id>`, with `pod5/`
inside it. MinKNOW marks the end of a run with a `final_summary_*.txt` there, one level
above the POD5 files; pointed at `pod5/`, mRNAspect never sees it and the run never ends.

**Unless you stop it, a run ends only when MinKNOW ends it**, not when files stop
arriving. After the end marker, mRNAspect waits for the last POD5 files to finish writing,
analyses them and writes the final report. A pause in sequencing (a mux scan, an operator
action) looks the same as a crash from outside, so after 15 minutes with no new file it
warns and keeps waiting.

**To stop early**, press Ctrl-C (or Stop in the desktop app). The file in progress is
finished and the final report is written, which can take a minute; files still waiting
are not analysed, and the report says so. To stop at once, kill the process (Ctrl-C prints
the command), then render a report from the last snapshot:

```bash
mrnaspect --output-dir <the run's directory> --resume-from-snapshot
```

This only renders what had been analysed; it does not continue the run.

**To continue a run** that was stopped or crashed, start it again with the same
`--output-dir` and `--output-name`, or with `--continue-latest` for the most recent one.
Files already analysed are not basecalled again, and the run carries on from where it
stopped.

### Before the run: MinKNOW's POD5 interval

The live mode reads a run one POD5 file at a time, as MinKNOW closes each one, so the
report is only as recent as the last closed file. MinKNOW closes POD5 files **by time, not
by read count**, and by default once an hour (`batch_duration = "3600s"`): left there, a
live run shows nothing for up to an hour and then moves in hourly steps. mRNAspect was
built and measured at **180 s**, a new file every 3 minutes.

The interval is not among MinKNOW's run-setup options. It is one line in MinKNOW's own
output-writer config, `default_writer.toml`, and changing it needs admin rights:

1. Find the file and keep a copy of the original.

   ```bash
   find /Applications/MinKNOW.app -name default_writer.toml   # macOS
   find /opt/ont/minknow -name default_writer.toml            # Linux
   sudo cp /path/to/default_writer.toml /path/to/default_writer.toml.orig
   ```

2. Open it with `sudo` (e.g. `sudo nano /path/to/default_writer.toml`) and, in the
   `[writer_configuration.read_pod5]` section, change one line:

   ```toml
   batch_duration = "180s"   # default "3600s"
   ```

   Leave `bases_per_batch` alone. A file closes at whichever limit it reaches first, and a
   3-minute MinION file is a few hundred MB, well under that cap. POD5 has no
   reads-per-file setting; the `batch_count = 4000` you may see belongs to FAST5.

3. Restart the computer. MinKNOW reads the file only when it starts, and closing its
   window can leave it running in the background.

4. Check on the next run: a new file should land in the run's `pod5/` folder about every
   3 minutes. The first and last files cover shorter windows; that is normal.

If macOS answers `Operation not permitted` even under `sudo`, allow your terminal app in
System Settings → Privacy & Security → App Management. If MinKNOW then fails to start, put
the `.orig` copy back. A MinKNOW update can restore the default, so check again after one.

### Trying it without a sequencer

`mrnaspect-realtime` can replay the POD5 files of a finished run the way MinKNOW writes
them: one file at a time into a new run directory, then the end marker. It follows that
directory as it would a live run, so the live mode can be tried, or a setup checked,
without a sequencer. The reads are real and are basecalled as they arrive, so it needs
dorado-server and a model like any live run. Only the arrival times are simulated;
`qc_result.json` records the driver as `realtime+mimic`.

```bash
mrnaspect-realtime --mimic-source-dir /path/to/pod5_files \
    --input-dir ./replay_run --output-dir ./out --config my_config.yaml
```

- `--mimic-source-dir` holds the `*.pod5` files, played in name order (`_2` before `_10`).
- `--input-dir` is the run directory the replay creates. One that already holds POD5 files
  or an end marker is refused. The files are hard-linked into it where the disk allows
  and copied otherwise.
- A file arrives every 180.6 s, the interval recorded from MinKNOW set to 180 s.
  `--mimic-speed 2` halves every interval. Basecalling is not sped up, so a fast replay
  falls behind as a real run would. `--mimic-limit N` plays only the first N files.

The test dataset that accompanies the preprint (Zenodo) holds 40 such files, the first two
hours of one run.

## Outputs

`qc_result.json` (machine-readable, provenance-stamped with the git commit, the exact
command and the version of every module that ran), plus an HTML/PDF QC report. It is not a
certificate of analysis: no acceptance criteria are set, and the report says so. In live
mode, interim reports render as the run accumulates. `report_latest.html` is the one to
open during a run: it draws the figures and reloads itself every three minutes, and stops
reloading once you press on a figure, so a zoom is not undone; reload it yourself to
update. The numbered `report_interim_<n>.html` copies keep no figures, so that each stays
small.

## Licence

mRNAspect is released under the MIT License ([LICENSE](LICENSE)), copyright © 2026
Qbiolab. The licence covers mRNAspect's own code only. The software the desktop app
bundles (Python and its libraries, minimap2, samtools, the Tauri runtime, plotly.js)
keeps its own licences: the app carries each one's notice and licence text in one file,
which About opens (written at build time by `scripts/make_notices.py`). mRNAspect does
not include a basecaller: dorado-server and its models are Oxford Nanopore Technologies'
software, installed by the user and used under ONT's terms. A basecalling model
fine-tuned from an ONT model is not covered by this licence either.
