"""Graceful abort for the drivers, on SIGINT (Ctrl-C) or SIGTERM (``kill``).

The handler only sets a flag; it never raises. The driver checks the flag between
inputs, so the in-flight input finishes and the final ``user_abort`` report still
renders.

SIGKILL, OOM and power loss run no handler; the per-input durability writes (the
basecall cache and ``latest_snapshot.json``) cover those.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FinalizeReason(str, Enum):
    """Why the driver is finalizing; the values are the reporter's reasons.

    INTERIM is non-terminal (``reporter.report``); RUN_END and USER_ABORT are terminal
    (``reporter.finalize``, which accepts only these two).
    """

    INTERIM = "interim"
    RUN_END = "run_end"
    USER_ABORT = "user_abort"


class AbortFlag:
    """A thread-safe, one-way 'please stop after the current input' flag."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        """Request a graceful abort (idempotent) and log it.

        Not for signal handlers: use :meth:`set_from_signal` there.
        """
        if not self._event.is_set():
            logger.info("Abort requested")
        self._event.set()

    def set_from_signal(self) -> None:
        """Async-signal-safe abort request: sets the flag and nothing else.

        A handler runs at an arbitrary bytecode boundary and ``logging`` takes locks, so
        logging here could deadlock. The run loop logs the abort when it sees the flag.
        """
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def event(self) -> threading.Event:
        """The underlying Event, for code that takes a ``threading.Event`` stop signal.

        The real-time seal sweep is given this one, so an abort reaches it too.
        """
        return self._event


def install_signal_handlers(flag: AbortFlag, *,
                            notice: Optional[bytes] = None) -> dict[int, Any]:
    """Route SIGINT and SIGTERM to a graceful abort (call from the main thread).

    The handler only sets ``flag``; it never raises. Returns the previous handlers, for
    :func:`restore_signal_handlers`.

    ``notice``, if given, is written to stderr on every press, so the operator sees the
    abort was registered while the in-flight input finishes. It is written with
    ``os.write``, which takes no lock and is safe in a handler.
    """
    previous: dict[int, Any] = {}

    def _handler(signum, _frame):
        # The flag and one raw write; no logging here (see set_from_signal).
        flag.set_from_signal()
        if notice:
            try:
                os.write(2, notice)
            except OSError:
                pass  # a closed/redirected stderr must never break the abort itself

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, _handler)
    return previous


def restore_signal_handlers(previous: dict[int, Any]) -> None:
    """Restore handlers saved by :func:`install_signal_handlers`."""
    for sig, handler in previous.items():
        signal.signal(sig, handler)
