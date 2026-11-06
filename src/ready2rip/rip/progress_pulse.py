# SPDX-License-Identifier: GPL-3.0-or-later
"""Background progress heartbeat for blocking rip phases (gi-free)."""

from __future__ import annotations

import threading
from collections.abc import Callable


class ProgressPulse:
    """Call *on_tick* on an interval until :meth:`stop`.

    Used while CRC, encode, or ReplayGain block so the banner keeps moving.
    """

    def __init__(
        self,
        on_tick: Callable[[], None],
        *,
        interval: float = 0.2,
    ) -> None:
        self._on_tick = on_tick
        self._interval = max(0.05, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def _loop() -> None:
            # Immediate tick so the status line updates before the first wait.
            try:
                self._on_tick()
            except Exception:
                pass
            while not self._stop.wait(self._interval):
                try:
                    self._on_tick()
                except Exception:
                    pass

        self._thread = threading.Thread(
            target=_loop, name='r2r-progress-pulse', daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def __enter__(self) -> ProgressPulse:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def pulse_while(
    on_tick: Callable[[], None],
    work: Callable[[], object],
    *,
    interval: float = 0.2,
) -> object:
    """Run *work* while pulsing *on_tick*."""
    with ProgressPulse(on_tick, interval=interval):
        return work()
