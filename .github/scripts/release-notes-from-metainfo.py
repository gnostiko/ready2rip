#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Print the current AppStream release blurb for ``gh release create --notes-file``."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METAINFO = ROOT / 'data' / 'org.ready2rip.Ready2Rip.metainfo.xml.in'

_RELEASE_P = re.compile(
    r'<release\b[^>]*>\s*<description>\s*<p>\s*(.*?)\s*</p>',
    re.DOTALL,
)


def main() -> int:
    text = METAINFO.read_text(encoding='utf-8')
    match = _RELEASE_P.search(text)
    if not match:
        print('ready2rip release', end='')
        return 0
    body = re.sub(r'\s+', ' ', match.group(1)).strip()
    # Drop leading "0.4.21: " style version prefix — the release title already
    # carries the version.
    body = re.sub(r'^\d+\.\d+\.\d+\s*:\s*', '', body)
    print(body, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
