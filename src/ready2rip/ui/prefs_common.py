# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers shared by preference UI modules."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def block_handlers(pairs: list[tuple[Any, Callable[..., Any]]]) -> Iterator[None]:
    """Temporarily block GTK signal handlers on *(widget, handler)* pairs."""
    for widget, handler in pairs:
        widget.handler_block_by_func(handler)
    try:
        yield
    finally:
        for widget, handler in pairs:
            widget.handler_unblock_by_func(handler)