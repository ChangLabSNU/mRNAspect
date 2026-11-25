# Live runs

The live mode watches the MinKNOW run folder, takes each POD5 or BAM file once MinKNOW has
finished writing it, and analyses the files one at a time. After every file the results,
`qc_result.json` and the HTML report are rewritten.

## How a live run behaves

- **What to follow.** POD5 files are basecalled by mRNAspect (dorado-server and a model
  needed). BAM files are the ones MinKNOW basecalls itself, from `bam_pass/` and
  `bam_fail/` (no dorado-server, model or GPU).
- **Give the run folder** (`<experiment>/<sample>/<run_id>`), not `pod5/` inside it.
  MinKNOW writes its end-of-run marker (`final_summary_*.txt`) there, and without it the
  run never ends. For BAM, giving `bam_pass/` or `bam_fail/` follows that folder only.
- **The run ends when MinKNOW ends it** and every queued file is analysed, not when files
  stop arriving. A pause such as a mux scan looks like a crash from outside, so after 15
  minutes without a new file mRNAspect warns and keeps waiting.
- **Speed.** On Linux with one NVIDIA RTX 2080 Ti, a POD5 file of 180 s of sequencing
  (about 5,700 reads) took 38 s, 81% of it basecalling; a slower GPU takes longer.
  Real-time throughput on a Mac has not been demonstrated. If basecalling falls behind,
  the config can let mRNAspect skip some queued files (`realtime.max_queue_depth`, off by
  default).
- **Stopping early** (Stop in the app, Ctrl-C on the command line) finishes the file in
  progress and writes the final report. Files still waiting are not analysed, and the
  report says so.

## Set MinKNOW to close a POD5 file every 3 minutes

A live POD5 run is only as recent as the last POD5 file MinKNOW closed, and by default
MinKNOW closes one per hour. mRNAspect was built and measured at 180 s. The interval is
one line in MinKNOW's `default_writer.toml`, and changing it needs admin rights.

1. Find the file and keep a copy of the original:

   ```bash
   find /Applications/MinKNOW.app -name default_writer.toml   # macOS
   find /opt/ont/minknow -name default_writer.toml            # Linux
   sudo cp /path/to/default_writer.toml /path/to/default_writer.toml.orig
   ```

2. Open it with `sudo` (e.g. `sudo nano /path/to/default_writer.toml`) and, in the
   `[writer_configuration.read_pod5]` section, change one line. Leave `bases_per_batch`
   as it is.

   ```toml
   batch_duration = "180s"   # default "3600s"
   ```

3. Restart the computer: MinKNOW reads the file only when it starts.
4. On the next run, a new file should land in `pod5/` about every 3 minutes.

If macOS answers `Operation not permitted` under `sudo`, allow your terminal app in System
Settings › Privacy & Security › App Management. If MinKNOW then fails to start, put the
`.orig` copy back. A MinKNOW update can restore the default, so check again after one.

## On the command line

```bash
mrnaspect-realtime --mode bam --input-dir /path/to/run --reference ref.fasta --arm m1psi \
    --output-dir ./out
```

- `--mode pod5` or `--mode bam` must be given. On a barcoded run, `--barcode barcode01`
  follows one barcode.
- **After a hard kill**, `mrnaspect --output-dir <the run's folder> --resume-from-snapshot`
  renders a report from the last snapshot.
- **To continue** a stopped or crashed run, start it again with the same `--output-dir`
  and `--output-name` (or `--continue-latest`). Files already analysed are not basecalled
  again. A run continues only with the same input folder, mode, reference and model.

## Try it without a sequencer

With the demo data (see the README), there are two ways:

- **MinKNOW playback.** Add a simulated MinION device in MinKNOW and play the bulk FAST5
  file back, with the POD5 interval set as above.
- **Replay by mRNAspect.** The POD5 files are replayed into a new run folder, one every
  180.6 s, and followed like a live run. The reads are basecalled as they arrive, so
  dorado-server is needed:

  ```bash
  mrnaspect-realtime --mode pod5 --mimic-source-dir /path/to/pod5_files \
      --input-dir ./replay_run --output-dir ./out --config run_config.yaml \
      --reference Run1_QK_HA_IVT.fa --basecall-model /path/to/m1psi_tuned_v1
  ```

  `--mimic-speed 2` halves every interval, and `--mimic-limit N` plays only the first N
  files.
