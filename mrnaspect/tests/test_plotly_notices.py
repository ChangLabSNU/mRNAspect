"""Whatever carries the plotly bundle carries the licence notices of what is in it.

The bundle is plotly.js (MIT) with 66 other packages inside it. Their licences (MIT, ISC,
BSD, Zlib) ask that the copyright notice and the terms go with every copy. The bundle
keeps a one-line mention of seven of them: for ieee754, "BSD-3-Clause License" and a
name, without the copyright holder (Fair Oaks Labs, Inc.) or the conditions.

Three things hand the bundle on, and each is opened here as its recipient gets it: the
wheel, a report (one HTML file), and the desktop app's notices file.
``scripts/vendor_plotly_notices.py`` writes the texts, from the packages plotly.js was
built with; these tests hold the result to the bundle.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from mrnaspect.main import main
from mrnaspect.reporting import figspec
from mrnaspect.reporting.figspec import PLOTLY_BUNDLE, PLOTLY_BUNDLE_SHA256, PLOTLY_NOTICES

_ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(figspec.__file__).resolve().parent / "assets"
RULE = "-" * 78

IEEE754 = ("Copyright 2008 Fair Oaks Labs, Inc.",
           "Redistributions of source code must retain the above copyright notice",
           "Redistributions in binary form must reproduce the above copyright notice",
           "Neither the name of the copyright holder nor the names of its contributors")
PLOTLY = ("Copyright (c) 2016-2024 Plotly Technologies Inc.",
          "Permission is hereby granted, free of charge, to any person obtaining a copy",
          "The above copyright notice and this permission notice shall be included in all")


def _whole(text: str) -> list[str]:
    """What of the two licences named in the report is NOT in ``text``."""
    flat = " ".join(text.split())
    return [line for line in IEEE754 + PLOTLY if line not in flat]


def _sections(text: str) -> dict[str, str]:
    """Package name -> its section of the notices file."""
    parts = re.split(rf"^{RULE}\n(.+)\n{RULE}\n", text, flags=re.M)
    return {parts[i].rsplit(" ", 1)[0]: parts[i + 1] for i in range(1, len(parts), 2)}


def _notices() -> str:
    return (ASSETS / PLOTLY_NOTICES).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------

def test_the_notices_are_written_for_the_bundle_that_is_vendored() -> None:
    head = _notices().split(RULE)[0]
    assert PLOTLY_BUNDLE in head and PLOTLY_BUNDLE_SHA256 in head
    assert PLOTLY_NOTICES == PLOTLY_BUNDLE.removesuffix(".min.js") + ".LICENSES.txt"


def test_plotly_s_terms_and_ieee754_s_are_there_in_full() -> None:
    assert _whole(_notices()) == []
    sections = _sections(_notices())
    assert "Fair Oaks Labs, Inc." in sections["ieee754"]
    assert "Plotly Technologies Inc." in sections["plotly.js"]


def test_every_package_the_bundle_names_has_its_own_section() -> None:
    bundle = (ASSETS / PLOTLY_BUNDLE).read_text(encoding="utf-8")
    block = bundle[bundle.rindex("/*! Bundled license information:"):]
    named = set(re.findall(r"^([@\w.-]+)/[\w./-]+\.js:$", block, re.M))
    assert len(named) >= 7 and "ieee754" in named
    sections = _sections(_notices())
    assert named <= set(sections), sorted(named - set(sections))
    for name in named:
        assert "itself carries" in sections[name], name


def test_every_section_gives_a_text_or_says_the_package_ships_none() -> None:
    sections = _sections(_notices())
    assert len(sections) > 60
    for name, body in sections.items():
        assert body.startswith("Licence: "), name
        has_text = re.search(r"^--- https://\S+", body, re.M)
        assert has_text or "the package ships none" in body, name
    without = [n for n, b in sections.items() if "the package ships none" in b]
    assert without == ["has-hover"], without


def test_the_copy_the_gui_serves_is_the_one_the_notices_are_for() -> None:
    gui = _ROOT / "gui" / "public" / PLOTLY_BUNDLE
    assert hashlib.sha256(gui.read_bytes()).hexdigest() in _notices()


# ---------------------------------------------------------------------------
# 1. The wheel
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> zipfile.ZipFile:
    """A wheel built from a copy of what the build reads, so the checkout stays clean."""
    src = tmp_path_factory.mktemp("wheel_src")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(_ROOT / name, src / name)
    shutil.copytree(_ROOT / "mrnaspect", src / "mrnaspect",
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    out = tmp_path_factory.mktemp("wheel_out")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--no-index", "-w", str(out), str(src)], capture_output=True, text=True)
    if proc.returncode != 0 and "No module named pip" in proc.stderr:
        pytest.skip("this environment has no pip to build a wheel with")
    assert proc.returncode == 0, proc.stderr[-2000:]
    built = list(out.glob("mrnaspect-*.whl"))
    assert len(built) == 1, [p.name for p in out.iterdir()]
    return zipfile.ZipFile(built[0])


def test_the_wheel_carries_the_bundle_with_its_notices_beside_it(wheel) -> None:
    names = wheel.namelist()
    assert f"mrnaspect/reporting/assets/{PLOTLY_BUNDLE}" in names
    beside = f"mrnaspect/reporting/assets/{PLOTLY_NOTICES}"
    assert beside in names, "an installed report could not embed the notices"
    assert _whole(wheel.read(beside).decode("utf-8")) == []


def test_the_wheel_carries_this_project_s_licence_with_its_holder(wheel) -> None:
    """Asked of a wheel built now, not of the source: what an installer receives."""
    ours = [n for n in wheel.namelist() if n.endswith(".dist-info/licenses/LICENSE")]
    assert len(ours) == 1, wheel.namelist()
    text = wheel.read(ours[0]).decode("utf-8")
    assert text == (_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Seoul National University" in text


def test_the_wheel_s_licence_files_include_the_bundle_s(wheel) -> None:
    licences = [n for n in wheel.namelist() if ".dist-info/licenses/" in n]
    assert any(n.endswith("/LICENSE") for n in licences), licences
    theirs = [n for n in licences if n.endswith(PLOTLY_NOTICES)]
    assert len(theirs) == 1, licences
    assert _whole(wheel.read(theirs[0]).decode("utf-8")) == []
    metadata = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
    declared = re.findall(r"^License-File: (.+)$", wheel.read(metadata).decode(), re.M)
    assert any(d.endswith(PLOTLY_NOTICES) for d in declared), declared


# ---------------------------------------------------------------------------
# 2. A report, as the one file it is handed on as
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def report_dir(tmp_path_factory, fixture_bam: Path, curlcake_ref: Path) -> Path:
    tmp = tmp_path_factory.mktemp("report")
    in_dir = tmp / "in"
    in_dir.mkdir()
    shutil.copy(fixture_bam, in_dir / "small_aligned.bam")
    code = main(["--input-dir", str(in_dir), "--output-dir", str(tmp / "out"),
                 "--output-name", "r", "--reference", str(curlcake_ref), "--mode", "bam",
                 "--min-reads", "1", "--min-reads-per-construct", "0"])
    assert code == 0
    return tmp / "out" / "r"


def _final_html(run: Path) -> str:
    """The page a finished run leaves: after the final render, the latest is the final."""
    return (run / "report_latest.html").read_text(encoding="utf-8")


def test_a_report_that_embeds_plotly_holds_the_notices(report_dir: Path) -> None:
    html = _final_html(report_dir)
    assert "plotly.js (cartesian - minified)" in html, "this report embeds no bundle"
    assert _whole(html) == [], "the report hands plotly on without its terms"
    assert 'class="third-party-notices"' in html
    # Every package's section, not only the two named in the report.
    for name in ("ieee754", "buffer", "@plotly/d3", "tinycolor2", "has-hover"):
        assert f"{RULE}\n{name} " in html, name


def test_the_notices_are_text_on_the_page_and_nothing_else(report_dir: Path) -> None:
    """Autoescaped: no licence text can close an element or open a script."""
    html = _final_html(report_dir)
    start = html.index('<details class="third-party-notices">')
    block = html[start:html.index("</details>", start)]
    inner = block[block.index("<pre>") + 5:block.rindex("</pre>")]
    assert "<" not in inner and len(inner) > 50_000


def test_the_pdf_holds_neither_plotly_nor_its_notices(report_dir: Path) -> None:
    """The printed report draws its figures as images; there is nothing to give terms for."""
    import pypdf  # not importorskip: without the reader this must fail, not skip
    pdfs = sorted(report_dir.glob("*.pdf"))
    assert pdfs, "the run wrote no PDF"
    text = "".join(page.extract_text() or "" for page in pypdf.PdfReader(pdfs[-1]).pages)
    assert "Generated by" in text, "the PDF's text could not be read"
    assert "Fair Oaks Labs" not in text and "Third-party software" not in text


def test_a_page_without_figures_embeds_neither() -> None:
    from mrnaspect.reporting.reporter import CoAReporter
    keys = CoAReporter._screen_figures([])
    assert keys["plotly_js"] == "" and keys["plotly_notices"] == ""


# ---------------------------------------------------------------------------
# 3. The desktop app's notices file
# ---------------------------------------------------------------------------

def _make_notices():
    spec = importlib.util.spec_from_file_location(
        "make_notices", _ROOT / "scripts" / "make_notices.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_notices"] = module
    spec.loader.exec_module(module)
    return module


def test_the_app_s_notices_give_the_texts_in_full() -> None:
    make = _make_notices()
    entries, missing = make.collect_page(_ROOT)
    plotly = [e for e in entries if e.name == "plotly.js"]
    assert len(plotly) == 1
    assert not [m for m in missing if "plotly" in m], missing
    written = make.render([], [], entries, [])
    assert _whole(written) == []
    assert f"{RULE}\nieee754 " in written and "Fair Oaks Labs, Inc." in written


def test_notices_written_for_another_bundle_are_refused(tmp_path: Path) -> None:
    """The kept file names its bundle by sha256; a different file makes the app's
    notices say INCOMPLETE instead of carrying the wrong texts."""
    make = _make_notices()
    root = tmp_path / "checkout"
    (root / "gui" / "public").mkdir(parents=True)
    (root / "gui" / "public" / PLOTLY_BUNDLE).write_text(
        "/**\n* plotly.js (cartesian - minified) v3.7.0\n* Licensed under the MIT license\n*/\n"
        "var Plotly = {};\n")
    kept = root / "mrnaspect" / "reporting" / "assets"
    kept.mkdir(parents=True)
    shutil.copy(ASSETS / PLOTLY_NOTICES, kept / PLOTLY_NOTICES)
    entries, missing = make.collect_page(root)
    assert not [e for e in entries if e.name == "plotly.js"]
    assert any("written for another file" in m for m in missing), missing


def test_the_bundle_check_reads_the_notices_of_the_built_app() -> None:
    """On the Mac, check_bundle.sh runs `make_notices.py --check` on the built app."""
    script = (_ROOT / "scripts" / "check_bundle.sh").read_text()
    assert "make_notices.py" in script and "--check" in script
