# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for CTDB Galois16 parity encode / syndrome helpers."""

from __future__ import annotations

import base64
import zlib

from ready2rip.ctdb import (
    CtdbEntry,
    attempt_repair,
    compute_disc_crc,
    disc_crc_bounds,
    parse_lookup_xml,
)
from ready2rip.ctdb_parity import (
    CTDB_NPAR,
    CTDB_STRIDE,
    GALOIS16,
    CdRepairEncode,
    Galois16,
    bytes_to_syndrome,
    encode_gx_log,
    encode_parity_from_pcm,
    parity_to_syndrome,
    syndrome_to_bytes,
    syndrome_to_parity,
)


def test_galois16_mul_basics() -> None:
    g = GALOIS16
    assert g.mul(0, 1) == 0
    assert g.mul(1, 1) == 1
    assert g.mul(2, 3) == 6
    # α^1 * α^1 = α^2
    assert g.mul(g.to_exp(1), g.to_exp(1)) == g.to_exp(2)
    # a * inv(a) == 1
    a = 0x1234
    assert g.mul(a, g.inv(a)) == 1


def test_galois16_mul_commutative() -> None:
    g = Galois16()
    assert g.mul(2, 3) == g.mul(3, 2)
    assert g.mul(0, 9) == 0


def test_encode_gx_log_npar8_reference() -> None:
    # Hard-coded GF log table for npar=8 (ProcessStride8 reference values).
    assert encode_gx_log(8) == [
        19483,
        41576,
        9460,
        52075,
        9467,
        41590,
        19504,
        28,
    ]


def test_disc_crc_truncation_matches_slice() -> None:
    stride = 16
    # Need enough samples for prefix/suffix with this tiny stride.
    total_samples = stride * 4  # 64 stereo frames
    pcm = bytes((i * 17) % 256 for i in range(total_samples * 4))
    start, end = disc_crc_bounds(total_samples, stride=stride)
    expected = zlib.crc32(pcm[start:end]) & 0xFFFFFFFF
    assert compute_disc_crc(pcm, total_samples, stride=stride) == expected


def test_parity_syndrome_roundtrip_synthetic() -> None:
    stride = 8
    npar = CTDB_NPAR
    # stridecount = (N*2)/stride - 2 >= 1  =>  N*2 >= 3*stride  => N >= 12
    total_samples = 24
    pcm = bytes((i * 31 + 7) % 256 for i in range(total_samples * 4))

    result = encode_parity_from_pcm(pcm, stride=stride, npar=npar)
    assert result.npar == npar
    assert result.stride == stride
    assert len(result.parity_raw) == stride * npar * 2
    assert len(result.syndrome_bytes) == stride * npar * 2
    assert len(result.parity_head) == npar * 2
    assert len(result.syndrome_head_b64_bytes) == npar * 2

    # Streaming encoder must match one-shot.
    enc = CdRepairEncode(total_samples, stride=stride, npar=npar)
    mid = (total_samples // 2) * 4
    enc.write_pcm(pcm[:mid])
    enc.write_pcm(pcm[mid:])
    streamed = enc.finish()
    assert streamed.parity_raw == result.parity_raw
    assert streamed.disc_crc == result.disc_crc
    assert streamed.disc_crc == compute_disc_crc(pcm, total_samples, stride=stride)

    # Parity -> syndrome -> parity round-trip on the head stride.
    syn_head = parity_to_syndrome(result.parity_raw, stride, npar, strides=1, npar2=npar)
    roundtrip = syndrome_to_parity(syn_head)
    assert roundtrip == result.parity_head

    # Syndrome2Bytes / Bytes2Syndrome round-trip.
    rebuilt = bytes_to_syndrome(result.syndrome_bytes, stride, npar)
    assert syndrome_to_bytes(rebuilt) == result.syndrome_bytes


def test_encode_parity_silence_full_stride() -> None:
    samples = CTDB_STRIDE * 4
    pcm = b"\x00" * (samples * 4)
    res = encode_parity_from_pcm(pcm)
    assert len(res.parity_raw) == CTDB_STRIDE * CTDB_NPAR * 2
    assert res.disc_crc == compute_disc_crc(pcm)
    assert len(res.syndrome_head_b64_bytes) == CTDB_NPAR * 2


def test_parse_hasparity_from_lookup_xml() -> None:
    xml = """<?xml version="1.0"?>
<ctdb xmlns="http://db.cuetools.net/ns/mmd-1.0#">
 <entry confidence="11" crc32="843c89cb" id="10631343"
        toc="0:14741:26007:39239:54137"
        hasparity="http://p.cuetools.net/10631343"
        syndrome="c3luZA==" npar="8" stride="5880"
        trackcrcs="797874e7 b8492504 4b87b5de d29262a2" />
</ctdb>
"""
    db = parse_lookup_xml(xml)
    assert len(db.entries) == 1
    entry = db.entries[0]
    assert entry.hasparity == "http://p.cuetools.net/10631343"
    assert entry.npar == 8
    assert entry.stride == 5880


def test_attempt_repair_without_hasparity() -> None:
    entry = CtdbEntry(
        entry_id="1",
        confidence=1,
        crc32="0",
        track_crcs=[1],
        hasparity="",
    )
    probe = attempt_repair(entry)
    assert not probe.ok
    assert not probe.has_parity
    assert "hasparity" in probe.message.lower()


def test_syndrome_head_b64_roundtrip_length() -> None:
    stride = 8
    total_samples = 24
    pcm = bytes(range(256)) * ((total_samples * 4 + 255) // 256)
    pcm = pcm[: total_samples * 4]
    result = encode_parity_from_pcm(pcm, stride=stride, npar=8)
    b64 = base64.b64encode(result.syndrome_head_b64_bytes).decode("ascii")
    assert len(base64.b64decode(b64)) == 16  # 8 ushorts
