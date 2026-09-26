# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal PyGObject stubs so the type checker can resolve ``import gi``.

Runtime still uses the system PyGObject; these files are analysis-only.
"""

from typing import Any

def require_version(namespace: str, version: str) -> None: ...
def require_foreign(namespace: str, symbol: str | None = None) -> None: ...
def check_version(*args: Any) -> None: ...

class module:
    def __getattr__(self, name: str) -> Any: ...
