# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal mutagen stubs for static analysis. Runtime uses the real package."""

from typing import Any

def File(filename: str, *args: Any, **kwargs: Any) -> Any: ...
def __getattr__(name: str) -> Any: ...
