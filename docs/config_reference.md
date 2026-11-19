# run_config.yaml — why the values are what they are

`run_config.yaml` carries one line per setting. The measurements and decisions behind
them live here, so the file stays readable.

## reference

No default, and the absence is the feature. A valid FASTA of the **wrong** construct maps
0.00% and reads exactly like a dead flow cell — measured 0/19,992 on IVT_260407 against
the curlcake reference this line used to hold. The pipeline refuses to start.

## mode, min_reads, on_error

`min_reads` is the floor below which the report says it cannot diagnose, not a filter: the
reads are still analysed. Interims share the same floor — there is no separate
`interim_min_reads`.

`min_reads_per_construct` (100) decides which constructs get a section and figures of
their own; one with fewer assigned reads is listed instead, with its read count and its
MAPQ 0 share. A display rule, not a filter and not a criterion: its reads still count in
every whole-run result, and `qc_result.json` keeps every construct. It exists because a
panel reference holds constructs the sample may not, and the aligner puts a few reads on
them anyway: on a test input carrying six of run 1's fifteen constructs, six others got
1–12 reads each, and each took a section and every per-construct figure.

`on_error: skip` keeps a run alive past one unreadable input; `abort` stops. Either way
the skipped inputs are named in the report, because a POD5 that failed to basecall
contributes nothing to any module and its absence otherwise looks like it never existed.

## basecall.emit_moves

Off. The move table (`mv` tag) maps the signal onto the called bases; signal-level tools
read it, mRNAspect does not. It is most of an aligned record: on 50,000 stock m1Ψ reads it
was 87.6% of the aligned SAM text, and the align step took 36.6 s with it and 19.0 s
without (minimap2 on 8 threads), the BAM 202 MB and 98 MB. The cost on the basecall side
was not measured. A change applies to POD5s basecalled after it: a continued run reuses
its cached basecalls as they were. In both modes it also decides whether the aligned BAM
carries `mv`: BAMs basecalled elsewhere with `--emit-moves` have it on every read, and it
is left behind unless this is on.

## basecall.server

`device` is never inferred from the platform. "macOS means metal" was challenged on
2026-10-05 and the challenge was right, so it stays null and must be stated.

`min_free_gpus_remaining` and `free_gpu_mib` are lab etiquette made explicit rather than
hardcoded: shared GPUs, and a machine with one card is not misconfigured.

dorado-server is ONT-proprietary and cannot be bundled; models are installed by the user
into no predictable location. Both are user-supplied by design.

## align.minimap2_flags

`k7 w5` is a **locked methodological parameter** (CLAUDE.md rule #5), validated on QK/HA:
the short m1Ψ constructs need sensitive small-seed settings. `-y` is not optional — it
carries the basecaller tags, `pt` among them, into the SAM, and poly(A) has nothing to
read without it. `-k14` belongs to the production workflow's primary pass over longer
references, not here.

Both modes align with these flags, bam mode included since 2026-11-10 (an alignment
already in a BAM is discarded), and both stop before they start (exit 2) when the flags
lack `-a` or `-y`. Measured on 270 reads: without `-a` minimap2 writes PAF and every input
fails at `samtools sort`; without `-y` all 270 align and none carries `pt` or `RG`, with
nothing to say so.

## align.threads

minimap2's thread count (`-t`). Null, the shipped value, means every CPU the process may
use (its affinity where the OS reports one). minimap2's own default is 3 threads, and
since bam mode aligns every input, alignment is a large part of a bam-mode run: on run 1's
28,599 held-out reads it took 24.09 s at 3 threads, 7.16 s at 16 and 4.76 s at 72, with
the same mapped reads and identity each time. The thread count does not change an
alignment. On a shared machine, set a number. A `-t` written into
`align.minimap2_flags` decides instead when this is null; both at once stop the run
before it starts.

## modules

Read by no code. The engine's set is the `MODULES` tuple in `main.py`, which is what
provenance records. This list is for a human and must be kept in step by hand.

## module_params

Per-module settings, absent by default. Each key falls back to the module's own default on
its own, and a key a module does not have is **refused**, not ignored: a misspelt setting
must not read as applied. The run records every setting it was given.

Meant to be changed by the operator:

| setting | default | what it does |
| --- | --- | --- |
| `consensus.mismatch_threshold` | 0.5 | A position is flagged when the fraction of its covering reads that do not carry the reference base (a deletion counts) is at or above this; in (0, 1]. The default flags where at most half the reads carry the reference: below a majority, nanopore basecalling error dominates (at 0.1, about half the positions of a stock-basecalled m1Ψ construct flag). The report's per-position figure states the value used. |
| `polya.target_length` | none | The construct's designed poly(A) length (nt). Without it "short" is undefined and the short-tail rate is not measured. Tails are compared on the corrected scale when the correction applies, otherwise as called (`short_tail.judged_on`). |
| `polya.short_fraction` | none | A tail is short below `target_length` × this; in (0, 1]. No default: what counts as short is the manufacturer's specification, so with `target_length` alone no rate is given. |
| `polya.short_threshold` | none | An absolute short-tail cutoff (nt), used instead of the fraction. |
| `polya.target_lengths` | none | Per-construct designed lengths, `{construct: nt}`, in place of `target_length` for the constructs named. A name the reference lacks stops the run. With any set, no whole-run short-tail rate is given: it would mix designs. |
| `error_hotspot.hotspot_top_n` | 20 | How many of the highest-error positions the ranking lists. |
| `mapq.low_mapq_threshold` | 10 | Below this a read's assignment to its construct is counted as weak. |

Changeable, but they define the method, so a change makes a different measurement:

| setting | default | why it is fixed |
| --- | --- | --- |
| `consensus.min_depth` | 50 | A locked methodological parameter: the flagged positions and the per-position mismatch rate are reported only at positions this deep. Identity and its substitution, deletion and insertion rates are not gated by it: they are scored at every position with depth > 0 and a reference base other than N, because thinly covered positions tend to be the error-prone ends, and a gate would bias identity up and shift it as positions cross the gate mid-run. |
| `error_hotspot.min_depth`, `error_hotspot.hotspot_min_depth` | 50, 50 | Positions are reported (per position and in the mean and max rates) from depth 50, as in the consensus module, and ranked from depth 50: a rate's sampling error falls as 1/√depth. A run below 50x therefore shows no error profile; lower `min_depth` to see one. |
| `terminal_reach.full_length_5p_max_short_nt` | 50 | A read is full length when its alignment starts at most this many nt from the reference's 5' end. Direct RNA reads rarely include a molecule's last few 5' bases, and sequence in the reference upstream of the transcript (a promoter, say) uses up part of the allowance. The full-length ratio moves strongly with this value; the report states the one used. |

Not a setting: `polya.minimap2_flags` is filled from `align.minimap2_flags` by the run
itself, whatever the config says. The histogram keys (`run_summary.histogram`,
`run_summary.length_histogram`, `polya.histogram`, `terminal_reach.position_histogram_bins`,
`terminal_reach.joint_histogram_bins`) change only how distributions are binned; reads
outside a range are counted, not dropped.

Poly(A) is drawn at 1 nt bins centred on the integers (`polya.histogram`: −0.5 to 499.5,
500 bins), with a curve through them: a Gaussian kernel of fixed standard deviation
`polya.smoothing_bandwidth_nt` (default 2; `null` for no curve). Neither changes a reported
number: the poly(A) quartiles and the short-tail count come from one count per integer
`pt`, not from the bins. `pt` is a whole number of nt, so bins of any other width are
refused (they would draw a comb that is not in the reads), and so is a bandwidth below
the bin width. The bandwidth is fixed rather than set from the read count, so the curve
does not sharpen as reads arrive during a run.

## analysis.max_constructs

A resource ceiling, not a methodological parameter: changing it cannot change a reported
number, only how much work one run may do. Each construct allocates O(ref_length) pileup
arrays (~56 bytes/position), so an unbounded count against a transcriptome reference is an
OOM at hour 14. Exceeding it **raises** rather than silently tracking the first N — a run
spread over more constructs than configured is answering a different question than the
reference describes.

## sample

Everything here is what a person declared. None of it is verifiable by this pipeline,
which is why the report prints it apart from the block read out of MinKNOW's own run
summary. An unset field gets no row at all; an em dash would read as "we looked and it is
blank".

### sample.arm / sample.arms

The poly(A) ruler correction's additive terminus term applies on a fully m1Ψ-substituted
construct and is **zero** on a canonical-U one. Nothing in a BAM says which an arm is — it
is an IVT choice. There is no safe default in either direction: assume `U` on an m1Ψ run
and the tail is under-reported; assume `m1psi` on a canonical run and it is over-reported
by up to ~7 nt at U12=6. Undeclared therefore withholds.

Per construct, with a run-level default, because the arm genuinely varies within a run:
run 1 pooled 15 constructs with no barcoding, and
without barcodes a construct carries only one arm in a pool.

`arms` states each exception's value rather than meaning "the opposite of `arm`". A bare
list would couple every exception to the default, so re-running the same panel as a
canonical control — one character — would silently invert all of them.

From the window the run is always declared (2026-10-28): an m1Ψ box by the
reference, checked by default because the product is for m1Ψ-substituted mRNA, sends
`--arm m1psi`, or `--arm U` unchecked. Like every typed field it beats the config's
`sample.arm`, so a config that names one sets the box when it is loaded; the config's
`sample.arms` still names the constructs that differ. The command line and a config alone
still withhold when nothing is declared.

An `arms` name that is not a contig in the reference refuses the run. Tracks are created
lazily from `read.reference_name`, so a typo would match nothing, leave the construct on
the default, and produce a corrected tail that looks fine and is wrong.

The basecaller is **not** consulted. The model is observed from the RG tag and selects
which constants apply; the arm is declared and selects whether the terminus term applies.
Coupling them would discard stock × m1psi — a manufacturer running MinKNOW's stock model
on an m1Ψ product, the commonest production case.

## realtime.max_queue_depth

Decimation is **stride-based** (`index % stride == 0`), not random, and off by default. A
random sample would make two runs over the same data disagree.

## Removed settings

`consensus_mode` and `basecaller_model` were removed 2026-10-23. Every reference position
is scored now, U included. The basecaller behind a BAM is read
from its `@RG` `DS:basecall_model=`, which is evidence rather than a declaration, and the
report names it in both modes by its released name (an earlier name of the same model
resolves to it).
