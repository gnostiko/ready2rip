# SPDX-License-Identifier: GPL-3.0-or-later
"""CTDB lookup, CRC verify, parity submit, and repair probe.

Uses colon-separated TOC lookup via ``lookup2.php``, per-track CRC32 over
raw CDDA PCM with first/last truncation (stride 5880), and optional
``submit2.php`` entries with Galois16 Reed-Solomon parity (npar=8) when a
confirm is impossible but rip quality is high.
"""

from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from ready2rip.ctdb_parity import (
    CTDB_NPAR,
    CdRepairEncode,
    ParityResult,
    bytes_to_syndrome,
    compute_disc_crc,
    disc_crc_bounds,
    encode_parity_from_pcm,
    syndrome_nonzero_count,
    syndrome_suggests_correctable,
    syndrome_to_bytes,
)
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import USER_AGENT

log = logging.getLogger(__name__)

# Primary HTTPS endpoint; HTTP host kept as fallback.
CTDB_LOOKUP_URLS = (
    "https://db.cue.tools/lookup2.php",
    "http://db.cuetools.net/lookup2.php",
)
CTDB_SUBMIT_URLS = (
    "https://db.cue.tools/submit2.php",
    "http://db.cuetools.net/submit2.php",
)

# CTDB stride (samples are stereo frames = 4 bytes).
CTDB_STRIDE = 5880

# Re-export parity helpers used by tests / callers.
__all__ = (
    "CTDB_NPAR",
    "CTDB_STRIDE",
    "CtdbConfidence",
    "CtdbDatabase",
    "CtdbEntry",
    "CtdbRepairProbe",
    "CtdbResult",
    "CtdbSubmitResult",
    "CtdbVerifier",
    "attempt_repair",
    "compute_disc_crc",
    "compute_track_crc",
    "compute_track_crc_wav",
    "disc_crc_bounds",
    "encode_parity_from_pcm",
    "fetch_database",
    "laststride_samples",
    "load_cdda_pcm",
    "parse_lookup_xml",
    "submit_confirm",
    "submit_new",
    "toc_string_from_info",
    "total_audio_samples",
    "track_crc_bounds",
)


class CtdbConfidence(Enum):
    MISMATCH = auto()
    MATCH = auto()
    NOT_IN_DB = auto()
    ERROR = auto()


@dataclass
class CtdbResult:
    track_number: int
    confidence: CtdbConfidence
    confidence_count: int = 0
    crc: str = ""
    message: str = ""


@dataclass
class CtdbEntry:
    entry_id: str
    confidence: int
    crc32: str
    track_crcs: list[int]
    toc: str = ""
    parity: str = ""
    syndrome: str = ""
    npar: int = 0
    hasparity: str = ""
    stride: int = CTDB_STRIDE


@dataclass
class CtdbSubmitResult:
    ok: bool
    message: str
    status: str = ""


@dataclass
class CtdbRepairProbe:
    """Result of :func:`attempt_repair` (download + syndrome probe only)."""

    ok: bool
    message: str
    has_parity: bool = False
    syndrome_match: bool = False
    nonzero_syndrome: bool = False
    correctable_hint: bool | None = None
    parity_bytes: int = 0


@dataclass
class CtdbDatabase:
    toc: str
    entries: list[CtdbEntry] = field(default_factory=list)

    def best_match(self, track_number: int, crc: int) -> CtdbResult:
        """Sum confidence across entries whose track CRC matches *crc*."""
        if not self.entries:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.NOT_IN_DB,
                crc=f"{crc:08x}",
                message="Disc not in CUETools Database",
            )

        idx = track_number - 1
        conf = 0
        for entry in self.entries:
            if 0 <= idx < len(entry.track_crcs) and entry.track_crcs[idx] == crc:
                conf += max(0, int(entry.confidence))

        if conf > 0:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.MATCH,
                confidence_count=conf,
                crc=f"{crc:08x}",
                message=f"CTDB match (confidence {conf})",
            )

        max_conf = max((e.confidence for e in self.entries), default=0)
        return CtdbResult(
            track_number=track_number,
            confidence=CtdbConfidence.MISMATCH,
            confidence_count=max_conf,
            crc=f"{crc:08x}",
            message=f"No CTDB match (DB max confidence {max_conf})",
        )

    def entry_matching_track_crcs(self, track_crcs: list[int]) -> CtdbEntry | None:
        """Return an entry whose track CRC list matches *track_crcs* exactly."""
        if not track_crcs:
            return None
        for entry in self.entries:
            if list(entry.track_crcs) == list(track_crcs):
                return entry
        return None

    @property
    def max_entry_id(self) -> int:
        ids: list[int] = []
        for entry in self.entries:
            try:
                ids.append(int(entry.entry_id))
            except (TypeError, ValueError):
                continue
        return max(ids) if ids else 0


def toc_string_from_info(info: DiscInfo) -> str | None:
    """Build a CTDB TOC string relative to the first audio track start.

    Format: ``0:t2:t3:…:leadout`` with data-track offsets prefixed by ``-``.
    """
    if not info.tracks:
        return None

    first = int(info.tracks[0].start_sector)
    items: list[tuple[int, int, bool]] = [
        (t.number, int(t.start_sector), True) for t in info.tracks
    ]
    for d in info.data_tracks or []:
        items.append((d.number, int(d.start_sector), False))
    items.sort(key=lambda item: item[0])

    parts: list[str] = []
    for _num, start, is_audio in items:
        rel = start - first
        parts.append(str(rel) if is_audio else f"-{abs(rel)}")

    leadout = int(info.tracks[-1].start_sector) + int(info.tracks[-1].length_sectors)
    if int(getattr(info, "leadout_lba", 0) or 0) > leadout:
        leadout = int(info.leadout_lba)
    for d in info.data_tracks or []:
        end = int(d.start_sector) + int(d.length_sectors)
        if end > leadout:
            leadout = end
    parts.append(str(leadout - first))
    return ":".join(parts)


def total_audio_samples(info: DiscInfo) -> int:
    """Stereo frames from INDEX 01 of track 1 through last audio lead-out."""
    return sum(int(t.length_sectors) for t in info.tracks) * 588


def laststride_samples(total_samples: int, *, stride: int = CTDB_STRIDE) -> int:
    """CTDB ``laststride`` sample count for *total_samples* (pregap already excluded)."""
    if stride <= 0:
        raise ValueError("stride must be positive")
    return stride + (max(0, total_samples) * 2) % stride


def track_crc_bounds(
    pcm_bytes: int,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> tuple[int, int]:
    """Return ``[start, end)`` byte offsets into raw PCM for a CTDB track CRC."""
    if pcm_bytes < 0 or pcm_bytes % 4 != 0:
        raise ValueError("PCM length must be a non-negative multiple of 4")
    prefix = stride // 2
    suffix = laststride_samples(total_samples, stride=stride) // 2
    start = 0
    end = pcm_bytes
    if track_number == 1 or total_tracks == 1:
        start += prefix * 4
    if track_number == total_tracks or total_tracks == 1:
        end -= suffix * 4
    if start < 0 or end > pcm_bytes or end < start:
        raise ValueError(
            f"Track too short for CTDB CRC (need prefix={prefix} suffix={suffix} samples)"
        )
    return start, end


def compute_track_crc(
    pcm: bytes,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> int:
    """CRC32 of the CTDB-truncated PCM range (IEEE / zlib)."""
    start, end = track_crc_bounds(
        len(pcm),
        track_number,
        total_tracks,
        total_samples,
        stride=stride,
    )
    return zlib.crc32(pcm[start:end]) & 0xFFFFFFFF


def load_cdda_pcm(path: Path) -> bytes:
    """Load raw interleaved LE PCM from a 16-bit stereo 44.1 kHz WAV."""
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 2 or wf.getsampwidth() != 2 or wf.getframerate() != 44100:
            raise ValueError(
                f"CTDB requires 16-bit stereo 44.1 kHz WAV, got "
                f"{wf.getnchannels()}ch {wf.getsampwidth() * 8}bit {wf.getframerate()}Hz"
            )
        return wf.readframes(wf.getnframes())


def compute_track_crc_wav(
    path: Path,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> int:
    return compute_track_crc(
        load_cdda_pcm(path),
        track_number,
        total_tracks,
        total_samples,
        stride=stride,
    )


def parse_lookup_xml(raw: bytes | str, *, toc: str = "") -> CtdbDatabase:
    """Parse a ``lookup2.php`` XML body into :class:`CtdbDatabase`."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    root = ET.fromstring(text)
    db = CtdbDatabase(toc=toc)
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag != "entry":
            continue
        trackcrcs_raw = (elem.get("trackcrcs") or "").strip()
        if not trackcrcs_raw:
            continue
        try:
            track_crcs = [int(part, 16) for part in trackcrcs_raw.split()]
        except ValueError:
            continue
        try:
            conf = int(elem.get("confidence") or "0")
        except ValueError:
            conf = 0
        try:
            npar = int(elem.get("npar") or "0")
        except ValueError:
            npar = 0
        try:
            stride = int(elem.get("stride") or str(CTDB_STRIDE))
        except ValueError:
            stride = CTDB_STRIDE
        db.entries.append(
            CtdbEntry(
                entry_id=str(elem.get("id") or ""),
                confidence=conf,
                crc32=str(elem.get("crc32") or ""),
                track_crcs=track_crcs,
                toc=str(elem.get("toc") or toc),
                parity=str(elem.get("parity") or ""),
                syndrome=str(elem.get("syndrome") or ""),
                npar=npar,
                hasparity=str(elem.get("hasparity") or ""),
                stride=stride if stride > 0 else CTDB_STRIDE,
            )
        )
    return db


def fetch_database(toc: str, timeout: float = 15.0) -> CtdbDatabase | None:
    """Download CTDB entries for *toc*. Returns empty DB when not listed."""
    from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

    query = urllib.parse.urlencode(
        {
            "version": "3",
            "ctdb": "1",
            "fuzzy": "1",
            "metadata": "none",
            "toc": toc,
        }
    )
    last_error: Exception | None = None
    for base in CTDB_LOOKUP_URLS:
        url = f"{base}?{query}"
        if not is_safe_http_url(url):
            log.warning("Refusing non-allowlisted CTDB URL: %s", url)
            continue
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning("Refusing CTDB redirect to unsafe URL: %s", final)
                    continue
                raw = read_limited(response)
            return parse_lookup_xml(raw, toc=toc)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.info("Disc not in CTDB: toc=%s", toc)
                drain_http_error(exc)
                return CtdbDatabase(toc=toc)
            log.warning("CTDB HTTP %s: %s", exc.code, url)
            drain_http_error(exc)
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            log.warning("CTDB fetch failed via %s: %s", base, exc)
            last_error = exc
    if last_error is not None:
        log.warning("CTDB lookup failed for toc=%s: %s", toc, last_error)
    return None


def _crc32_to_signed(value: str | int) -> int:
    """CTDB submit expects a signed 32-bit CRC32 integer."""
    if isinstance(value, str):
        text = value.strip().lower().removeprefix("0x")
        raw = int(text, 16) if text else 0
    else:
        raw = int(value) & 0xFFFFFFFF
    if raw >= 0x80000000:
        return raw - 0x100000000
    return raw


def submit_confirm(
    *,
    toc: str,
    entry: CtdbEntry,
    track_crcs: list[int],
    maxid: int = 0,
    quality: int = 100,
    drivename: str = "",
    artist: str = "",
    title: str = "",
    barcode: str = "",
    timeout: float = 20.0,
) -> CtdbSubmitResult:
    """Confirm an existing CTDB entry (echo lookup parity; no new parity).

    Requires every track CRC to match *entry*. Does not upload parity files or
    create new database rows when the server asks for parity.
    """
    from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

    if not toc or not entry.entry_id:
        return CtdbSubmitResult(ok=False, message="CTDB submit: missing toc/id")
    if list(entry.track_crcs) != list(track_crcs):
        return CtdbSubmitResult(
            ok=False, message="CTDB submit: track CRC list does not match entry"
        )
    if not entry.parity and not entry.syndrome:
        return CtdbSubmitResult(
            ok=False,
            message="CTDB submit: entry has no parity/syndrome to confirm",
        )

    trackcrcs = " ".join(f"{crc:08x}" for crc in track_crcs)
    form: dict[str, str] = {
        "ctdb": "3",
        "toc": toc,
        "crc32": str(_crc32_to_signed(entry.crc32)),
        "trackcrcs": trackcrcs,
        "parity": entry.parity or entry.syndrome,
        "quality": str(max(0, min(100, int(quality)))),
        "confirmid": str(entry.entry_id),
    }
    if entry.syndrome:
        form["syndrome"] = entry.syndrome
    if entry.npar > 0:
        form["npar"] = str(entry.npar)
    if maxid > 0:
        form["maxid"] = str(maxid)
    if drivename:
        form["drivename"] = drivename
    if artist:
        form["artist"] = artist
    if title:
        form["title"] = title
    if barcode:
        form["barcode"] = barcode

    body = urllib.parse.urlencode(form).encode("utf-8")
    last_error: Exception | None = None
    for base in CTDB_SUBMIT_URLS:
        if not is_safe_http_url(base):
            log.warning("Refusing non-allowlisted CTDB submit URL: %s", base)
            continue
        request = urllib.request.Request(
            base,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning("Refusing CTDB submit redirect to unsafe URL: %s", final)
                    continue
                raw = read_limited(response)
            return _parse_submit_response(raw)
        except urllib.error.HTTPError as exc:
            drain_http_error(exc)
            last_error = exc
            log.warning("CTDB submit HTTP %s via %s", exc.code, base)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            last_error = exc
            log.warning("CTDB submit failed via %s: %s", base, exc)
    return CtdbSubmitResult(
        ok=False,
        message=f"CTDB submit failed: {last_error or 'no endpoint'}",
        status="error",
    )


def _parse_submit_response(raw: bytes | str) -> CtdbSubmitResult:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    root = ET.fromstring(text)
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag != "response":
            continue
        status = str(elem.get("status") or "").strip().lower()
        message = str(elem.get("message") or status or "CTDB response").strip()
        if status == "success":
            return CtdbSubmitResult(ok=True, message=message, status=status)
        if status == "parity needed":
            return CtdbSubmitResult(
                ok=False,
                message=message or "CTDB submit: parity needed",
                status=status,
            )
        return CtdbSubmitResult(ok=False, message=message, status=status or "error")
    return CtdbSubmitResult(
        ok=False,
        message="CTDB submit: empty or unrecognized response",
        status="error",
    )


def _multipart_body(
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str]:
    """Build a multipart/form-data body and return ``(body, content_type_header)``."""
    boundary = f"----ready2rip{uuid.uuid4().hex}"
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        lines.append(value.encode("utf-8"))
        lines.append(b"\r\n")
    lines.append(f"--{boundary}\r\n".encode())
    lines.append(
        (f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n').encode()
    )
    lines.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    lines.append(file_bytes)
    lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"


def submit_new(
    *,
    toc: str,
    track_crcs: list[int],
    disc_crc: int,
    parity: ParityResult,
    maxid: int = 0,
    quality: int = 100,
    confidence: int = 1,
    drivename: str = "",
    artist: str = "",
    title: str = "",
    barcode: str = "",
    userid: str = "",
    timeout: float = 60.0,
) -> CtdbSubmitResult:
    """Submit a new CTDB entry with generated parity/syndrome.

    First posts without a parity file. If the server replies ``parity needed``,
    retries once with the full syndrome payload as ``parityfile``.
    """
    if not toc or not track_crcs:
        return CtdbSubmitResult(ok=False, message="CTDB submit: missing toc/trackcrcs")
    if quality < 100:
        return CtdbSubmitResult(
            ok=False,
            message=f"CTDB new submit skipped: quality {quality} < 100",
        )

    trackcrcs = " ".join(f"{crc:08x}" for crc in track_crcs)
    npar = parity.npar if parity.npar > 0 else CTDB_NPAR
    form: dict[str, str] = {
        "ctdb": "3",
        "toc": toc,
        "crc32": str(_crc32_to_signed(disc_crc)),
        "trackcrcs": trackcrcs,
        "parity": base64.b64encode(parity.parity_head).decode("ascii"),
        "syndrome": base64.b64encode(parity.syndrome_head_b64_bytes).decode("ascii"),
        "npar": str(npar),
        "quality": str(max(0, min(100, int(quality)))),
        "confidence": str(max(1, int(confidence))),
        "userid": userid or uuid.uuid4().hex,
    }
    if maxid > 0:
        form["maxid"] = str(maxid)
    if drivename:
        form["drivename"] = drivename
    if artist:
        form["artist"] = artist
    if title:
        form["title"] = title
    if barcode:
        form["barcode"] = barcode

    result = _post_submit(form, timeout=timeout)
    if result.ok or result.status != "parity needed":
        if result.ok:
            result.message = result.message or "CTDB new entry submitted"
        return result

    # Server wants the full parityfile — retry with multipart upload.
    upload_npar = npar
    # Response may advertise a preferred npar; we only generated ``parity.npar``.
    form = dict(form)
    form["parityfile"] = "1"
    form["npar"] = str(upload_npar)
    body, content_type = _multipart_body(
        form,
        file_field="parityfile",
        filename="data.bin",
        file_bytes=parity.syndrome_bytes,
    )
    result2 = _post_submit_raw(body, content_type, timeout=timeout)
    if result2.ok:
        result2.message = result2.message or "CTDB new entry submitted (with parity)"
    return result2


def _post_submit(
    form: dict[str, str],
    *,
    timeout: float,
) -> CtdbSubmitResult:
    body = urllib.parse.urlencode(form).encode("utf-8")
    return _post_submit_raw(
        body,
        "application/x-www-form-urlencoded",
        timeout=timeout,
    )


def _post_submit_raw(
    body: bytes,
    content_type: str,
    *,
    timeout: float,
) -> CtdbSubmitResult:
    from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

    last_error: Exception | None = None
    for base in CTDB_SUBMIT_URLS:
        if not is_safe_http_url(base):
            log.warning("Refusing non-allowlisted CTDB submit URL: %s", base)
            continue
        request = urllib.request.Request(
            base,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": content_type,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning("Refusing CTDB submit redirect to unsafe URL: %s", final)
                    continue
                raw = read_limited(response)
            return _parse_submit_response(raw)
        except urllib.error.HTTPError as exc:
            drain_http_error(exc)
            last_error = exc
            log.warning("CTDB submit HTTP %s via %s", exc.code, base)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            last_error = exc
            log.warning("CTDB submit failed via %s: %s", base, exc)
    return CtdbSubmitResult(
        ok=False,
        message=f"CTDB submit failed: {last_error or 'no endpoint'}",
        status="error",
    )


def fetch_parity_bytes(
    entry: CtdbEntry,
    *,
    timeout: float = 30.0,
) -> bytes | None:
    """Download the parity payload from ``entry.hasparity`` (CTDB CDN)."""
    from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

    url = (entry.hasparity or "").strip()
    if not url:
        return None
    if url.startswith("/"):
        url = f"http://db.cuetools.net{url}"
    if not is_safe_http_url(url):
        log.warning("Refusing non-allowlisted CTDB parity URL: %s", url)
        return None
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
            if final and not is_safe_http_url(final):
                log.warning("Refusing CTDB parity redirect to unsafe URL: %s", final)
                return None
            return read_limited(response)
    except urllib.error.HTTPError as exc:
        drain_http_error(exc)
        log.warning("CTDB parity HTTP %s: %s", exc.code, url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("CTDB parity fetch failed: %s", exc)
    return None


def attempt_repair(
    entry: CtdbEntry,
    *,
    local_syndrome_head: bytes | None = None,
    timeout: float = 30.0,
) -> CtdbRepairProbe:
    """Download entry parity and report whether syndrome suggests correctable errors.

    Full PCM rewrite / RS decode is not implemented yet — this stub verifies the
    parity CDN payload against the entry's stored syndrome head and optionally
    compares it to a locally computed first-stride syndrome.
    """
    if not entry.hasparity:
        return CtdbRepairProbe(
            ok=False,
            message="CTDB repair: entry has no hasparity URL",
            has_parity=False,
        )
    npar = entry.npar if entry.npar > 0 else CTDB_NPAR
    stride = entry.stride if entry.stride > 0 else CTDB_STRIDE
    raw = fetch_parity_bytes(entry, timeout=timeout)
    if raw is None:
        return CtdbRepairProbe(
            ok=False,
            message="CTDB repair: failed to download parity",
            has_parity=True,
        )
    expected_min = npar * stride * 2
    if len(raw) < expected_min:
        return CtdbRepairProbe(
            ok=False,
            message=(f"CTDB repair: parity too short ({len(raw)} < {expected_min} bytes)"),
            has_parity=True,
            parity_bytes=len(raw),
        )
    try:
        syn = bytes_to_syndrome(raw, stride, npar)
    except ValueError as exc:
        return CtdbRepairProbe(
            ok=False,
            message=f"CTDB repair: bad parity layout: {exc}",
            has_parity=True,
            parity_bytes=len(raw),
        )

    entry_syn_match = False
    if entry.syndrome:
        try:
            entry_syn = base64.b64decode(entry.syndrome, validate=False)
            head = syndrome_to_bytes(syn[:1])
            entry_syn_match = entry_syn == head or entry_syn == head[: len(entry_syn)]
        except Exception:
            entry_syn_match = False

    correctable: bool | None = None
    nonzero = syndrome_nonzero_count(syn[:1]) > 0
    if local_syndrome_head is not None:
        try:
            local = bytes_to_syndrome(local_syndrome_head, 1, npar)
            correctable = syndrome_suggests_correctable(local, syn[:1], npar=npar)
            nonzero = (
                syndrome_nonzero_count([[a ^ b for a, b in zip(local[0], syn[0], strict=True)]]) > 0
            )
        except Exception as exc:
            log.warning("CTDB repair local syndrome compare failed: %s", exc)
            correctable = None

    if entry_syn_match and correctable is True:
        msg = (
            "CTDB repair: parity downloaded; syndrome suggests errors are "
            "correctable (PCM rewrite not implemented)"
        )
    elif entry_syn_match and correctable is False:
        msg = (
            "CTDB repair: parity downloaded; syndrome suggests errors exceed "
            "npar/2 (likely not correctable)"
        )
    elif entry_syn_match:
        msg = (
            "CTDB repair: parity downloaded and matches entry syndrome "
            "(full decode/PCM rewrite not implemented)"
        )
    else:
        msg = "CTDB repair: parity downloaded but did not match entry syndrome head"

    return CtdbRepairProbe(
        ok=entry_syn_match,
        message=msg,
        has_parity=True,
        syndrome_match=entry_syn_match,
        nonzero_syndrome=nonzero,
        correctable_hint=correctable,
        parity_bytes=len(raw),
    )


class CtdbVerifier:
    """Fetch CTDB once, then verify tracks as they are ripped.

    While verifying, PCM is also fed into a streaming parity encoder so a new
    CTDB entry can be submitted without retaining whole-disc WAVs.
    """

    def __init__(self) -> None:
        self._toc: str | None = None
        self._db: CtdbDatabase | None = None
        self._total_tracks = 0
        self._total_samples = 0
        self._encoder: CdRepairEncode | None = None
        self._parity: ParityResult | None = None
        self._track_crcs: dict[int, int] = {}
        self._next_encode_track = 1
        self._encode_error: str | None = None

    def prepare(self, info: DiscInfo) -> str:
        self._toc = toc_string_from_info(info)
        self._total_tracks = info.track_count
        self._total_samples = total_audio_samples(info)
        self._parity = None
        self._track_crcs.clear()
        self._next_encode_track = 1
        self._encode_error = None
        self._encoder = None
        if self._total_samples > 0:
            try:
                self._encoder = CdRepairEncode(self._total_samples)
            except ValueError as exc:
                self._encode_error = str(exc)
                log.info("CTDB parity encoder unavailable: %s", exc)
        if not self._toc:
            self._db = None
            return "CTDB: could not build TOC"
        self._db = fetch_database(self._toc)
        if self._db is None:
            return f"CTDB: lookup failed (toc={self._toc})"
        if not self._db.entries:
            return f"CTDB: disc not in database (toc={self._toc})"
        return f"CTDB: {len(self._db.entries)} entry(ies) for toc={self._toc}"

    def set_totals(self, *, total_tracks: int, total_samples: int) -> None:
        if total_tracks > 0:
            self._total_tracks = total_tracks
        if total_samples > 0 and total_samples != self._total_samples:
            self._total_samples = total_samples
            self._parity = None
            try:
                self._encoder = CdRepairEncode(self._total_samples)
                self._encode_error = None
            except ValueError as exc:
                self._encoder = None
                self._encode_error = str(exc)

    @property
    def database(self) -> CtdbDatabase | None:
        return self._db

    @property
    def parity_result(self) -> ParityResult | None:
        return self._parity

    def verify_wav(self, path: Path, track_number: int) -> CtdbResult:
        try:
            pcm = load_cdda_pcm(path)
            crc = compute_track_crc(
                pcm,
                track_number,
                self._total_tracks,
                self._total_samples,
            )
        except Exception as exc:
            log.warning("CTDB CRC failed for track %s: %s", track_number, exc)
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.ERROR,
                message=f"CRC error: {exc}",
            )

        self._track_crcs[track_number] = crc
        self._feed_parity(pcm, track_number)

        if self._db is None:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.ERROR,
                crc=f"{crc:08x}",
                message="CTDB database unavailable",
            )
        if not self._db.entries:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.NOT_IN_DB,
                crc=f"{crc:08x}",
                message="Disc not in CUETools Database",
            )
        return self._db.best_match(track_number, crc)

    def _feed_parity(self, pcm: bytes, track_number: int) -> None:
        if self._encoder is None or self._encode_error is not None:
            return
        if track_number != self._next_encode_track:
            # Parity requires contiguous track order from 1.
            if track_number < self._next_encode_track:
                return
            self._encode_error = (
                f"parity encode requires tracks in order "
                f"(expected {self._next_encode_track}, got {track_number})"
            )
            self._encoder = None
            return
        try:
            self._encoder.write_pcm(pcm)
            self._next_encode_track = track_number + 1
            if (
                self._next_encode_track > self._total_tracks
                and self._encoder.samples_written == self._total_samples
            ):
                self._parity = self._encoder.finish()
                self._encoder = None
        except Exception as exc:
            log.warning("CTDB parity encode failed: %s", exc)
            self._encode_error = str(exc)
            self._encoder = None

    def ensure_parity(self) -> ParityResult | None:
        """Finish streaming encode if all tracks were fed; return cached result."""
        if self._parity is not None:
            return self._parity
        if self._encoder is not None and self._encode_error is None:
            try:
                if self._encoder.samples_written == self._total_samples:
                    self._parity = self._encoder.finish()
                    self._encoder = None
            except Exception as exc:
                self._encode_error = str(exc)
                log.warning("CTDB parity finalize failed: %s", exc)
        return self._parity

    def confirm_submit(
        self,
        results: list[CtdbResult],
        *,
        drivename: str = "",
        artist: str = "",
        title: str = "",
        barcode: str = "",
        quality: int = 100,
    ) -> CtdbSubmitResult:
        """Confirm the matching CTDB entry when every ripped track matches."""
        if self._db is None or not self._toc:
            return CtdbSubmitResult(ok=False, message="CTDB submit skipped: database unavailable")
        if not results:
            return CtdbSubmitResult(ok=False, message="CTDB submit skipped: no track results")
        ordered = sorted(results, key=lambda r: r.track_number)
        if any(r.confidence != CtdbConfidence.MATCH for r in ordered):
            return CtdbSubmitResult(ok=False, message="CTDB submit skipped: not all tracks matched")
        if len(ordered) != self._total_tracks:
            return CtdbSubmitResult(
                ok=False,
                message=(f"CTDB submit skipped: ripped {len(ordered)}/{self._total_tracks} tracks"),
            )
        try:
            track_crcs = [int(r.crc, 16) for r in ordered]
        except ValueError:
            return CtdbSubmitResult(ok=False, message="CTDB submit skipped: invalid track CRC")
        entry = self._db.entry_matching_track_crcs(track_crcs)
        if entry is None:
            return CtdbSubmitResult(
                ok=False,
                message="CTDB submit skipped: no single entry matches all tracks",
            )
        return submit_confirm(
            toc=self._toc,
            entry=entry,
            track_crcs=track_crcs,
            maxid=self._db.max_entry_id,
            quality=quality,
            drivename=drivename,
            artist=artist,
            title=title,
            barcode=barcode,
        )

    def submit(
        self,
        results: list[CtdbResult],
        *,
        drivename: str = "",
        artist: str = "",
        title: str = "",
        barcode: str = "",
        quality: int = 100,
    ) -> CtdbSubmitResult:
        """Confirm a matching entry, or submit a new parity entry when quality is high.

        New-entry path requires ``quality >= 100`` (secure rip, test&copy OK) and
        a finished parity encode from :meth:`verify_wav` feeds.
        """
        confirm = self.confirm_submit(
            results,
            drivename=drivename,
            artist=artist,
            title=title,
            barcode=barcode,
            quality=quality,
        )
        if confirm.ok:
            return confirm
        if quality < 100:
            return CtdbSubmitResult(
                ok=False,
                message=(
                    f"{confirm.message}; new-entry submit requires quality≥100 (got {quality})"
                ),
                status=confirm.status,
            )
        if self._db is None or not self._toc:
            return confirm

        ordered = sorted(results, key=lambda r: r.track_number)
        if len(ordered) != self._total_tracks:
            return CtdbSubmitResult(
                ok=False,
                message=(
                    f"CTDB new submit skipped: ripped {len(ordered)}/{self._total_tracks} tracks"
                ),
            )
        if any(r.confidence == CtdbConfidence.ERROR for r in ordered):
            return CtdbSubmitResult(ok=False, message="CTDB new submit skipped: track CRC errors")
        # Prefer CRCs collected during verify (same values as results).
        try:
            track_crcs = [self._track_crcs.get(r.track_number, int(r.crc, 16)) for r in ordered]
        except ValueError:
            return CtdbSubmitResult(ok=False, message="CTDB new submit skipped: invalid track CRC")

        # Matching entry already handled by confirm; if confirm failed for other
        # reasons (e.g. missing parity/syndrome on entry), still try new submit
        # only when no single matching entry exists.
        if self._db.entry_matching_track_crcs(track_crcs) is not None:
            return confirm

        parity = self.ensure_parity()
        if parity is None:
            reason = self._encode_error or "parity not generated"
            return CtdbSubmitResult(
                ok=False,
                message=f"CTDB new submit skipped: {reason}",
            )
        return submit_new(
            toc=self._toc,
            track_crcs=track_crcs,
            disc_crc=parity.disc_crc,
            parity=parity,
            maxid=self._db.max_entry_id,
            quality=quality,
            drivename=drivename,
            artist=artist,
            title=title,
            barcode=barcode,
        )

    def attempt_repair_for_mismatch(
        self,
        results: list[CtdbResult],
        *,
        timeout: float = 30.0,
    ) -> CtdbRepairProbe:
        """Pick a hasparity entry and probe whether repair looks feasible."""
        if self._db is None or not self._db.entries:
            return CtdbRepairProbe(ok=False, message="CTDB repair: no database entries")
        if not any(r.confidence == CtdbConfidence.MISMATCH for r in results):
            return CtdbRepairProbe(ok=False, message="CTDB repair: no mismatched tracks")
        entry = max(
            (e for e in self._db.entries if e.hasparity),
            key=lambda e: e.confidence,
            default=None,
        )
        if entry is None:
            return CtdbRepairProbe(
                ok=False,
                message="CTDB repair: no entry with hasparity URL",
            )
        local_head = None
        parity = self.ensure_parity()
        if parity is not None:
            local_head = parity.syndrome_head_b64_bytes
        return attempt_repair(entry, local_syndrome_head=local_head, timeout=timeout)
