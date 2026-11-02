# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact Audio Copy–compatible log checksum (Rijndael-256 CBC XOR).

Algorithm matches OPSnet/eac_logchecker.py and puddly/eac_logsigner:
UTF-16-LE plaintext (newlines/BOMs stripped), Rijndael-256 CBC with
all-zero IV and key, then hex of the final ciphertext block.
"""

from __future__ import annotations

import re

from ready2rip.eac_rijndael import rijndael

# Public EAC key used by Exact Audio Copy for log signatures.
_EAC_KEY = bytes.fromhex(
    '9378716cf13e4265ae55338e940b376184da389e50647726b35f6f341ee3efd9'
)
_CHECKSUM_RE = re.compile(
    r'\n\n==== Log checksum [A-F0-9]{64} ====\s*\n?\Z',
    re.IGNORECASE,
)


def eac_log_checksum(text: str) -> str:
    """Return 64-char uppercase hex EAC log checksum for *text* (unsigned body)."""
    body = text.replace('\r', '').replace('\n', '')
    body = body.replace('\ufeff', '').replace('\ufffe', '')
    cipher = rijndael(key=_EAC_KEY, block_size=32)
    plaintext = body.encode('utf-16-le')
    checksum = b'\x00' * 32
    for i in range(0, len(plaintext), 32):
        block = plaintext[i : i + 32].ljust(32, b'\x00')
        xored = bytes(a ^ b for a, b in zip(checksum, block, strict=True))
        checksum = cipher.encrypt(xored)
    return checksum.hex().upper()


def strip_existing_checksum(text: str) -> str:
    """Remove a trailing ``==== Log checksum … ====`` block if present."""
    return _CHECKSUM_RE.sub('', text)


def append_eac_checksum(text: str) -> str:
    """Append an EAC-style checksum footer to *text* (replacing any prior one)."""
    body = strip_existing_checksum(text).rstrip() + '\n'
    digest = eac_log_checksum(body)
    return f'{body}\n==== Log checksum {digest} ====\n'
