# SPDX-License-Identifier: GPL-3.0-or-later
"""CTDB Reed-Solomon parity (Galois16) matching CUETools CDRepairEncode.

Ported from CUETools.Parity (Galois / RsEncode / ParityToSyndrome) and the
``CDRepairEncode`` / ``AccurateRipVerify`` encode path. LGPL/GPL-compatible
with ready2rip's GPL-3.0.
"""

from __future__ import annotations

import struct
import zlib
from array import array
from dataclasses import dataclass

# GF(2^16) primitive used by CUETools Galois16.
GALOIS16_POLYNOMIAL = 0x1100B
GALOIS16_W = 16
GALOIS16_MAX = (1 << GALOIS16_W) - 1

# CUETools default CTDB stride (ushort units; stereo frame = 2 ushorts).
CTDB_STRIDE = 5880
# Classic CTDB submit width (form ``parity`` field always uses 8).
CTDB_NPAR = 8


def _laststride_samples(total_samples: int, *, stride: int = CTDB_STRIDE) -> int:
    if stride <= 0:
        raise ValueError("stride must be positive")
    return stride + (max(0, total_samples) * 2) % stride


class Galois16:
    """Galois field GF(2^16) with CUETools polynomial ``0x1100B``."""

    __slots__ = ("exp_tbl", "log_tbl", "max")

    def __init__(self, polynomial: int = GALOIS16_POLYNOMIAL, width: int = GALOIS16_W) -> None:
        self.max = (1 << width) - 1
        exp_tbl = [0] * (self.max * 2)
        log_tbl = [0] * (self.max + 1)
        d = 1
        for i in range(self.max):
            exp_tbl[i] = exp_tbl[self.max + i] = d
            log_tbl[d] = i
            d <<= 1
            if (d >> width) & 1:
                d = (d ^ polynomial) & self.max
        self.exp_tbl = exp_tbl
        self.log_tbl = log_tbl

    def to_exp(self, a: int) -> int:
        return self.exp_tbl[a]

    def to_log(self, a: int) -> int:
        return self.log_tbl[a]

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp_tbl[self.log_tbl[a] + self.log_tbl[b]]

    def mul_exp(self, a: int, b: int) -> int:
        if a == 0:
            return 0
        return self.exp_tbl[self.log_tbl[a] + b]

    def div(self, a: int, b: int) -> int:
        if a == 0:
            return 0
        return self.exp_tbl[self.log_tbl[a] - self.log_tbl[b] + self.max]

    def div_exp(self, a: int, b: int) -> int:
        if a == 0:
            return 0
        return self.exp_tbl[self.log_tbl[a] - b + self.max]

    def inv(self, a: int) -> int:
        return self.exp_tbl[self.max - self.log_tbl[a]]

    def to_log_list(self, values: list[int]) -> list[int]:
        return [-1 if v == 0 else self.to_log(v) for v in values]

    def to_exp_list(self, values: list[int]) -> list[int]:
        return [0 if v == -1 else self.to_exp(v) for v in values]

    def gfdiff(self, a: list[int]) -> list[int]:
        return [a[i + 1] if (i % 2) == 0 else -1 for i in range(len(a) - 1)]

    def gfconv(self, a: list[int], b: list[int], length: int | None = None) -> list[int]:
        if length is None:
            length = len(a) + len(b) - 1
        seki = [0] * length
        for ia, loga in enumerate(a):
            if loga == -1:
                continue
            ib2 = min(len(b), length - ia)
            for ib in range(ib2):
                logb = b[ib]
                if logb != -1:
                    seki[ia + ib] ^= self.exp_tbl[loga + logb]
        return [-1 if v == 0 else self.log_tbl[v] for v in seki]

    def gfsubstitute(self, polynomial: list[int], value: int, terms: int) -> int:
        total = 0
        if value != -1:
            for p in range(terms):
                if polynomial[p] != -1:
                    pow_ = polynomial[p] + value * p
                    total ^= self.exp_tbl[(pow_ & self.max) + (pow_ >> GALOIS16_W)]
        return -1 if total == 0 else self.log_tbl[total]

    def make_encode_gx(self, npar: int) -> list[int]:
        """Generator polynomial coefficients (high degree first), as field elements."""
        if npar <= 0:
            raise ValueError("npar must be positive")
        encode_gx = [0] * npar
        encode_gx[npar - 1] = 1
        for kou in range(npar):
            ex = self.to_exp(kou)
            for j in range(npar - 1):
                encode_gx[j] = self.mul(encode_gx[j], ex) ^ encode_gx[j + 1]
            encode_gx[npar - 1] = self.mul(encode_gx[npar - 1], ex)
        return encode_gx

    def make_encode_gx_log(self, npar: int) -> list[int]:
        encode_gx = self.make_encode_gx(npar)
        out: list[int] = []
        for coeff in encode_gx:
            if coeff == 0:
                raise ValueError("0 in encodeGx")
            out.append(self.to_log(coeff))
        return out


GALOIS16 = Galois16()


def encode_gx_log(npar: int = CTDB_NPAR) -> list[int]:
    return GALOIS16.make_encode_gx_log(npar)


def rs_encode_update(
    wr: array,
    sample: int,
    encode_gx_log_vals: list[int],
    *,
    galois: Galois16 = GALOIS16,
) -> None:
    """One RsEncode16 step into remainder register *wr* (length = npar)."""
    npar = len(encode_gx_log_vals)
    ib = wr[0] ^ (sample & 0xFFFF)
    if ib != 0:
        base = galois.log_tbl[ib]
        exp = galois.exp_tbl
        for i in range(npar - 1):
            wr[i] = wr[i + 1] ^ exp[base + encode_gx_log_vals[i]]
        wr[npar - 1] = exp[base + encode_gx_log_vals[npar - 1]]
    else:
        for i in range(npar - 1):
            wr[i] = wr[i + 1]
        wr[npar - 1] = 0


@dataclass
class ParityResult:
    """Parity/syndrome artefacts for a CTDB submit."""

    parity_raw: bytes
    """Raw RS remainder buffer: ``ushort[stride][npar]`` little-endian."""

    syndrome_bytes: bytes
    """Full-disc syndrome in CTDB ``Syndrome2Bytes`` layout (parityfile body)."""

    syndrome_head_b64_bytes: bytes
    """First-stride syndrome bytes (XML/form ``syndrome`` payload, not b64)."""

    parity_head: bytes
    """First-stride RS parity (form ``parity`` payload after Syndrome2Parity)."""

    disc_crc: int
    """Whole-disc CTDB CRC32 (truncated like track CRC)."""

    npar: int
    stride: int
    total_samples: int


def disc_crc_bounds(
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> tuple[int, int]:
    """Return ``[start, end)`` byte offsets into whole-disc PCM for disc CRC."""
    if total_samples < 0:
        raise ValueError("total_samples must be non-negative")
    total_bytes = total_samples * 4
    prefix = stride // 2
    suffix = _laststride_samples(total_samples, stride=stride) // 2
    start = prefix * 4
    end = total_bytes - suffix * 4
    if start < 0 or end > total_bytes or end < start:
        raise ValueError(
            f"Disc too short for CTDB CRC (need prefix={prefix} suffix={suffix} samples)"
        )
    return start, end


def compute_disc_crc(
    pcm: bytes,
    total_samples: int | None = None,
    *,
    stride: int = CTDB_STRIDE,
) -> int:
    """CRC32 of the CTDB-truncated whole-disc PCM range (IEEE / zlib)."""
    if len(pcm) % 4 != 0:
        raise ValueError("PCM length must be a multiple of 4")
    samples = total_samples if total_samples is not None else len(pcm) // 4
    if samples * 4 != len(pcm):
        raise ValueError("PCM length does not match total_samples")
    start, end = disc_crc_bounds(samples, stride=stride)
    return zlib.crc32(pcm[start:end]) & 0xFFFFFFFF


def parity_raw_to_u16(parity: bytes, stride: int, npar: int) -> list[list[int]]:
    """Parse raw remainder buffer into ``[stride][npar]`` ushorts."""
    expected = stride * npar * 2
    if len(parity) < expected:
        raise ValueError(f"parity length {len(parity)} < {expected}")
    rows: list[list[int]] = []
    for part in range(stride):
        base = part * npar * 2
        row = list(struct.unpack_from(f"<{npar}H", parity, base))
        rows.append(row)
    return rows


def u16_grid_to_bytes(rows: list[list[int]]) -> bytes:
    """Serialize ``[stride][npar]`` ushorts to little-endian bytes."""
    out = bytearray()
    for row in rows:
        out.extend(struct.pack(f"<{len(row)}H", *row))
    return bytes(out)


def syndrome_to_bytes(syndrome: list[list[int]]) -> bytes:
    """``ParityToSyndrome.Syndrome2Bytes`` layout."""
    if not syndrome:
        return b""
    stride = len(syndrome)
    npar = len(syndrome[0])
    out = bytearray(npar * stride * 2)
    for i in range(npar):
        for j in range(stride):
            struct.pack_into("<H", out, (j + i * stride) * 2, syndrome[j][i] & 0xFFFF)
    return bytes(out)


def bytes_to_syndrome(data: bytes, stride: int, npar: int) -> list[list[int]]:
    """``ParityToSyndrome.Bytes2Syndrome``."""
    expected = npar * stride * 2
    if len(data) < expected:
        raise ValueError(f"parity length {len(data)} < {expected}")
    syn = [[0] * npar for _ in range(stride)]
    for i in range(npar):
        for j in range(stride):
            syn[j][i] = struct.unpack_from("<H", data, (j + i * stride) * 2)[0]
    return syn


def parity_to_syndrome(
    parity: bytes,
    stride: int,
    npar: int,
    *,
    strides: int | None = None,
    npar2: int | None = None,
    offset: int = 0,
    galois: Galois16 = GALOIS16,
) -> list[list[int]]:
    """``ParityToSyndrome.Parity2Syndrome`` (offset in ushort units)."""
    stride2 = stride
    npar_full = npar if npar2 is None else npar2
    out_strides = stride if strides is None else strides
    if npar > npar_full or out_strides > stride2:
        raise ValueError("npar/stride larger than source parity")
    expected = stride2 * npar_full * 2
    if len(parity) < expected:
        raise ValueError(f"parity length {len(parity)} < {expected}")

    syn = [[0] * npar for _ in range(out_strides)]
    log_tbl = galois.log_tbl
    exp_tbl = galois.exp_tbl
    for y in range(out_strides):
        y1 = (y - offset + stride2) % stride2
        base = y1 * npar_full * 2
        for x1 in range(npar_full):
            lo = struct.unpack_from("<H", parity, base + x1 * 2)[0]
            if lo == 0:
                continue
            llo = log_tbl[lo] + 0xFFFF
            for x in range(npar):
                syn[y][x] ^= exp_tbl[llo - (1 + x1) * x]
    return syn


class _ParityToSyndromeConverter:
    """Erasure-only Syndrome2Parity for one stride column."""

    def __init__(self, npar: int, *, galois: Galois16 = GALOIS16) -> None:
        self.npar = npar
        self.galois = galois
        self.max = galois.max
        erasures_pos = list(range(npar))
        erasure_loc_pol_exp = [0] * (npar + 1)
        erasure_loc_pol_exp[0] = 1
        for i in range(npar):
            for x in range(npar, 0, -1):
                erasure_loc_pol_exp[x] ^= galois.mul_exp(
                    erasure_loc_pol_exp[x - 1], erasures_pos[i]
                )
        self.erasure_loc_pol = galois.to_log_list(erasure_loc_pol_exp)
        self.erasure_diff = galois.gfdiff(self.erasure_loc_pol)
        self.erasures_pos = erasures_pos

    def syndrome_to_parity_column(self, syndrome: list[int]) -> list[int]:
        npar = self.npar
        galois = self.galois
        s_pol = [-1] * (npar + 1)
        for i in range(npar):
            if syndrome[i] == 0:
                s_pol[i + 1] = -1
            else:
                exp = galois.log_tbl[syndrome[i]] + npar * i
                s_pol[i + 1] = (exp & self.max) + (exp >> GALOIS16_W)
        mod_syn = galois.gfconv(self.erasure_loc_pol, s_pol, npar + 1)
        omega = mod_syn
        tsi_diff = self.erasure_diff
        parity = [0] * npar
        for ii, place in enumerate(self.erasures_pos):
            point = self.max - place
            err_den = galois.gfsubstitute(tsi_diff, point, len(tsi_diff))
            err_num = galois.gfsubstitute(omega, point, len(omega))
            pow_ = err_num + place + ii + self.max - err_den
            if err_num == -1:
                parity[npar - 1 - ii] = 0
            else:
                parity[npar - 1 - ii] = galois.exp_tbl[(pow_ & self.max) + (pow_ >> GALOIS16_W)]
        return parity


def syndrome_to_parity(syndrome: list[list[int]]) -> bytes:
    """``ParityToSyndrome.Syndrome2Parity``."""
    if not syndrome:
        return b""
    npar = len(syndrome[0])
    converter = _ParityToSyndromeConverter(npar)
    rows = [converter.syndrome_to_parity_column(row) for row in syndrome]
    return u16_grid_to_bytes(rows)


class CdRepairEncode:
    """Streaming CDRepairEncode-compatible parity + disc CRC accumulator.

    Feeds interleaved little-endian CDDA PCM (ushort samples). Skips the first
    stride of ushorts and the lead-out (``stride + laststride`` ushorts at the
    end of the remaining region), matching CUETools.
    """

    def __init__(
        self,
        total_samples: int,
        *,
        stride: int = CTDB_STRIDE,
        npar: int = CTDB_NPAR,
        galois: Galois16 = GALOIS16,
    ) -> None:
        if stride <= 0 or stride % 2 != 0:
            raise ValueError("stride must be a positive even integer")
        if npar <= 0:
            raise ValueError("npar must be positive")
        if total_samples <= 0:
            raise ValueError("total_samples must be positive")
        self.total_samples = total_samples
        self.stride = stride
        self.npar = npar
        self.galois = galois
        self.laststride = _laststride_samples(total_samples, stride=stride)
        self.stridecount = (total_samples * 2) // stride - 2
        if self.stridecount < 1:
            raise ValueError("disc too short for CTDB parity")
        if (total_samples * 2 + stride - 1) // stride + npar > galois.max:
            raise ValueError("invalid stride for Galois16")
        self.encode_gx_log = galois.make_encode_gx_log(npar)
        self._parity = [array("H", [0] * npar) for _ in range(stride)]
        self._sample_count = 0  # stereo frames consumed
        self._ushort_index = 0  # ushorts into current stereo stream position
        self._crc = 0
        self._crc_started = False
        prefix = stride // 2
        suffix = self.laststride // 2
        self._crc_start_sample = prefix
        self._crc_end_sample = total_samples - suffix
        if self._crc_end_sample < self._crc_start_sample:
            raise ValueError("disc too short for CTDB CRC")

    @property
    def samples_written(self) -> int:
        return self._sample_count

    def write_pcm(self, pcm: bytes) -> None:
        if len(pcm) % 4 != 0:
            raise ValueError("PCM chunk length must be a multiple of 4")
        n_samples = len(pcm) // 4
        if self._sample_count + n_samples > self.total_samples:
            raise ValueError("sampleCount > finalSampleCount")
        # Process as little-endian ushort pairs (L, R) per stereo frame.
        mv = memoryview(pcm).cast("B")
        stride = self.stride
        gx = self.encode_gx_log
        galois = self.galois
        parity = self._parity
        crc_start = self._crc_start_sample
        crc_end = self._crc_end_sample

        offset = 0
        while offset < n_samples:
            sample_count = self._sample_count
            current_part = (sample_count * 2) % stride
            current_stride = (sample_count * 2) // stride
            copy_count = min((n_samples - offset) * 2, stride - current_part)
            # copy_count is in ushorts; convert stereo frames.
            frames = copy_count >> 1

            if current_stride >= 1 and current_stride <= self.stridecount:
                # Encode ushorts for this partial stride.
                base = offset * 4
                for pos in range(copy_count):
                    part = current_part + pos
                    # ushort at stereo-frame offset: frame = pos//2, ch = pos%2
                    byte_off = base + pos * 2
                    dd = mv[byte_off] | (mv[byte_off + 1] << 8)
                    rs_encode_update(parity[part], dd, gx, galois=galois)

            # Disc CRC over truncated stereo frames (full frames only).
            for i in range(frames):
                abs_sample = sample_count + i
                if crc_start <= abs_sample < crc_end:
                    frame = pcm[(offset + i) * 4 : (offset + i + 1) * 4]
                    if not self._crc_started:
                        self._crc = zlib.crc32(frame) & 0xFFFFFFFF
                        self._crc_started = True
                    else:
                        self._crc = zlib.crc32(frame, self._crc) & 0xFFFFFFFF

            self._sample_count += frames
            offset += frames

    def finish(self) -> ParityResult:
        if self._sample_count != self.total_samples:
            raise ValueError(
                f"incomplete PCM: wrote {self._sample_count}/{self.total_samples} samples"
            )
        parity_raw = u16_grid_to_bytes([list(row) for row in self._parity])
        syn_full = parity_to_syndrome(
            parity_raw, self.stride, self.npar, strides=self.stride, npar2=self.npar
        )
        syn_head = parity_to_syndrome(
            parity_raw, self.stride, self.npar, strides=1, npar2=self.npar
        )
        parity_head = syndrome_to_parity(syn_head)
        return ParityResult(
            parity_raw=parity_raw,
            syndrome_bytes=syndrome_to_bytes(syn_full),
            syndrome_head_b64_bytes=syndrome_to_bytes(syn_head),
            parity_head=parity_head,
            disc_crc=self._crc if self._crc_started else 0,
            npar=self.npar,
            stride=self.stride,
            total_samples=self.total_samples,
        )


def encode_parity_from_pcm(
    pcm: bytes,
    *,
    stride: int = CTDB_STRIDE,
    npar: int = CTDB_NPAR,
) -> ParityResult:
    """One-shot parity encode over whole-disc PCM."""
    if len(pcm) % 4 != 0:
        raise ValueError("PCM length must be a multiple of 4")
    enc = CdRepairEncode(len(pcm) // 4, stride=stride, npar=npar)
    enc.write_pcm(pcm)
    return enc.finish()


def syndrome_nonzero_count(syndrome: list[list[int]]) -> int:
    return sum(1 for row in syndrome for v in row if v)


def syndrome_suggests_correctable(
    local_syn: list[list[int]],
    db_syn: list[list[int]],
    *,
    npar: int | None = None,
) -> bool | None:
    """Heuristic: XOR of syndromes; correctable if ≤ npar/2 nonzero columns.

    Returns ``None`` when shapes mismatch.
    """
    if not local_syn or not db_syn:
        return None
    if len(local_syn) != len(db_syn) or len(local_syn[0]) != len(db_syn[0]):
        return None
    width = npar if npar is not None else len(local_syn[0])
    bad_cols = 0
    for col in range(len(local_syn)):
        if any((local_syn[col][i] ^ db_syn[col][i]) for i in range(width)):
            bad_cols += 1
    return bad_cols <= max(1, width // 2)
