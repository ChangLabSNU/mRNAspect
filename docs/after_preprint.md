# After the preprint

Improvements decided or found while preparing the preprint and deliberately left until after
it. Each says why it waits and what it needs first. This is a record, not a schedule: work
toward the preprint itself is tracked elsewhere.

## A warning when the reference looks wrong

*Decided 2026-10-29.*

**What.** A notice on a construct whose reads agree with each other and not with its
reference: positions where the consensus base is not the reference's and the consensus
purity is at least 0.9. Read as "the reference may not be this construct's sequence", which
today shows only as flagged positions near 1.0 in the per-position figure and as lower
identity — a reference problem that reads as a sample-quality problem.

**What exists.** `mrnaspect/analysis/modules/consensus.py` already lists the positions where at
most half the reads carry the reference base (`mismatch_sites`), each with its base counts,
from which the consensus base and its purity follow. Missing are the rule and its validation.

**Why it waits.** One confound has to be measured first. Under a stock basecaller an m1Ψ
position is read as C often enough that some U positions take a C consensus, which is the
same signature. So U→C on constructs declared `m1psi` is excluded from the count, and before
the notice ships:

- the false alarms are counted on runs whose references are right — run 1 and run 2, stock
  and the fine-tuned model;
- detection is checked by editing a reference on purpose and seeing the notice fire.

## A construct missing from the reference

*Not planned. The operator's responsibility, stated in the README (2026-10-29).*

The reads of a construct absent from the reference align to its nearest relative. At the
positions where the two differ, the alternative base sits at the missing construct's share
of the reads, not near 1.0. Telling that from basecalling error needs read-level linkage —
the same reads carrying all the alternatives — and Core keeps per-position counts only. A
stock basecaller's m1Ψ miscalls also make a mid-frequency pattern at many positions.

## Hotspots against the basecaller's own expected error

*Deferred 2026-10-28.*

Since then the deep-enough positions are ranked by observed error, not flagged, and
`NULL_TABLES` in `mrnaspect/analysis/modules/error_hotspot.py` is empty. A null for the
fine-tuned model, one per arm, needs held-out data. Measured that day:

- In the October run the held-out constructs cover all 1,024 centred 5-mers in each arm.
- The run's evaluation BAM carries no barcode, so its arms cannot be separated until the
  read-to-barcode assignment is joined in.
- Most of those 5-mers come from the QK family, whose contexts match the trained QK
  constructs'. Held out by construct, not by context — a null built on them should say so.

## Barcoded runs

*Deferred 2026-10-22.*

Core splits a run by reference contig, not by barcode, so two batches of one construct are
pooled into one result (see the README). Demultiplexing with ONT's own barcodes could be
added later; it is not meaningful at this stage.
