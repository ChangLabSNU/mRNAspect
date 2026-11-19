"""Core draws headless, and the environment it ships in must reflect that.

`matplotlib` the conda meta-package depends on `pyside6`, which pulls `qt6-main`,
`libclang` and `libllvm`: 186 MB packed, ~280 MB unpacked, a quarter of the environment —
for a process that calls `matplotlib.use("Agg")` before it draws anything and never opens
a window. The environment is what a shipped .app has to carry, so that quarter is paid by
every copy.

Two different claims, so two tests: that nothing pulls a GUI toolkit at RUN time, and
that nothing re-declares the meta-package that would put one back in the environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")


def test_rendering_a_figure_imports_no_gui_toolkit() -> None:
    """Agg is selected in figrender, but a dependency can import Qt on its own."""
    from mrnaspect.reporting import figrender, figspec

    figrender.render_spec(figspec.histogram_spec(
        [("s", {"bin_counts": [1, 2, 3], "bin_min": 0.0, "bin_width": 10.0,
                "n_bins": 3, "bin_max": 30.0, "n": 6}, figspec.CATEGORICAL[0])],
        x_title="x"))

    import matplotlib
    assert matplotlib.get_backend().lower() == "agg"
    loaded = {name.split(".")[0] for name in sys.modules}
    for toolkit in ("PySide6", "PySide2", "PyQt5", "PyQt6", "tkinter", "gi", "wx"):
        assert toolkit not in loaded, (
            f"{toolkit} was imported while drawing a figure. Core is headless; a GUI "
            f"toolkit on the import path is a dependency the shipped app has to carry.")


def test_the_environment_declares_matplotlib_base() -> None:
    """The packaging half of the same claim, and the one that actually costs the MB.

    A string check on purpose: the decision lives in pixi.toml, and re-solving with the
    meta-package would silently restore 186 MB that nothing imports.
    """
    pixi = Path(__file__).resolve().parents[2] / "pixi.toml"
    if not pixi.exists():  # installed wheel, no source tree
        pytest.skip("not a source checkout")
    text = pixi.read_text()
    assert "matplotlib-base" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        name = stripped.split("=", 1)[0].strip()
        assert name != "matplotlib", (
            "pixi.toml declares the matplotlib META-package, which depends on pyside6. "
            "Use matplotlib-base; Core sets the Agg backend and never opens a window.")
        assert name not in ("pyside6", "pyqt", "qt6-main"), f"GUI toolkit declared: {name}"
