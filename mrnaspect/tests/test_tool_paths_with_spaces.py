"""The wrappers run their tools from a path that holds a space.

Inside the desktop app minimap2 and samtools are found by absolute path, and the app may
sit in a folder such as ``Lab Tools``. The align script used those paths unquoted, so the
shell ran the part before the first space and no BAM was aligned, whatever the data's own
path was. Spaces in the data path alone were always handled.
"""

from __future__ import annotations

import re
from pathlib import Path

import pysam
import pytest

from mrnaspect.wrappers._shell import resolve_tool
from mrnaspect.wrappers.aligner import shell_align

SHIPPED = "-ax map-ont -k7 -w5 --secondary=no -y"
_WRAPPERS = Path(__file__).resolve().parents[1] / "wrappers"


def _staged(parent: Path, bam: Path, prefix: str = "x") -> Path:
    out = parent / "cache"
    (out / f"basecall_{prefix}").mkdir(parents=True)
    (out / f"basecall_{prefix}" / bam.name).symlink_to(bam)
    return out


def _n_primary(bam: Path) -> int:
    with pysam.AlignmentFile(str(bam), check_sq=False) as fh:
        return sum(1 for r in fh.fetch(until_eof=True)
                   if not (r.is_secondary or r.is_supplementary))


def _tools_under(folder: Path) -> dict[str, str]:
    """The environment's own minimap2 and samtools, reached through ``folder``."""
    folder.mkdir(parents=True)
    tools = {}
    for name in ("minimap2", "samtools"):
        link = folder / name
        link.symlink_to(resolve_tool(name, None))
        tools[name] = str(link)
    return tools


def test_tools_in_a_folder_with_a_space(tmp_path, variant_bam, qk_ha_ref) -> None:
    tools = _tools_under(tmp_path / "Lab Tools" / "mRNAspect.app" / "bin")
    out = _staged(tmp_path / "RunA", variant_bam)
    bam = shell_align("x", qk_ha_ref, out, align_flags=SHIPPED, **tools)
    assert _n_primary(bam) == _n_primary(variant_bam) > 0
    log = (out / "align_x.log").read_text()
    assert "No such file or directory" not in log and "command not found" not in log


def test_data_in_a_folder_with_a_space(tmp_path, variant_bam, qk_ha_ref) -> None:
    """The control: this always worked, and still does."""
    tools = _tools_under(tmp_path / "LabTools" / "bin")
    out = _staged(tmp_path / "Run C", variant_bam)
    bam = shell_align("x", qk_ha_ref, out, align_flags=SHIPPED, **tools)
    assert _n_primary(bam) == _n_primary(variant_bam) > 0


#: A tool's path is one argument wherever a wrapper runs it. Flag strings (ALIGN_FLAGS,
#: THREAD_FLAGS, COUNT_THREADS, MOVES_FLAG) are split on purpose and are not listed. The
#: default in an assignment, VAR=${VAR:-name}, is not a use.
_TOOL_VARS = {"run_align.sh": ("SAMTOOLS", "MINIMAP2"),
              "run_basecall.sh": ("CLIENT",),
              "start_server.sh": ("SERVER",)}


@pytest.mark.parametrize("script", sorted(_TOOL_VARS))
def test_no_wrapper_runs_a_tool_unquoted(script: str) -> None:
    text = "\n".join(line for line in (_WRAPPERS / script).read_text().splitlines()
                     if not line.lstrip().startswith("#"))
    for var in _TOOL_VARS[script]:
        bare = re.findall(rf'(?<!")\$\{{?{var}\}}?(?![A-Za-z_":])', text)
        assert not bare, f"{script}: ${var} is used unquoted {len(bare)} time(s)"
        assert re.search(rf'"\${var}"', text), f"{script}: ${var} is never run"
