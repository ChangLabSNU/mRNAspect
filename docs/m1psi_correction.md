# m1Ψ bias correction

N¹-methylpseudouridine (m1Ψ), which often replaces uridine in therapeutic mRNA, alters the
nanopore signal. Stock basecallers miscall it, and dorado underestimates the poly(A) tail
of m1Ψ RNA. mRNAspect addresses both: a basecaller fine-tuned on m1Ψ RNA, and a poly(A)
correction computed from the reference sequence.

## Fine-tuned basecaller

`m1psi_tuned_v1` is ONT's `rna004_130bps_hac@v5.3.0` fine-tuned with Bonito on 15 m1Ψ and
14 unmodified in vitro transcribed constructs, with public cDNA-IVT reads. On 10 m1Ψ
constructs held out from training it lowered the per-base error rate from 20.1% to 7.2%
(unmodified RNA: 3.0%), and on public Curlcake m1Ψ reads from 24.2% to 7.7%.

Any RNA004 basecalling model can be used with mRNAspect, but this one gives more
interpretable identity results for m1Ψ samples.

- **Availability.** The model will be deposited on Zenodo with its model card. Until
  then, it is available on request: open an issue in this repository.
- **Licence.** Oxford Nanopore Technologies Public License v1.0, for research purposes
  only.
- **Keep its folder name.** The basecaller writes the folder name into each read's read
  group (`RG`), and mRNAspect selects the poly(A) correction by it.

## Poly(A) correction

dorado converts the tail's signal duration into nucleotides using the samples per base of
the read body. m1Ψ RNA has more samples per base, so its tail is underestimated. mRNAspect
corrects each construct's tail lengths from its reference sequence: one term for the
reference's U fraction and, for m1Ψ constructs, one for the number of m1Ψ in the 12 nt
before the tail. Across eight constructs made in both versions, the correction narrowed
the difference between the m1Ψ and unmodified medians from up to 9.0 nt to within 1.8 nt.

The correction is applied when:

- the reads were basecalled with `rna004_130bps_hac@v5.3.0` or `m1psi_tuned_v1` (other
  models get uncorrected lengths);
- each construct's modification, m1Ψ or unmodified, is declared;
- the reference ends immediately before the poly(A) tail.

The report shows both the uncorrected and the corrected distribution, and flags a
reference outside the range the correction was fitted on. For m1Ψ RNA basecalled with the
stock model, keep the default alignment settings (`-k7 -w5 --secondary=no`): the
correction also accounts for the deleted and inserted bases counted under them.
