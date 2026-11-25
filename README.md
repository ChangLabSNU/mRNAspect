# mRNAspect

Real-time quality control of therapeutic mRNA with Oxford Nanopore **direct RNA**
sequencing (RNA004). From one run, mRNAspect measures the sequence identity, transcript
completeness and poly(A) tail length of every construct, and updates the results while the
flow cell is still sequencing.

It also corrects the biases of N¹-methylpseudouridine (m1Ψ), which often replaces uridine
in therapeutic mRNA, with a basecaller fine-tuned on m1Ψ RNA and a poly(A) correction from
the reference sequence ([how](docs/m1psi_correction.md)).

Version 0.1.0 · desktop app for macOS (Apple Silicon) · command-line tools for Linux and
macOS · MIT licence

![Screen recording of the mRNAspect desktop app on macOS. On the Start page the user
chooses BAM files, selects a folder of 40 BAM files, the reference FASTA and an output
folder, names the run Demo, loads run_config.yaml and presses Start. The Current run page
fills with read length, poly(A) tail, read quality, construct share, per-base identity and
full-length charts. The HTML report then opens and scrolls to the whole-run poly(A)
tail-length distribution, median 87 nt.](docs/images/app_demo.webp)

*The desktop app on the [demo data](#demo-data) in BAM mode. The processing wait is cut.*

## What it measures

For each construct and for the whole run:

| | Attribute | What is reported |
|---|---|---|
| 1 | **Per-base identity** | Share of aligned bases that match the reference, and the mismatch rate at each position. Positions where at least half the reads disagree with the reference are flagged. |
| 2 | **Poly(A) tail length** | Distribution of dorado's per-read tail estimate, and per construct a [corrected](docs/m1psi_correction.md#polya-correction) distribution. |
| 3 | **Full length (5' reach)** | Fraction of reads whose alignment starts within 50 nt of the reference's 5' end. Only the 5' end is judged, because sequencing starts at the 3' poly(A) tail. |

Also reported: reads and mapping rate per construct, read length, read quality, mapping
quality, coverage, and MinKNOW's sequencing information (read end reason, pore activity,
bias voltage). Definitions and output files: [docs/results.md](docs/results.md).

## Three modes

| Your situation | Mode | Input |
|---|---|---|
| A run is in progress | **Live** | the MinKNOW run folder (POD5 or BAM files) |
| A run is finished, raw signal | **POD5** | a folder of POD5 files |
| The reads are already basecalled | **BAM** | a folder of BAM files (no GPU needed) |

Every mode gives the same results: `qc_result.json` (machine-readable, with provenance)
and an HTML and PDF report, in the run's own folder. A live run rewrites the HTML report
after every file.

## What you need

1. **Reads** from one sample. mRNAspect does not demultiplex: on a barcoded run, give one
   barcode's folder.
2. **A reference FASTA** with one record for every construct in the sample. Each record is
   the transcript exactly, from its first transcribed base to the last base before the
   poly(A): leave out the promoter and the poly(A).
3. **Each construct's modification**: m1Ψ or unmodified. An undeclared construct gets an
   uncorrected tail length.
4. **For POD5 files only**: ONT's dorado-server (tested: 7.13.6), a GPU (NVIDIA, or Metal
   on Apple Silicon) and a basecalling model:

   | Model | Get it |
   |---|---|
   | `rna004_130bps_hac@v5.3.0`, ONT's stock model | `dorado download --model rna004_130bps_hac@v5.3.0` |
   | `m1psi_tuned_v1`, [fine-tuned on m1Ψ RNA](docs/m1psi_correction.md#fine-tuned-basecaller) | to be deposited on Zenodo; until then, on request (open an issue) |

For BAM files: mRNAspect aligns the reads itself, poly(A) needs dorado's `pt` tag
(`--estimate-poly-a`), and giving the MinKNOW run folder reads `bam_pass/` and `bam_fail/`
together (leaving out the failed reads filters by quality score, and m1Ψ reads score
lower).

For a live run: give the MinKNOW run folder, and set MinKNOW to close a POD5 file every 3
minutes instead of every hour ([how](docs/live_runs.md)).

## Desktop app (macOS on Apple Silicon)

1. Download `mRNAspect-<version>-macos-arm64.zip` from
   [Releases](https://github.com/ChangLabSNU/mRNAspect/releases) and unzip it.
2. Open the app. It is not signed by Apple, so macOS blocks the first open: close the
   dialog, open System Settings › Privacy & Security, and choose **Open Anyway**.
3. On first launch, Settings › Device asks for the dorado-server, the processor and your
   models folder. BAM mode needs none of them: **Skip**.
4. On **Start**, choose **Live run**, **POD5 files** or **BAM files**, and answer one
   screen per step: Input · Reference · Modification · Model (POD5 only) · Sample · Save to · Review.
5. Press **Start**. **Current run** shows the latest results, and the run card opens the
   HTML and PDF reports.

Tested on macOS 15 and 27; older versions have not been tried.

## Command line (Linux x86-64, macOS on Apple Silicon)

With [pixi](https://pixi.sh) installed:

```bash
git clone https://github.com/ChangLabSNU/mRNAspect.git
cd mRNAspect
pixi install
pixi run dev-install
```

Then, inside `pixi shell`:

```bash
# BAM files
mrnaspect --mode bam --input-dir ./bams --reference ref.fasta --arm m1psi --output-dir ./out

# POD5 files
mrnaspect --mode pod5 --input-dir ./pod5 --reference ref.fasta --arm m1psi \
    --basecall-model /path/to/m1psi_tuned_v1 --output-dir ./out

# Live: follow a MinKNOW run while it sequences
mrnaspect-realtime --mode bam --input-dir /path/to/run --reference ref.fasta --arm m1psi \
    --output-dir ./out
```

`--arm m1psi` or `--arm U` declares the constructs' modification. Options, the config
file, and what a command returns are in [docs/command_line.md](docs/command_line.md).

## Demo data

The first two hours of one MinION run (15 in vitro transcribed constructs, m1Ψ and
unmodified): a bulk FAST5 file for MinKNOW playback, 40 POD5 files, BAM files from both
models, the reference FASTA and a `run_config.yaml`. It will be deposited on Zenodo under
CC BY 4.0; until then, it is available on request (open an issue).

## Documentation

- [Results and outputs](docs/results.md): what each measurement is, which reads count,
  and the files a run writes.
- [m1Ψ bias correction](docs/m1psi_correction.md): the fine-tuned basecaller and the
  poly(A) correction.
- [Live runs](docs/live_runs.md): how a live run behaves, the MinKNOW setting, and trying
  it without a sequencer.
- [Command line](docs/command_line.md) and [configuration](docs/config_reference.md).
- [Development](docs/DEVELOPMENT.md): building the app and running the tests.

## Licence

mRNAspect's code is under the MIT License ([LICENSE](LICENSE)), copyright © 2026 Seoul
National University. Bundled software keeps its own licences (About in the app opens their
notices). `m1psi_tuned_v1` is under the Oxford Nanopore Technologies Public License v1.0,
for research purposes only.
