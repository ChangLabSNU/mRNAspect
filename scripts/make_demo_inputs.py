#!/usr/bin/env python
"""Split the test fixture BAM into several smaller ones, for watching a run progress.

Why this is a script and not committed files. The fixture BAM is already in the
repository; N slices of it are the same reads a second time, and 850 KB of derived binary
in git ages into something nobody can tell apart from real test data. Generating them
takes under a second.

What it is for. `mrnaspect.main` processes its input files ONE AT A TIME — basecall, align,
ingest, refresh the instrument panel, write `latest_snapshot.json`, and render an interim
CoA every `--interim-every-n` inputs. With a single input that whole loop runs once and is
over before anything can be watched. With six it is visible: the GUI polls
`latest_snapshot.json` and the panel moves as each slice lands.

What it is NOT. This does not make real-time mode work on BAMs. `mrnaspect-realtime` is gated
to `mode: pod5` on purpose (`realtime/main.py`, "the watcher releases *.pod5
unconditionally"), and the offline driver globs its input set ONCE at startup, so files
dropped in afterwards are not picked up. This shows the per-input loop, not file arrival.

usage: python scripts/make_demo_inputs.py [--n 6] [--out DIR] [--bam PATH]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pysam

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BAM = REPO / "mrnaspect" / "tests" / "data" / "small_aligned.bam"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bam", type=Path, default=DEFAULT_BAM)
    ap.add_argument("--n", type=int, default=6, help="number of slices (default 6)")
    ap.add_argument("--out", type=Path, default=Path("demo_inputs"),
                    help="output directory (created; must be empty of *.bam)")
    args = ap.parse_args()

    if args.n < 1:
        raise SystemExit(f"--n must be >= 1, got {args.n}")
    if not args.bam.exists():
        raise SystemExit(f"source BAM not found: {args.bam}")

    args.out.mkdir(parents=True, exist_ok=True)
    # Refuse to add to a directory that already holds inputs: the offline driver globs
    # the whole directory, so a leftover slice from an earlier run with a different --n
    # would silently join this one and change every number.
    existing = sorted(args.out.glob("*.bam"))
    if existing:
        raise SystemExit(
            f"{args.out} already contains {len(existing)} *.bam file(s). The driver globs "
            f"the whole directory, so mixing runs would change the numbers. Remove them "
            f"first: rm {args.out}/*.bam")

    src = pysam.AlignmentFile(str(args.bam), "rb")
    reads = list(src.fetch(until_eof=True))
    if not reads:
        raise SystemExit(f"{args.bam} contains no records")

    # Contiguous blocks, not round-robin: consecutive records share a reference and a
    # position range, so a block looks like what a sequencer actually delivers over time
    # rather than a uniform sample of the whole run.
    per = -(-len(reads) // args.n)          # ceil
    written = []
    for i in range(args.n):
        chunk = reads[i * per:(i + 1) * per]
        if not chunk:
            break
        # Zero-padded so `natural_sorted` and a plain `ls` agree for any n.
        path = args.out / f"demo_{i + 1:02d}.bam"
        with pysam.AlignmentFile(str(path), "wb", header=src.header) as out:
            for r in chunk:
                out.write(r)
        written.append((path, len(chunk)))

    print(f"{len(reads)} reads from {args.bam.name} -> {len(written)} file(s) in {args.out}")
    for path, n in written:
        print(f"  {path.name}  {n:>4} reads  {path.stat().st_size / 1024:>6.0f} KB")
    print()
    if args.bam == DEFAULT_BAM:
        # Said here rather than left to be discovered: the whole point is to WATCH, and
        # the fixture is too small to watch. Measured: 166 reads finish in about 2 s,
        # while 21k reads per slice take 13-21 s each and the panel visibly moves.
        print("NOTE: the fixture's 166 reads process in ~2 s in total — too fast to watch.")
        print("      Point --bam at a larger aligned BAM to see the panel move.")
        print()
    print("Watch the loop run (the GUI polls latest_snapshot.json as each lands):")
    print(f"  mrnaspect --input-dir {args.out} \\")
    print(f"        --reference {REPO / 'mrnaspect/tests/data/curlcake_ref.fasta'} \\")
    print("        --output-dir <out> --mode bam --min-reads 1 --interim-every-n 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
