"""Natural ordering for discovered input files.

``sorted()`` puts a MinKNOW run's ``_10`` before ``_2``, which scrambles acquisition
order. The final QC numbers do not depend on order, but three things do: interim reports
(a running view of the run so far), agreement between the offline and real-time drivers'
interim artifacts (the real-time driver sees files in arrival order), and progress logs
(``processed 3/400`` should mean the third file).

Digit runs compare numerically, so ``_2`` precedes ``_10`` regardless of zero-padding.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Union

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: Union[str, Path]) -> tuple:
    """Sort key that compares embedded digit runs numerically.

    ``re.split`` with a capturing group alternates text and digits, so every key has a
    ``str`` at even indices and an ``int`` at odd ones, which keeps any two keys
    comparable. The raw name is a separate second element, not appended to the parts
    (that would break the alternation and can raise ``TypeError``); it breaks
    zero-padding ties (``_02`` vs ``_2``) deterministically.
    """
    text = name.name if isinstance(name, Path) else str(name)
    parts = _DIGITS.split(text)
    return (tuple(int(p) if i % 2 else p for i, p in enumerate(parts)), text)


def natural_sorted(paths: Iterable[Path]) -> list[Path]:
    """``sorted`` over paths, comparing filenames with :func:`natural_key`."""
    return sorted(paths, key=lambda p: natural_key(p.name))
