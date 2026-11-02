# SPDX-License-Identifier: GPL-3.0-or-later
"""EAC-compatible log checksum tests."""

from __future__ import annotations

from ready2rip.rip.eac_logcrc import (
    append_eac_checksum,
    eac_log_checksum,
    strip_existing_checksum,
)


def test_checksum_is_64_hex() -> None:
    digest = eac_log_checksum('hello\nworld\n')
    assert len(digest) == 64
    assert digest == digest.upper()
    int(digest, 16)


def test_append_and_strip_roundtrip() -> None:
    body = 'No errors occurred\n\nEnd of status report\n'
    signed = append_eac_checksum(body)
    assert '==== Log checksum ' in signed
    unsigned = strip_existing_checksum(signed)
    assert 'Log checksum' not in unsigned
    again = append_eac_checksum(unsigned)
    assert again == signed


def test_newlines_ignored_in_digest() -> None:
    a = eac_log_checksum('a\nb\n')
    b = eac_log_checksum('ab')
    assert a == b
