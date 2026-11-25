# Command line

`mrnaspect` analyses finished data and `mrnaspect-realtime` follows a live run
([live runs](live_runs.md)). Installation is in the README.

```bash
# BAM files, basecalled (aligned or not)
mrnaspect --mode bam --input-dir ./bams --reference ref.fasta --arm m1psi --output-dir ./out

# POD5 files, basecalled here
mrnaspect --mode pod5 --input-dir ./pod5 --reference ref.fasta --arm m1psi \
    --basecall-model /path/to/m1psi_tuned_v1 --output-dir ./out
```

Run the commands inside `pixi shell`, or put `pixi run` in front of each. A notice that the
lock file "uses an older format (v6)" is harmless.

## Options

- `--arm m1psi` or `--arm U` declares every construct's modification; `sample.arms` in a
  config names the ones that differ.
- `--basecall-model` is the model folder, for POD5 files. dorado-server is found through
  `basecall.server.binary` in a config, `$DORADO_SERVER`, or `PATH`. On NVIDIA an idle GPU
  is picked; on a Mac give `--dorado-device metal`.
- `--output-name` names the run's folder under `--output-dir` (default
  `run_YYYYMMDD_HHMMSS`).
- `--config my_run.yaml` holds the settings that stay the same between runs (alignment, QC
  thresholds, each construct's modification); a flag always wins over the config. Every
  setting and why it has its default is in [config_reference.md](config_reference.md).
- `mrnaspect --print-config` prints the configuration a run would use without running it.

## What a command returns

Each command writes one JSON object to stdout, with the keys `status`, `exit_code`,
`run_dir`, `report`, `qc_result`, `mapped_reads`, `message` and `error` (`null`, never
omitted), and a sentence for the operator to stderr:

```bash
mrnaspect ... | jq -r .qc_result   # path of the machine-readable result
```

Exit code **0**: a verdict was reached, "not enough reads" included. **1**: the run gave
no verdict. **2**: the command could not have worked.

## BAM input

- mRNAspect aligns every read itself with minimap2 (`-ax map-ont -k7 -w5 --secondary=no
  -y`) and discards any alignment already in the files, so BAM and POD5 runs report on
  the same alignment. minimap2 runs on the CPU.
- Poly(A) needs dorado's `pt` tag (`--estimate-poly-a`); reads without it, or with
  `pt = 0`, are counted separately. The read group (`RG`) names the model, which selects
  the poly(A) correction.
- Give the MinKNOW run folder to read `bam_pass/` and `bam_fail/` together. Leaving the
  failed reads out is a quality-score filter, and m1Ψ reads score lower. Given `bam_pass/`
  alone, the report says how many failed-read files were left out.
- A folder is not searched recursively, apart from a run folder's `bam_pass/` and
  `bam_fail/`. On a barcoded run, give one barcode's folder.
