"""Both modes refuse minimap2 flags without -a or -y, before anything runs.

bam mode was exempt until 2026-11-10, when it began aligning every BAM itself.

Measured 2026-11-03 on the 270 reads of the QK/HA fixture: without -a, minimap2 writes
PAF and ``samtools sort`` stops with "failed to read header", so every POD5 fails;
without -y, all 270 reads align and none carries pt or RG, so poly(A) and the model
check have nothing to read, and nothing says so.

Which letters of a cluster such as ``-ax`` count is checked against minimap2 itself.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mrnaspect.main import OfflinePipeline, load_config, main
from mrnaspect.tests.conftest import dorado_stubs, pod5_ready
from mrnaspect.wrappers.aligner import missing_minimap2_flags

SHIPPED = "-ax map-ont -k7 -w5 --secondary=no -y"

# flags -> the required flags they leave out
CASES = {
    SHIPPED: [],
    "-x map-ont -k7 -w5 --secondary=no -y": ["-a"],
    "-ax map-ont -k7 -w5 --secondary=no": ["-y"],
    "-x map-ont -k7 -w5": ["-a", "-y"],
    "-a -y -x map-ont -k7 -w5": [],
    "-ay -x map-ont -k7 -w5": [],
    "-yax map-ont -k7 -w5": [],
    "-Hay -x map-ont -k7 -w5": [],
    "-xmap-ont -a -y -k7 -w5": [],
    # -t takes "8y" as its value, so y is not a flag here.
    "-ax map-ont -k7 -w5 -t8y": ["-y"],
}


@pytest.mark.parametrize("flags, missing", CASES.items())
def test_the_required_flags_a_string_leaves_out(flags: str, missing: list[str]) -> None:
    assert missing_minimap2_flags(flags) == missing


@pytest.fixture(scope="module")
def minimap2_probe(tmp_path_factory):
    """Run minimap2 on one read whose FASTQ comment holds a tag; report what it wrote."""
    minimap2 = shutil.which("minimap2")
    if minimap2 is None:
        pytest.skip("minimap2 not on PATH")
    d = tmp_path_factory.mktemp("minimap2_probe")
    rng = random.Random(0)
    ref = "".join(rng.choice("ACGT") for _ in range(2000))
    (d / "ref.fa").write_text(f">c\n{ref}\n", encoding="utf-8")
    read = ref[500:1100]
    (d / "r.fq").write_text(f"@r pt:i:42\n{read}\n+\n{'I' * len(read)}\n", encoding="utf-8")

    def probe(flags: str) -> tuple[bool, bool]:
        out = subprocess.run([minimap2, *flags.split(), str(d / "ref.fa"), str(d / "r.fq")],
                             capture_output=True, text=True, check=True).stdout
        writes_sam = any(line.startswith("@SQ") for line in out.splitlines())
        return writes_sam, "pt:i:42" in out

    return probe


@pytest.mark.parametrize("flags, missing", CASES.items())
def test_the_verdict_matches_what_minimap2_does(minimap2_probe, flags: str,
                                                missing: list[str]) -> None:
    writes_sam, carries_tag = minimap2_probe(flags)
    assert writes_sam == ("-a" not in missing)
    if writes_sam:
        assert carries_tag == ("-y" not in missing)


def _pod5_config(reference: Path, flags: str) -> dict:
    cfg = pod5_ready(load_config(
        None, {"reference": str(reference), "mode": "pod5", "min_reads": 1}))
    cfg["align"]["minimap2_flags"] = flags
    return cfg


@pytest.mark.parametrize("flags, named", [
    ("-x map-ont -k7 -w5 --secondary=no -y", "-a"),
    ("-ax map-ont -k7 -w5 --secondary=no", "-y"),
    ("-x map-ont -k7 -w5 --secondary=no", "-a and -y"),
])
def test_pod5_mode_stops_on_flags_without_a_or_y(
    tmp_path: Path, curlcake_ref: Path, flags: str, named: str
) -> None:
    with pytest.raises(ValueError, match=f"lacks {named}"):
        OfflinePipeline(tmp_path / "in", tmp_path / "out", _pod5_config(curlcake_ref, flags))


def test_the_shipped_flags_pass(tmp_path: Path, curlcake_ref: Path) -> None:
    OfflinePipeline(tmp_path / "in", tmp_path / "out", _pod5_config(curlcake_ref, SHIPPED))


def test_bam_mode_aligns_and_so_checks_too(tmp_path: Path, curlcake_ref: Path) -> None:
    cfg = load_config(None, {"reference": str(curlcake_ref), "mode": "bam", "min_reads": 1})
    cfg["align"]["minimap2_flags"] = "-x map-ont"
    with pytest.raises(ValueError, match="lacks -a and -y"):
        OfflinePipeline(tmp_path / "in", tmp_path / "out", cfg)


def test_the_cli_exits_2_and_names_the_flag(tmp_path: Path, curlcake_ref: Path,
                                            capsys) -> None:
    stubs = dorado_stubs()
    config = tmp_path / "run.yaml"
    config.write_text(yaml.safe_dump({
        "basecall": {"model": stubs["model"], "server": {"client": stubs["client"]}},
        "align": {"minimap2_flags": "-ax map-ont -k7 -w5 --secondary=no"},
    }), encoding="utf-8")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "x.pod5").write_bytes(b"")
    code = main(["--config", str(config), "--input-dir", str(in_dir),
                 "--output-dir", str(tmp_path / "out"), "--reference", str(curlcake_ref),
                 "--mode", "pod5", "--min-reads", "1"])
    result = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "lacks -y" in result["error"]
