# Results and outputs

## The three quality attributes

Reported for each construct and for the whole run.

- **Per-base identity.** Share of aligned bases that match the reference; mismatches
  (substitutions and deletions) per position, and insertion events per 100 scored bases.
  Positions where at least half the reads disagree with the reference (depth ≥ 50) are
  flagged and listed.
- **Poly(A) tail length.** Distribution of dorado's per-read tail estimate (`pt`), and per
  construct a [corrected](m1psi_correction.md#polya-correction) distribution. With a
  designed tail length set, also the share of short tails.
- **Full length (5' reach).** Transcript completeness: the fraction of reads whose
  alignment starts within 50 nt of the reference's 5' end, with where reads start and end
  along the reference. Only the 5' end is judged, because sequencing starts at the 3'
  poly(A) tail: a read can lose its 5' end but not its 3' end. Extra 5' sequence in the
  reference lowers this fraction (a T7 promoter alone uses 17 of the 50 nt).

## Also reported

| Measurement | Scope | What it is |
|---|---|---|
| Mapping | construct | Reads mapped to each construct and the mapping rate, with unmapped reads as their own group. |
| Read length | run, construct | Distribution of basecalled read length. |
| Read quality | run | Distribution of the basecaller's mean quality score per read. |
| Mapping quality | run | Distribution of minimap2 MAPQ. A read that fits two constructs almost equally shows up as low MAPQ. |
| Coverage | construct | Mean depth and the share of the reference covered. |
| Sequencing information | run | Read end reason, pore activity and bias voltage over the run, from MinKNOW's own files when they are found with the input. |

## Which reads count

Construct-level results use *admitted reads*: reads whose primary alignment is to that
construct, on the forward strand, and that are neither chimeric (`SA` tag) nor split by
dorado (`pi` tag). Run-level read length, read quality and mapping rate include unmapped
reads. Rates are pooled (summed numerators over summed denominators). Run-level results
appear once 1,000 reads have mapped, and a construct's once 100 reads have mapped to it.

Reads are grouped by the construct they align to. Barcodes that carry the same construct
are therefore pooled into one result, and the reads of a construct missing from the
reference align to its closest relative and show up there as lower identity.

## Output files

Each run gets its own folder inside the output folder you choose. It holds:

- `qc_result.json`: every result, machine-readable, with its provenance: mRNAspect's
  source commit, the exact command, and the version of every module that ran.
- The QC report: an HTML report after every input file, and a final HTML and PDF report
  when the run is finished. During a live run, open `report_latest.html`: it reloads
  itself every three minutes, and stops reloading once you click a figure, so a zoom is
  kept. The numbered `report_interim_<n>.html` copies hold no figures.
- `run.log`.

In the desktop app, the **Current run** page shows the same results in eight panels:
Overview, Read length, Poly(A), Quality, Constructs, Identity, Full length and Sequencing
information.
