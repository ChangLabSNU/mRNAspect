"""Pytest bootstrap: put the repo root on sys.path.

ONE entry, because the layout is FLAT: the importable package is ``mrnaspect/`` at the repo
root, so the single path that lets ``tests/`` and outside analysis helpers
resolve also makes ``import mrnaspect`` work.

Flat rather than ``src/mrnaspect/`` deliberately (2026-09-17). ~205 analysis and finetuning
scripts already insert the REPO ROOT on ``sys.path``; a package one level deeper under
``src/`` was invisible to every one of them, and the suite did not catch it because those
scripts are not in the suite. Putting the package where the path already points fixes that
class of breakage by construction instead of by editing 205 files.

pytest does not add rootdir to sys.path by default, which is why this file exists.
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
