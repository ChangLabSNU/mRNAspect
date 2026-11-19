"""Startup guards on the real-time CLI: refuse early, with the fix in the message.

Each of these exists because the alternative is not an error but a plausible-looking run:
a bam-mode config fails once per POD5 minutes apart; a mistyped input dir watches an empty
path for twenty hours; `--mimic-*` with no source silently runs no mimic at all; and a
reused drill folder ingests a previous drill's POD5s as if this run had produced them.
"""

from __future__ import annotations

from pathlib import Path

from mrnaspect.realtime.main import _dirty_mimic_target, main


def _cfg_file(tmp_path: Path, mode: str = "pod5") -> Path:
    """A minimal user config.

    Carries a `reference` because the shipped config stopped answering that key on
    2026-09-19 (there is no default construct — see `load_config`). A placeholder path is
    enough: every guard under test fires in `main()` BEFORE `RealtimePipeline` is built,
    and it is that constructor, not the config load, which checks the file exists. Without
    this each test would trip the reference error first and never reach the guard it is
    supposed to be testing.
    """
    p = tmp_path / "cfg.yaml"
    p.write_text(f"mode: {mode}\nreference: /placeholder/ref.fasta\n", encoding="utf-8")
    return p


def _argv(tmp_path: Path, cfg: Path, **over) -> list[str]:
    argv = ["--input-dir", str(over.pop("input_dir", tmp_path / "run")),
            "--output-dir", str(tmp_path / "out"), "--config", str(cfg)]
    for flag, value in over.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return argv


# ---------------------------------------------------------------------------
# the dirty-target check, as a function
# ---------------------------------------------------------------------------

def test_a_clean_folder_is_accepted(tmp_path: Path):
    (tmp_path / "pod5").mkdir()
    assert _dirty_mimic_target(tmp_path) is None


def test_a_missing_folder_is_accepted(tmp_path: Path):
    """The mimic creates it; "not there yet" is the normal case."""
    assert _dirty_mimic_target(tmp_path / "nope") is None


def test_leftover_pod5s_are_refused(tmp_path: Path):
    (tmp_path / "pod5").mkdir()
    for i in range(3):
        (tmp_path / "pod5" / f"old_{i}.pod5").write_bytes(b"x")
    msg = _dirty_mimic_target(tmp_path)
    assert msg is not None
    assert "3 POD5" in msg
    # the consequence, not just the fact — this is the bit that made --mimic-limit look broken
    assert "Sealing sweeps the directory" in msg


def test_a_leftover_run_end_marker_alone_is_refused(tmp_path: Path):
    """A stale marker ends the run on the first poll: the drill never reaches LIVE."""
    (tmp_path / "pod5").mkdir()
    (tmp_path / "final_summary_old.txt").write_text("x")
    msg = _dirty_mimic_target(tmp_path)
    assert msg is not None and "1 run-end marker" in msg


# ---------------------------------------------------------------------------
# the guards as the CLI applies them
# ---------------------------------------------------------------------------

def test_bam_mode_is_refused_before_any_input_is_touched(tmp_path: Path, capsys):
    (tmp_path / "run").mkdir()
    code = main(_argv(tmp_path, _cfg_file(tmp_path, mode="bam")))
    assert code == 2
    assert "requires mode=pod5" in capsys.readouterr().out


def test_a_missing_input_dir_is_refused(tmp_path: Path, capsys):
    code = main(_argv(tmp_path, _cfg_file(tmp_path)))
    assert code == 2
    assert "does not exist" in capsys.readouterr().out


def test_mimic_flags_without_a_source_are_refused(tmp_path: Path, capsys):
    (tmp_path / "run").mkdir()
    code = main(_argv(tmp_path, _cfg_file(tmp_path), mimic_speed=60))
    assert code == 2
    out = capsys.readouterr().out
    assert "without --mimic-source-dir" in out and "mimic_speed" in out


def test_a_reused_drill_folder_is_refused(tmp_path: Path, capsys):
    """The source dir is never opened here, so it need not be the recorded replay.

    This used to point at a replay directory on one lab filesystem behind a skipif, and
    therefore did not run anywhere but this machine. The guard fires on the *target*
    before `Batch.from_dir` ever reads the source (`main()` only tests that
    --mimic-source-dir was given), so the real replay bought nothing but a silent skip.
    """
    run = tmp_path / "run"
    (run / "pod5").mkdir(parents=True)
    (run / "pod5" / "stale_0.pod5").write_bytes(b"x")
    code = main(_argv(tmp_path, _cfg_file(tmp_path),
                      mimic_source_dir=tmp_path / "recorded_source", input_dir=run))
    assert code == 2
    assert "is not empty" in capsys.readouterr().out


def test_a_bad_reference_exits_1_as_offline_does(tmp_path: Path, capsys):
    """An unusable reference is a run with no verdict (1), as in the offline driver."""
    bad = tmp_path / "bad.fa"
    bad.write_text("no records here\n")
    (tmp_path / "run").mkdir()
    (tmp_path / "model").mkdir()
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"mode: pod5\nreference: {bad}\nbasecall:\n  model: {tmp_path / 'model'}\n",
                   encoding="utf-8")
    code = main(_argv(tmp_path, cfg))
    assert code == 1
    assert "no FASTA records" in capsys.readouterr().out


def test_the_queue_depth_help_names_the_shipped_default(capsys):
    """The help gave the code's fallback (4); the shipped config sets the default."""
    import pytest
    import yaml

    from mrnaspect.main import _SHIPPED_CONFIG

    shipped = yaml.safe_load(_SHIPPED_CONFIG.read_text())["realtime"]["max_queue_depth"]
    with pytest.raises(SystemExit):
        main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert f"({shipped} in the shipped config)" in text
